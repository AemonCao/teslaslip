from datetime import timedelta

from app.geo import wgs84_to_gcj02
from app.models import Address, ChargeSample, TrackPoint
from app.pages import build_charge_page, build_drive_page, expiry_from, link_expired
from tests.fakes import STREET, T0, make_charge, make_drive, make_settings


def test_drive_page_converts_to_gcj_and_draws_parking_gap(tmp_path):
    settings = make_settings(tmp_path)
    first_end = T0
    second_start = first_end + timedelta(minutes=9)
    second_end = second_start + timedelta(minutes=12)
    drives = [
        make_drive(1, first_end - timedelta(minutes=15), first_end, start_geofence="家", end_geofence="商场"),
        make_drive(2, second_start, second_end, start_geofence="商场", end_geofence="公司"),
    ]
    tracks = [
        [
            TrackPoint(date=drives[0].start_date, latitude=31.23, longitude=121.47, battery_level=80, usable_battery_level=79),
            TrackPoint(date=drives[0].end_date, latitude=31.24, longitude=121.48, battery_level=76, usable_battery_level=75),
        ],
        [
            TrackPoint(date=drives[1].start_date, latitude=31.25, longitude=121.49, battery_level=76, usable_battery_level=75),
            TrackPoint(date=drives[1].end_date, latitude=31.26, longitude=121.50, battery_level=70, usable_battery_level=69),
        ],
    ]
    page = build_drive_page(drives, tracks, None, settings)
    kinds = [segment.kind for segment in page["map_segments"]]
    assert kinds == ["drive", "parking", "drive"]
    assert page["map_segments"][1].dashed is True
    assert page["map_segments"][1].label == "停车"
    lat, lng = wgs84_to_gcj02(31.23, 121.47)
    assert page["map_segments"][0].path[0] == [lng, lat]
    assert page["map_segments"][0].path[0] != [31.23, 121.47]
    assert page["start_text"].endswith("10:00") or "2026-10-17" in page["start_text"]


def test_numbers_tires_climate_and_downsample(tmp_path):
    settings = make_settings(tmp_path)
    first = make_drive(1, T0, T0 + timedelta(minutes=10), outside=10, duration=10, distance=3)
    second = make_drive(
        2,
        T0 + timedelta(minutes=15),
        T0 + timedelta(minutes=45),
        outside=20,
        duration=30,
        distance=6,
        start_rated=400,
        end_rated=390,
    )
    points = [
        TrackPoint(date=first.start_date, latitude=31.2, longitude=121.4, battery_level=81, usable_battery_level=80)
    ]
    points.extend(
        TrackPoint(date=first.start_date + timedelta(seconds=index), latitude=31.2, longitude=121.4)
        for index in range(1, 2000)
    )
    points.append(
        TrackPoint(
            date=first.end_date,
            latitude=31.21,
            longitude=121.41,
            battery_level=70,
            usable_battery_level=69,
            climate_on=True,
            driver_temp=22,
            passenger_temp=22,
            front_defrost=True,
            tpms_fl=2.9,
        )
    )
    # 胎压只出现在会被抽稀丢掉的中间点，汇总仍要看到它。
    points[1000] = TrackPoint(
        date=first.start_date + timedelta(seconds=1000),
        latitude=31.205,
        longitude=121.405,
        tpms_fl=2.55,
        tpms_fr=2.56,
        tpms_rl=2.57,
        tpms_rr=2.58,
    )
    page = build_drive_page([first, second], [points, []], "朝阳路10号", settings)
    assert len(page["map_segments"][0].path) <= 2000
    assert page["map_segments"][0].path[0][0] != 31.2
    values = {stat.label: stat.value for stat in page["stats"]}
    assert values["车外温度"] == "17.5 °C"
    assert "2.55" in page["tire_text"]
    assert any("开过" in line for line in page["climate_lines"])
    assert any("出现过" in line for line in page["climate_lines"])
    assert page["destination"] == "朝阳路10号"
    assert page["segments"][0].shuffle_text == "否"


def test_missing_tires_message_and_ttl(tmp_path):
    settings = make_settings(tmp_path)
    row = make_drive(1, T0, T0 + timedelta(minutes=5))
    page = build_drive_page(
        [row],
        [[TrackPoint(date=row.start_date, latitude=31.2, longitude=121.4)]],
        None,
        settings,
    )
    assert page["tire_text"] == "本段没有胎压采样"
    assert expiry_from(T0, 0) is None
    assert link_expired(None, T0 + timedelta(days=2)) is False
    expires = expiry_from(T0, 1)
    assert link_expired(expires, T0 + timedelta(days=1)) is False
    assert link_expired(expires, T0 + timedelta(days=1, seconds=1)) is True


def test_charge_page_hides_null_cost_and_marks_sample(tmp_path):
    settings = make_settings(tmp_path)
    charge = make_charge(
        1,
        T0,
        T0 + timedelta(minutes=40),
        cost=None,
        geofence="地下车库",
        address=Address(display_name=STREET, road="中关村大街", house_number="88号"),
        samples=[ChargeSample(date=T0, charger_power=11, charger_voltage=220, charger_current=16, charger_phases=1)],
    )
    page = build_charge_page(charge, settings)
    labels = [stat.label for stat in page["stats"]]
    assert "费用" not in labels
    assert STREET in page["address"]
    assert any("采样值" in stat.value for stat in page["charger"])
    priced = make_charge(2, T0, T0 + timedelta(minutes=10), cost=12.5)
    assert any(stat.label == "费用" for stat in build_charge_page(priced, settings)["stats"])
