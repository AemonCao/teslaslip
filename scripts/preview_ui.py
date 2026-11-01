"""使用虚构数据在本机预览真实页面：python -m scripts.preview_ui。"""

from __future__ import annotations

import asyncio
import math
import secrets
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi.responses import RedirectResponse

from app.auth import Auth, SESSION_COOKIE, digest_token, password_hash
from app.main import create_app
from app.models import Address, Car, ChargeSample, TrackPoint
from app.reports import period_for
from tests.fakes import FakeClock, boot, make_charge, make_drive

ORIGIN = "http://127.0.0.1:8792"
PREVIEW_NOW = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)


async def preview_runtime(directory: Path):
    runtime, teslamate, _, _ = await boot(
        directory,
        clock=FakeClock(PREVIEW_NOW),
        public_base_url=ORIGIN,
        database_url="postgresql://preview:preview@127.0.0.1/preview",
        mqtt_host="127.0.0.1",
        mqtt_username="preview",
        mqtt_password="preview",
        bark_device_key="preview",
        admin_password_hash=password_hash(secrets.token_urlsafe(24)),
        reports_enabled=False,
    )
    teslamate.profile = Car(model="3", marketing_name="长续航版")
    places = ["家", "滨江公园", "工作室", "城市图书馆", "湖畔咖啡"]
    for index in range(1, 49):
        start = PREVIEW_NOW - timedelta(days=49 - index, hours=10, minutes=45)
        duration = 18 + index % 5 * 7
        end = start + timedelta(minutes=duration)
        distance = round(8.6 + index % 7 * 3.2, 1)
        drive = make_drive(
            index, start, end, duration=duration, distance=distance,
            start_geofence=places[index % len(places)],
            end_geofence=places[(index + 1) % len(places)],
            start_address=Address(display_name="上海市 · 演示起点"),
            end_address=Address(display_name="上海市 · 演示终点"),
            start_km=12000 + index * 20,
            end_km=12000 + index * 20 + distance,
        )
        teslamate.drives.append(drive)
        teslamate.tracks[index] = [
            TrackPoint(
                date=start + (end - start) * step / 90,
                latitude=31.21 + step * 0.0003 + math.sin(step / 12) * 0.003,
                longitude=121.44 + step * 0.0004,
                speed=round(45 + 30 * math.sin(step / 8), 1),
                power=round(20 + 35 * math.sin(step / 7), 1),
                battery_level=80 - step // 18,
                usable_battery_level=79 - step // 18,
                odometer=drive.start_km + distance * step / 90,
                elevation=12 + 6 * math.sin(step / 14),
                inside_temp=22,
                outside_temp=19 + math.sin(step / 20),
                rated_range=420 - step * 0.15,
                ideal_range=500 - step * 0.18,
                tpms_fl=2.65, tpms_fr=2.63, tpms_rl=2.68, tpms_rr=2.66,
                climate_on=True, driver_temp=22, passenger_temp=22,
            )
            for step in range(91)
        ]
        if index % 3 == 0:
            charge_start = end + timedelta(minutes=20)
            teslamate.charges.append(make_charge(
                index // 3, charge_start, charge_start + timedelta(minutes=48),
                duration=48, energy=24.6, used=26.2, cost=18.45,
                start_battery=35, end_battery=80,
                geofence="滨江停车场 · 充电站",
                address=Address(display_name="上海市 · 演示充电位置"),
                samples=[
                    ChargeSample(
                        date=charge_start + timedelta(minutes=step * 2),
                        charger_power=round(38 - step * 0.9, 1),
                        charger_voltage=380, charger_current=32, charger_phases=3,
                        cable="Type 2", battery_level=35 + round(step * 45 / 24),
                        charge_energy_added=round(step * 24.6 / 24, 2),
                        rated_range=140 + step * 200 / 24, outside_temp=19,
                    )
                    for step in range(25)
                ],
            ))
    for kind, day in [
        ("month", date(2026, 10, 1)), ("month", date(2026, 9, 1)),
        ("week", date(2026, 10, 19)), ("week", date(2026, 10, 12)),
        ("week", date(2026, 10, 5)),
    ]:
        runtime.reports.generate(period_for(kind, day), PREVIEW_NOW)
    return runtime


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="teslaslip-ui-") as directory:
        runtime = asyncio.run(preview_runtime(Path(directory)))
        app = create_app(runtime)
        auth = Auth(runtime)

        @app.get("/preview")
        def enter_preview():
            session = secrets.token_urlsafe(32)
            now = runtime.clock.now().timestamp()
            runtime.store.create_session(digest_token(session), auth.fingerprint, now + 86400, now)
            response = RedirectResponse("/?tab=drives", status_code=303)
            auth.set_cookie(response, SESSION_COOKIE, session, 86400)
            return response

        print(f"UI preview (fictional data): {ORIGIN}/preview", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=8792, access_log=False)


if __name__ == "__main__":
    main()
