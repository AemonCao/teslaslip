import asyncio
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.main import create_app
from app.models import Address, Car, ChargeSample, TrackPoint
from app.pages import build_charge_page, build_drive_page
from tests.fakes import STREET, T0, boot, make_charge, make_drive, make_settings

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
ENV = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(["html"]),
)

HERO_DRIVE_LABELS = {"总里程", "驾驶时长", "出门耗时"}
HERO_CHARGE_LABELS = {"时长", "加入电量", "起点电量", "结束电量", "地点围栏", "地址"}


def render(name: str, page: dict) -> str:
    return ENV.get_template(name).render(v=page)


def test_drive_template_groups_fields_without_repeating_hero(tmp_path):
    settings = make_settings(tmp_path, amap_js_key="test-key")
    first_end = T0
    second_start = first_end + timedelta(minutes=9)
    drives = [
        make_drive(
            1,
            first_end - timedelta(minutes=15),
            first_end,
            start_geofence="家",
            end_geofence="商场",
            start_address=Address(display_name="起点路1号"),
            end_address=Address(display_name="商场路2号"),
        ),
        make_drive(
            2,
            second_start,
            second_start + timedelta(minutes=12),
            start_geofence="商场",
            end_geofence="公司",
            start_address=Address(display_name="商场路2号"),
            end_address=Address(display_name="公司路3号"),
        ),
    ]
    tracks = [
        [TrackPoint(date=drives[0].start_date, latitude=31.23, longitude=121.47)],
        [TrackPoint(date=drives[1].end_date, latitude=31.26, longitude=121.50, tpms_fl=2.9)],
    ]
    page = build_drive_page(drives, tracks, "朝阳路10号", settings)
    html = render("drive.html", page)

    assert "<table" not in html
    assert "<h1>家 → 公司</h1>" in html
    for title in ("驾驶", "续航", "里程", "环境", "车辆", "分段"):
        assert f"<h2>{title}</h2>" in html
    assert page["distance_text"] in html
    assert page["driving_text"] in html
    assert page["elapsed_text"] in html
    assert page["start_text"] in html and page["end_text"] in html
    assert 'name="format-detection" content="telephone=no"' in html
    assert "起点路1号" in html and "公司路3号" in html
    assert "停车前最后一次导航目的地，可能为空或不准" in html
    assert "朝阳路10号" in html
    assert "胎压" in html and page["tire_text"] in html
    assert 'data-shape="generic"' in html
    assert 'class="shape shape-generic"' in html
    assert 'data-pos="fl"' in html and 'data-pos="rr"' in html
    assert "shape-model3" not in html
    for line in page["climate_lines"]:
        label, value = line.split("：", 1)
        assert label in html and value in html
    for stat in page["stats"]:
        if stat.label in HERO_DRIVE_LABELS:
            assert stat.label not in html
        else:
            assert stat.label in html
            assert stat.value in html
    for row in page["segments"]:
        assert row.time_text in html
        assert row.distance_text in html
        assert row.duration_text in html
        assert row.start_text in html and row.end_text in html
        assert f"挪车 {row.shuffle_text}" in html
    assert 'id="drive-map-app"' in html
    assert 'id="map-data"' in html
    assert "webapi.amap.com/maps?v=2.0&key=test-key" in html
    assert 'href="/settings"' in html
    for section in ("map", "driving", "range", "odometer", "environment", "vehicle", "segments", "destination"):
        assert f'data-section="{section}"' in html
    assert 'data-range="rated"' in html
    assert 'data-range="ideal"' in html


def test_drive_template_draws_model_three_and_keeps_empty_tires(tmp_path):
    settings = make_settings(tmp_path)
    row = make_drive(1, T0, T0 + timedelta(minutes=8))
    present = build_drive_page(
        [row],
        [[
            TrackPoint(
                date=row.end_date,
                latitude=31.2,
                longitude=121.4,
                tpms_fl=2.55,
                tpms_fr=2.56,
                tpms_rl=2.57,
                tpms_rr=None,
            )
        ]],
        None,
        settings,
        Car(model="3", marketing_name="SR+"),
    )
    html = render("drive.html", present)
    assert 'data-shape="model3"' in html
    assert 'class="shape shape-model3"' in html
    assert "shape-modely" not in html
    assert "Model 3 SR+" in html
    assert "2.55" in html and "2.57" in html
    assert "左前" in html and "右后" in html
    assert "本段没有胎压采样" not in html

    missing = build_drive_page(
        [row],
        [[TrackPoint(date=row.start_date, latitude=31.2, longitude=121.4)]],
        None,
        settings,
        Car(model="3", marketing_name="SR+"),
    )
    missing_html = render("drive.html", missing)
    assert missing_html.count("本段没有胎压采样") == 2
    assert "Model 3 SR+" in missing_html
    assert 'data-pos="fr"' in missing_html


def test_drive_page_keeps_pressures_when_model_lookup_fails(tmp_path):
    async def body():
        runtime, tm, _bark, _clock = await boot(tmp_path)
        drive = make_drive(1, T0, T0 + timedelta(minutes=8), distance=3, duration=8)
        tm.drives.append(drive)
        tm.tracks[1] = [
            TrackPoint(
                date=drive.start_date,
                latitude=31.2,
                longitude=121.4,
                tpms_fl=2.9,
                tpms_fr=2.8,
                tpms_rl=2.7,
                tpms_rr=2.6,
            )
        ]
        tm.profile = Car(model="3", marketing_name="SR+")
        runtime.store.insert_link(
            token="tok",
            kind="drive",
            drive_ids=[1],
            charge_id=None,
            destination=None,
            created_at=T0,
            expires_at=None,
            preview=False,
        )
        with TestClient(create_app(runtime)) as client:
            ok = client.get("/d/tok")
            assert ok.status_code == 200
            assert 'data-shape="model3"' in ok.text
            assert "Model 3 SR+" in ok.text
            assert "2.90" in ok.text

            def broken(_car_id: int):
                raise RuntimeError("database unavailable")

            tm.car = broken
            failed = client.get("/d/tok")
            assert failed.status_code == 200
            assert 'data-shape="generic"' in failed.text
            assert "Model 3 SR+" not in failed.text
            assert "2.90" in failed.text

    asyncio.run(body())


def test_drive_template_without_geofence_uses_plain_title(tmp_path):
    settings = make_settings(tmp_path)
    row = make_drive(1, T0, T0 + timedelta(minutes=5))
    page = build_drive_page(
        [row],
        [[TrackPoint(date=row.start_date, latitude=31.2, longitude=121.4)]],
        None,
        settings,
    )
    html = render("drive.html", page)
    assert "<h1>出门</h1>" in html
    assert "<h2>分段</h2>" not in html
    assert "未配置高德 Key" in html
    assert "<table" not in html


def test_charge_template_puts_session_in_the_hero(tmp_path):
    settings = make_settings(tmp_path)
    charge = make_charge(
        1,
        T0,
        T0 + timedelta(minutes=40),
        cost=12.5,
        geofence="地下车库",
        address=Address(display_name=STREET),
        samples=[ChargeSample(date=T0, charger_power=11)],
    )
    page = build_charge_page(charge, settings)
    html = render("charge.html", page)

    assert "<h1>地下车库</h1>" in html
    assert 'name="format-detection" content="telephone=no"' in html
    assert STREET in html
    assert "<h2>充电</h2>" in html
    assert "<h2>采样</h2>" in html
    assert "<table" not in html
    assert "没有充电采样" not in html
    for stat in page["stats"]:
        assert stat.value in html
        if stat.label in HERO_CHARGE_LABELS:
            assert stat.label not in html
        else:
            assert stat.label in html
    for stat in page["charger"]:
        assert stat.label in html
        assert stat.value in html
    assert 'href="/settings"' in html
    assert 'data-section="map"' in html
    assert 'data-section="samples"' in html
    assert 'data-range="rated"' in html
    assert 'data-range="ideal"' in html

    empty = build_charge_page(
        make_charge(2, T0, T0 + timedelta(minutes=10), latitude=None, longitude=None),
        settings,
    )
    empty_html = render("charge.html", empty)
    assert "<h1>充电完成</h1>" in empty_html
    assert "没有充电采样" in empty_html
    assert "没有位置" in empty_html
    assert "未配置高德 Key" in empty_html


def test_settings_template_keeps_the_page_shell():
    html = ENV.get_template("settings.html").render()
    assert "<h1>设置</h1>" in html
    assert "只保存在这台手机上" in html
    assert 'id="settings-app"' in html
    assert 'id="settings-back-slot"' in html
    assert 'name="format-detection" content="telephone=no"' in html


def test_settings_route_returns_page_and_root_stays_missing(tmp_path):
    async def body():
        runtime, _tm, _bark, _clock = await boot(tmp_path)
        with TestClient(create_app(runtime)) as client:
            settings = client.get("/settings")
            root = client.get("/")
        assert settings.status_code == 200
        assert "<h1>设置</h1>" in settings.text
        assert "只保存在这台手机上" in settings.text
        assert 'id="settings-app"' in settings.text
        assert root.status_code == 404
        assert root.text == "未找到"

    asyncio.run(body())
