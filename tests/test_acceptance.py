import asyncio
import json
import logging
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Address, TrackPoint
from app.pages import link_expired
from app.redact import SecretVault, setup_logging
from app.runtime import Runtime
from tests.fakes import (
    NAV_DEST,
    STREET,
    FakeBark,
    FakeClock,
    MemoryTeslaMate,
    T0,
    baseline,
    boot,
    line,
    make_charge,
    make_drive,
    make_settings,
    token_from,
)


def run(coro):
    return asyncio.run(coro)


def client_for(runtime):
    app = create_app(runtime)
    return TestClient(app)


def test_01_nine_minute_gap_is_one_bark_and_one_page(tmp_path):
    async def body():
        runtime, tm, bark, _clock = await boot(tmp_path)
        end = T0 - timedelta(minutes=40)
        first = make_drive(
            1,
            end - timedelta(minutes=20),
            end,
            distance=6.2,
            duration=20,
            start_geofence="家",
            end_geofence="商场",
            start_address=Address(display_name=STREET, road="中关村大街", house_number="88号"),
            end_address=Address(display_name="商场路1号", road="商场路"),
        )
        second_start = end + timedelta(minutes=9)
        second = make_drive(
            2,
            second_start,
            second_start + timedelta(minutes=15),
            distance=4.1,
            duration=15,
            start_geofence="商场",
            end_geofence="公司",
            end_address=Address(display_name="公司路2号", road="公司路"),
        )
        tm.drives.extend([first, second])
        tm.tracks[1] = line(1, first.start_date, first.end_date, 31.23, 121.47)
        tm.tracks[2] = line(2, second.start_date, second.end_date, 31.25, 121.50)
        await baseline(runtime)
        assert len(bark.payloads) == 1
        assert STREET not in bark.payloads[0]["title"]
        assert STREET not in bark.payloads[0]["body"]
        assert "中关村大街" not in bark.payloads[0]["title"] + bark.payloads[0]["body"]
        assert "家" in bark.payloads[0]["title"]
        assert "公司" in bark.payloads[0]["title"]
        token = token_from(bark.payloads[0])
        with client_for(runtime) as client:
            response = client.get(f"/d/{token}")
        assert response.status_code == 200
        assert response.text.count('data-kind="drive"') == 2
        assert response.text.count('data-kind="parking"') == 1
        assert "停车" in response.text
        assert "未配置高德 Key" in response.text
        assert "openstreetmap" not in response.text.lower()
        assert "leaflet" not in response.text.lower()
        assert "webapi.amap.com" not in response.text
        assert STREET in response.text
        return runtime

    run(body())


def test_02_eleven_minute_gap_is_two_pushes(tmp_path):
    async def body():
        runtime, tm, bark, _clock = await boot(tmp_path)
        end = T0 - timedelta(minutes=40)
        tm.drives.append(make_drive(1, end - timedelta(minutes=10), end, distance=3, duration=10))
        later = end + timedelta(minutes=11)
        tm.drives.append(make_drive(2, later, later + timedelta(minutes=8), distance=4, duration=8))
        await baseline(runtime)
        assert len(bark.payloads) == 2
        assert token_from(bark.payloads[0]) != token_from(bark.payloads[1])

    run(body())


def test_03_shuffle_does_not_bark_and_advances_watermark(tmp_path):
    async def body():
        runtime, tm, bark, _clock = await boot(tmp_path)
        end = T0 - timedelta(minutes=30)
        tm.drives.append(make_drive(4, end - timedelta(minutes=1), end, distance=0.2, duration=1))
        await baseline(runtime)
        assert bark.payloads == []
        assert runtime.store.get_meta("drive_watermark") == "4"
        assert runtime.store.find_drive_link([4]) is None

    run(body())


def test_04_one_threshold_alone_still_pushes(tmp_path):
    async def body():
        runtime, tm, bark, _clock = await boot(tmp_path)
        end = T0 - timedelta(minutes=40)
        tm.drives.append(make_drive(1, end - timedelta(minutes=5), end, distance=0.2, duration=5))
        later = end + timedelta(minutes=20)
        tm.drives.append(make_drive(2, later, later + timedelta(minutes=1), distance=2, duration=1))
        await baseline(runtime)
        assert len(bark.payloads) == 2

    run(body())


def test_05_shuffle_inside_outing_stays_on_the_page(tmp_path):
    async def body():
        runtime, tm, bark, _clock = await boot(tmp_path)
        start = T0 - timedelta(hours=2)
        first_end = start + timedelta(minutes=15)
        shuffle_start = first_end + timedelta(minutes=4)
        shuffle_end = shuffle_start + timedelta(minutes=1)
        last_start = shuffle_end + timedelta(minutes=4)
        last_end = last_start + timedelta(minutes=12)
        tm.drives.extend(
            [
                make_drive(1, start, first_end, distance=5, duration=15, start_geofence="家", end_geofence="路口"),
                make_drive(2, shuffle_start, shuffle_end, distance=0.1, duration=1, start_geofence="路口", end_geofence="门口"),
                make_drive(3, last_start, last_end, distance=6, duration=12, start_geofence="门口", end_geofence="公司"),
            ]
        )
        for drive in tm.drives:
            tm.tracks[drive.id] = line(drive.id, drive.start_date, drive.end_date)
        await baseline(runtime)
        assert len(bark.payloads) == 1
        token = token_from(bark.payloads[0])
        with client_for(runtime) as client:
            response = client.get(f"/d/{token}")
        assert response.status_code == 200
        assert response.text.count('data-kind="drive"') == 3
        assert response.text.count('data-kind="parking"') == 2
        assert "挪车 是" in response.text

    run(body())


def test_06_first_start_skips_history_and_logs_preview(tmp_path, caplog):
    async def body():
        caplog.set_level(logging.INFO)
        tm = MemoryTeslaMate()
        real_end = T0 - timedelta(days=2)
        tm.drives.append(
            make_drive(5, real_end - timedelta(minutes=30), real_end, distance=42.5, duration=30, end_geofence="公司")
        )
        shuffle_end = T0 - timedelta(days=1)
        tm.drives.append(make_drive(6, shuffle_end - timedelta(minutes=1), shuffle_end, distance=0.1, duration=1))
        tm.charges.append(make_charge(4, real_end - timedelta(minutes=40), real_end))
        tm.tracks[5] = line(5, tm.drives[0].start_date, tm.drives[0].end_date)
        clock = FakeClock()
        bark = FakeBark()
        cfg = make_settings(tmp_path)
        from app.db import Store

        runtime = Runtime(
            cfg,
            Store(cfg.sqlite_path),
            tm,
            bark,
            clock,
            SecretVault(),
            enable_tasks=False,
        )
        await runtime.startup()
        assert bark.payloads == []
        assert runtime.store.get_meta("drive_watermark") == "6"
        assert runtime.store.get_meta("charge_watermark") == "4"
        assert runtime.preview_url
        assert any(runtime.preview_url in record.message for record in caplog.records)
        await baseline(runtime)
        assert bark.payloads == []
        with client_for(runtime) as client:
            response = client.get(runtime.preview_url.replace(cfg.public_base_url, ""))
        assert response.status_code == 200
        assert "42.5 km" in response.text
        assert "公司" in response.text

    run(body())


def test_07_quiet_deadline_survives_restart(tmp_path):
    async def body():
        runtime, tm, bark, clock = await boot(tmp_path)
        topics = await baseline(runtime)
        await runtime.on_mqtt_message(topics["state"], "driving")
        await runtime.on_mqtt_message(topics["state"], "online")
        end = clock.now() - timedelta(seconds=5)
        tm.drives.append(make_drive(1, end - timedelta(minutes=12), end, distance=5, duration=12))
        clock.advance(timedelta(seconds=15))
        await runtime.on_timer()
        pending = runtime.store.get_pending_outing()
        assert pending is not None
        assert pending.phase == "quiet"
        assert bark.payloads == []
        deadline = pending.deadline_at

        restarted = Runtime(
            runtime.settings,
            runtime.store,
            tm,
            bark,
            clock,
            runtime.vault,
            enable_tasks=False,
        )
        await restarted.startup()
        assert restarted.store.get_pending_outing().deadline_at == deadline
        assert bark.payloads == []
        clock.set(deadline + timedelta(seconds=1))
        await restarted.on_timer()
        assert len(bark.payloads) == 1
        again = Runtime(
            runtime.settings,
            runtime.store,
            tm,
            bark,
            clock,
            runtime.vault,
            enable_tasks=False,
        )
        await again.startup()
        await again.on_timer()
        assert len(bark.payloads) == 1

    run(body())


def test_08_retained_online_is_not_a_new_park(tmp_path):
    async def body():
        runtime, _tm, bark, _clock = await boot(tmp_path)
        topics = await baseline(runtime, "online", "Disconnected")
        assert runtime.store.get_pending_outing() is None
        await runtime.on_mqtt_connect()
        await runtime.on_mqtt_message(topics["state"], "online")
        await runtime.on_mqtt_message(topics["charging_state"], "Disconnected")
        assert runtime.store.get_pending_outing() is None
        assert bark.payloads == []

    run(body())


def test_09_reconnect_pushes_only_above_watermark(tmp_path):
    async def body():
        tm = MemoryTeslaMate()
        old_end = T0 - timedelta(days=3)
        tm.drives.append(make_drive(1, old_end - timedelta(minutes=10), old_end, distance=3.3, duration=10))
        clock = FakeClock()
        bark = FakeBark()
        cfg = make_settings(tmp_path)
        from app.db import Store

        runtime = Runtime(cfg, Store(cfg.sqlite_path), tm, bark, clock, SecretVault(), enable_tasks=False)
        await runtime.startup()
        assert bark.payloads == []
        new_end = T0 - timedelta(minutes=30)
        tm.drives.append(make_drive(2, new_end - timedelta(minutes=12), new_end, distance=9.9, duration=12))
        await baseline(runtime)
        assert len(bark.payloads) == 1
        token = token_from(bark.payloads[0])
        link = runtime.store.get_link(token)
        assert link.drive_ids == [2]
        assert runtime.store.get_meta("drive_watermark") == "2"

    run(body())


def test_10_charge_is_separate_from_the_ten_minute_outing(tmp_path):
    async def body():
        runtime, tm, bark, clock = await boot(tmp_path)
        topics = await baseline(runtime, "online", "Charging")
        await runtime.on_mqtt_message(topics["state"], "driving")
        end = clock.now()
        drive = make_drive(1, end - timedelta(minutes=20), end, distance=8, duration=20, start_geofence="家", end_geofence="公司")
        tm.drives.append(drive)
        charge = make_charge(
            1,
            end - timedelta(minutes=30),
            end,
            geofence="地下车库",
            address=Address(display_name=STREET, road="中关村大街", house_number="88号"),
        )
        tm.charges.append(charge)
        await runtime.on_mqtt_message(topics["state"], "online")
        await runtime.on_mqtt_message(topics["charging_state"], "Complete")
        assert len(bark.payloads) == 1
        assert "/c/" in bark.payloads[0]["url"]
        assert STREET not in bark.payloads[0]["title"] + bark.payloads[0]["body"]
        assert "地下车库" in bark.payloads[0]["title"]
        clock.advance(timedelta(seconds=15))
        await runtime.on_timer()
        assert len(bark.payloads) == 1
        pending = runtime.store.get_pending_outing()
        assert pending.phase == "quiet"
        clock.set(pending.deadline_at + timedelta(seconds=1))
        await runtime.on_timer()
        assert len(bark.payloads) == 2
        assert "/d/" in bark.payloads[1]["url"]
        assert token_from(bark.payloads[0]) != token_from(bark.payloads[1])
        with client_for(runtime) as client:
            page = client.get(f"/c/{token_from(bark.payloads[0])}")
        assert page.status_code == 200
        assert STREET in page.text
        assert "未配置高德 Key" in page.text
        assert "openstreetmap" not in page.text.lower()

    run(body())


def test_12_missing_amap_key_has_polyline_and_no_osm(tmp_path):
    async def body():
        runtime, tm, bark, _clock = await boot(tmp_path, amap_js_key="")
        end = T0 - timedelta(minutes=30)
        drive = make_drive(1, end - timedelta(minutes=10), end)
        tm.drives.append(drive)
        tm.tracks[1] = line(1, drive.start_date, drive.end_date)
        await baseline(runtime)
        with client_for(runtime) as client:
            response = client.get(f"/d/{token_from(bark.payloads[0])}")
        assert response.status_code == 200
        assert 'data-kind="drive"' in response.text
        assert "未配置高德 Key" in response.text
        assert "webapi.amap.com" not in response.text
        assert "openstreetmap" not in response.text.lower()
        assert "mapbox" not in response.text.lower()

    run(body())


def test_amap_key_injects_gcj_path_and_not_osm(tmp_path):
    async def body():
        runtime, tm, bark, _clock = await boot(
            tmp_path,
            amap_js_key="test-amap-key",
            amap_security_js_code="security-code",
        )
        end = T0 - timedelta(minutes=30)
        drive = make_drive(1, end - timedelta(minutes=10), end)
        tm.drives.append(drive)
        tm.tracks[1] = line(1, drive.start_date, drive.end_date, 31.23, 121.47)
        await baseline(runtime)
        with client_for(runtime) as client:
            response = client.get(f"/d/{token_from(bark.payloads[0])}")
        assert response.status_code == 200
        assert "webapi.amap.com/maps?v=2.0&key=test-amap-key" in response.text
        assert "_AMapSecurityConfig" in response.text
        assert "未配置高德 Key" not in response.text
        assert "openstreetmap" not in response.text.lower()
        payload = json.loads(response.text.split('id="map-data" type="application/json">')[1].split("</script>")[0])
        assert payload[0]["path"][0][0] > 100
        assert payload[0]["path"][0][1] < 40

    run(body())


def test_13_ttl_zero_stays_open_and_one_day_expires_after_24h(tmp_path):
    async def zero():
        runtime, tm, bark, clock = await boot(tmp_path / "zero", trip_link_ttl_days=0)
        end = T0 - timedelta(minutes=30)
        tm.drives.append(make_drive(1, end - timedelta(minutes=10), end, distance=7))
        await baseline(runtime)
        token = token_from(bark.payloads[0])
        clock.advance(timedelta(days=2))
        with client_for(runtime) as client:
            assert client.get(f"/d/{token}").status_code == 200

    async def one_day():
        runtime, tm, bark, clock = await boot(tmp_path / "one", trip_link_ttl_days=1)
        end = T0 - timedelta(minutes=30)
        tm.drives.append(make_drive(1, end - timedelta(minutes=10), end, distance=7))
        await baseline(runtime)
        token = token_from(bark.payloads[0])
        link = runtime.store.get_link(token)
        assert link_expired(link.expires_at, clock.now() + timedelta(days=1)) is False
        clock.advance(timedelta(days=1))
        with client_for(runtime) as client:
            still = client.get(f"/d/{token}")
            clock.advance(timedelta(seconds=1))
            expired = client.get(f"/d/{token}")
            missing = client.get("/d/not-a-real-token")
        assert still.status_code == 200
        assert expired.status_code == 404
        assert missing.status_code == 404
        assert still.text != expired.text
        assert expired.text == missing.text == "未找到"
        assert "id" not in expired.text.lower()

    run(zero())
    run(one_day())


def test_14_operational_logs_omit_secrets(tmp_path):
    async def body():
        cfg = make_settings(tmp_path)
        vault = SecretVault(["bark-device-secret", "mqtt-secret-value", "db-secret-value", cfg.database_url])
        setup_logging("info", vault)
        found: list[str] = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                found.append(record.getMessage())

        logger = logging.getLogger("teslaslip")
        handler = ListHandler()
        logger.addHandler(handler)
        runtime, tm, bark, _clock = await boot(tmp_path)
        runtime.vault = vault
        end = T0 - timedelta(minutes=30)
        drive = make_drive(
            1,
            end - timedelta(minutes=10),
            end,
            start_address=Address(display_name=STREET, road="中关村大街", house_number="88号"),
        )
        tm.drives.append(drive)
        tm.tracks[1] = [
            TrackPoint(date=drive.start_date, latitude=31.234567, longitude=121.473701, battery_level=80)
        ]
        await baseline(runtime)
        text = "\n".join(found)
        assert "31.234567" not in text
        assert "121.473701" not in text
        assert "bark-device-secret" not in text
        assert "mqtt-secret-value" not in text
        assert "db-secret-value" not in text
        assert STREET not in text
        token = token_from(bark.payloads[0])
        assert token not in text

    run(body())


def test_bark_retry_keeps_the_same_link(tmp_path):
    async def body():
        bark = FakeBark(fail_times=1)
        runtime, tm, bark, clock = await boot(tmp_path, bark=bark)
        end = T0 - timedelta(minutes=30)
        tm.drives.append(make_drive(1, end - timedelta(minutes=8), end, distance=5, duration=8))
        await baseline(runtime)
        assert bark.payloads == []
        assert runtime.store.get_meta("drive_watermark") == "0"
        clock.advance(timedelta(seconds=29))
        await runtime.on_timer()
        assert bark.payloads == []
        clock.advance(timedelta(seconds=2))
        await runtime.on_timer()
        assert len(bark.payloads) == 1
        assert runtime.store.get_meta("drive_watermark") == "1"

    run(body())


def test_timer_does_not_query_until_it_is_due(tmp_path):
    async def body():
        runtime, tm, _bark, clock = await boot(tmp_path)
        topics = await baseline(runtime)
        before = tm.query_calls
        await runtime.on_mqtt_message(topics["state"], "driving")
        await runtime.on_mqtt_message(topics["state"], "online")
        armed = tm.query_calls
        clock.advance(timedelta(seconds=10))
        assert tm.query_calls == armed
        assert before <= armed

    run(body())


def test_navigation_destination_is_on_the_page_only(tmp_path):
    async def body():
        runtime, tm, bark, clock = await boot(tmp_path)
        topics = await baseline(runtime)
        await runtime.on_mqtt_message(topics["state"], "driving")
        await runtime.on_mqtt_message(
            topics["active_route"],
            json.dumps({"destination": NAV_DEST, "error": None}),
        )
        end = clock.now()
        drive = make_drive(1, end - timedelta(minutes=10), end, distance=4, duration=10)
        tm.drives.append(drive)
        tm.tracks[1] = line(1, drive.start_date, drive.end_date)
        await runtime.on_mqtt_message(topics["state"], "online")
        clock.advance(timedelta(seconds=15))
        await runtime.on_timer()
        pending = runtime.store.get_pending_outing()
        clock.set(pending.deadline_at + timedelta(seconds=1))
        await runtime.on_timer()
        assert NAV_DEST not in bark.payloads[0]["title"] + bark.payloads[0]["body"]
        with client_for(runtime) as client:
            page = client.get(f"/d/{token_from(bark.payloads[0])}")
        assert "停车前最后一次导航目的地，可能为空或不准" in page.text
        assert NAV_DEST in page.text

    run(body())


def test_returning_to_driving_does_not_split_the_outing(tmp_path):
    async def body():
        runtime, tm, bark, clock = await boot(tmp_path)
        topics = await baseline(runtime)
        await runtime.on_mqtt_message(topics["state"], "driving")
        first_end = clock.now()
        tm.drives.append(make_drive(1, first_end - timedelta(minutes=12), first_end, distance=5, duration=12))
        await runtime.on_mqtt_message(topics["state"], "online")
        clock.advance(timedelta(seconds=15))
        await runtime.on_timer()
        assert bark.payloads == []
        await runtime.on_mqtt_message(topics["state"], "driving")
        assert runtime.store.get_pending_outing() is None
        await runtime.on_mqtt_message(topics["state"], "online")
        clock.advance(timedelta(seconds=15))
        await runtime.on_timer()
        assert bark.payloads == []
        second_start = first_end + timedelta(minutes=5)
        second_end = clock.now()
        tm.drives.append(make_drive(2, second_start, second_end, distance=4, duration=8))
        clock.advance(timedelta(seconds=45))
        await runtime.on_timer()
        pending = runtime.store.get_pending_outing()
        assert pending is not None
        assert pending.phase == "quiet"
        clock.set(pending.deadline_at + timedelta(seconds=1))
        await runtime.on_timer()
        assert len(bark.payloads) == 1
        assert runtime.store.get_link(token_from(bark.payloads[0])).drive_ids == [1, 2]

    run(body())


def test_charge_retry_waits_for_end_date_then_pushes_once(tmp_path):
    async def body():
        runtime, tm, bark, clock = await boot(tmp_path)
        topics = await baseline(runtime, "charging", "Charging")
        await runtime.on_mqtt_message(topics["charging_state"], "Stopped")
        assert bark.payloads == []
        armed = runtime.store.get_pending_charge().armed_at
        clock.advance(timedelta(seconds=15))
        await runtime.on_timer()
        assert bark.payloads == []
        clock.set(armed + timedelta(seconds=60))
        tm.charges.append(make_charge(8, clock.now() - timedelta(minutes=20), clock.now(), geofence="家"))
        await runtime.on_timer()
        assert len(bark.payloads) == 1
        assert "/c/" in bark.payloads[0]["url"]
        clock.advance(timedelta(minutes=30))
        await runtime.on_timer()
        assert len(bark.payloads) == 1

    run(body())


def test_healthz_requires_sqlite_postgres_and_mqtt(tmp_path):
    async def body():
        runtime, tm, _bark, _clock = await boot(tmp_path)
        with client_for(runtime) as client:
            runtime.mqtt_connected = False
            down = client.get("/healthz")
            runtime.mqtt_connected = True
            up = client.get("/healthz")
            tm.ping_ok = False
            runtime.mqtt_connected = True
            db = client.get("/healthz")
        assert down.status_code == 503
        assert up.status_code == 200
        assert up.json() == {"ok": True}
        assert db.status_code == 503
        assert "secret" not in up.text
        assert "latitude" not in up.text

    run(body())


def test_deploy_files_do_not_touch_teslamate():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    example = (root / ".env.example").read_text(encoding="utf-8")
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "depends_on" not in compose
    assert "teslamate-db" not in compose
    assert "/mnt/user/appdata/teslaslip:/data" in compose
    assert "8791:8791" in compose
    assert "/healthz" in dockerfile
    assert "python:3.12-slim" in dockerfile
    assert ".env" in gitignore
    assert "替换成" in example
    assert "bark-device-secret" not in example
    assert "access_log=False" in (root / "app" / "main.py").read_text(encoding="utf-8")
