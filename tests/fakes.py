from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.bark import BarkError
from app.config import Settings
from app.db import Store
from app.models import Address, Car, Charge, ChargeSample, Drive, TrackPoint
from app.config import secret_values
from app.redact import SecretVault
from app.mqtt import topic_map
from app.runtime import Runtime
from app.teslamate import ReadOnlyError

T0 = datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc)
STREET = "中关村大街88号"
NAV_DEST = "朝阳路10号"


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, now: datetime) -> None:
        self._now = now


class FakeBark:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.attempts = 0
        self.payloads: list[dict] = []

    async def push(self, payload: dict) -> None:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise BarkError("fail")
        self.payloads.append(payload)


class MemoryTeslaMate:
    def __init__(self) -> None:
        self.drives: list[Drive] = []
        self.charges: list[Charge] = []
        self.tracks: dict[int, list[TrackPoint]] = {}
        self.profile: Car | None = None
        self.track_calls = 0
        self.query_calls = 0
        self.ping_ok = True
        self.write_attempts: list[str] = []

    def car(self, car_id: int) -> Car | None:
        self.query_calls += 1
        return self.profile

    def ping(self) -> bool:
        return self.ping_ok

    def max_ended_drive_id(self, car_id: int) -> int:
        self.query_calls += 1
        ended = [drive.id for drive in self.drives if drive.end_date is not None]
        return max(ended) if ended else 0

    def max_ended_charge_id(self, car_id: int) -> int:
        self.query_calls += 1
        ended = [charge.id for charge in self.charges if charge.end_date is not None]
        return max(ended) if ended else 0

    def ended_drives_after(self, car_id: int, watermark: int) -> list[Drive]:
        self.query_calls += 1
        rows = [drive for drive in self.drives if drive.end_date is not None and drive.id > watermark]
        return sorted(rows, key=lambda drive: (drive.end_date, drive.id))

    def drives_by_ids(self, car_id: int, drive_ids: list[int]) -> list[Drive]:
        self.query_calls += 1
        wanted = set(drive_ids)
        rows = [drive for drive in self.drives if drive.id in wanted]
        return sorted(rows, key=lambda drive: (drive.end_date, drive.id))

    def recent_ended_drives(
        self,
        car_id: int,
        watermark: int,
        *,
        limit: int,
        cursor_end: datetime | None = None,
        cursor_id: int | None = None,
    ) -> list[Drive]:
        self.query_calls += 1
        rows = [drive for drive in self.drives if drive.end_date is not None and drive.id <= watermark]
        rows.sort(key=lambda drive: (drive.end_date, drive.id), reverse=True)
        if cursor_end is not None:
            filtered = []
            for drive in rows:
                if drive.end_date < cursor_end or (
                    drive.end_date == cursor_end and (cursor_id is None or drive.id < cursor_id)
                ):
                    filtered.append(drive)
            rows = filtered
        return rows[:limit]

    def track(self, drive_id: int, start: datetime, end: datetime) -> list[TrackPoint]:
        self.track_calls += 1
        self.query_calls += 1
        points = [
            point
            for point in self.tracks.get(drive_id, [])
            if start <= point.date <= end
        ]
        return sorted(points, key=lambda point: point.date)

    def ended_charges_after(self, car_id: int, watermark: int) -> list[Charge]:
        self.query_calls += 1
        rows = [charge for charge in self.charges if charge.end_date is not None and charge.id > watermark]
        return sorted(rows, key=lambda charge: (charge.end_date, charge.id))

    def charge_by_id(self, car_id: int, charge_id: int) -> Charge | None:
        self.query_calls += 1
        for charge in self.charges:
            if charge.id == charge_id:
                return charge
        return None

    def execute_write(self, sql: str) -> None:
        self.write_attempts.append(sql)
        raise ReadOnlyError("只读账号拒绝写入")


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        public_base_url="http://192.168.1.223:8791",
        database_url="postgresql://teslaslip_ro:db-secret-value@192.168.1.223:45432/teslamate",
        mqtt_username="mqtt-user",
        mqtt_password="mqtt-secret-value",
        bark_device_key="bark-device-secret",
        sqlite_path=str(tmp_path / "teslaslip.sqlite"),
        amap_js_key="",
        amap_security_js_code="",
        trip_link_ttl_days=0,
        outing_gap_minutes=10,
        min_push_distance_km=0.3,
        min_push_duration_min=2,
    )
    values.update(overrides)
    return Settings(**values)


def make_drive(
    drive_id: int,
    start: datetime,
    end: datetime,
    *,
    distance: float | None = 12.4,
    duration: float | None = 28,
    **kwargs,
) -> Drive:
    start_address = kwargs.pop("start_address", Address())
    end_address = kwargs.pop("end_address", Address())
    return Drive(
        id=drive_id,
        start_date=start,
        end_date=end,
        distance_km=distance,
        duration_min=duration,
        speed_max=kwargs.pop("speed_max", 88),
        power_max=kwargs.pop("power_max", 140),
        power_min=kwargs.pop("power_min", -42),
        start_km=kwargs.pop("start_km", 12000),
        end_km=kwargs.pop("end_km", 12012.4),
        start_rated_range_km=kwargs.pop("start_rated", 420),
        end_rated_range_km=kwargs.pop("end_rated", 410),
        start_ideal_range_km=kwargs.pop("start_ideal", 500),
        end_ideal_range_km=kwargs.pop("end_ideal", 488),
        outside_temp_avg=kwargs.pop("outside", 21.5),
        inside_temp_avg=kwargs.pop("inside", 23.0),
        ascent=kwargs.pop("ascent", 16),
        descent=kwargs.pop("descent", 11),
        start_geofence=kwargs.pop("start_geofence", None),
        end_geofence=kwargs.pop("end_geofence", None),
        start_address=start_address,
        end_address=end_address,
    )


def make_charge(charge_id: int, start: datetime, end: datetime, **kwargs) -> Charge:
    return Charge(
        id=charge_id,
        start_date=start,
        end_date=end,
        duration_min=kwargs.pop("duration", 42),
        charge_energy_added=kwargs.pop("energy", 18.5),
        charge_energy_used=kwargs.pop("used", 20.1),
        cost=kwargs.pop("cost", None),
        start_battery_level=kwargs.pop("start_battery", 30),
        end_battery_level=kwargs.pop("end_battery", 80),
        start_rated_range_km=kwargs.pop("start_rated", 140),
        end_rated_range_km=kwargs.pop("end_rated", 340),
        start_ideal_range_km=kwargs.pop("start_ideal", 160),
        end_ideal_range_km=kwargs.pop("end_ideal", 390),
        outside_temp_avg=kwargs.pop("outside", 19),
        geofence=kwargs.pop("geofence", None),
        address=kwargs.pop("address", Address()),
        latitude=kwargs.pop("latitude", 31.23),
        longitude=kwargs.pop("longitude", 121.47),
        samples=kwargs.pop("samples", []),
    )


def line(drive_id: int, start: datetime, end: datetime, lat: float = 31.23, lng: float = 121.47) -> list[TrackPoint]:
    return [
        TrackPoint(date=start, latitude=lat, longitude=lng, battery_level=80, usable_battery_level=78),
        TrackPoint(
            date=end,
            latitude=lat + 0.01,
            longitude=lng + 0.01,
            battery_level=74,
            usable_battery_level=72,
        ),
    ]


async def boot(tmp_path, *, clock: FakeClock | None = None, bark: FakeBark | None = None, **settings_kw):
    clock = clock or FakeClock()
    bark = bark or FakeBark()
    teslamate = MemoryTeslaMate()
    cfg = make_settings(tmp_path, **settings_kw)
    runtime = Runtime(
        cfg,
        Store(cfg.sqlite_path),
        teslamate,
        bark,
        clock,
        SecretVault(secret_values(cfg)),
        enable_tasks=False,
    )
    await runtime.startup()
    return runtime, teslamate, bark, clock


def token_from(payload: dict) -> str:
    return payload["url"].rstrip("/").split("/")[-1]


async def baseline(runtime, state: str = "online", charging: str = "Disconnected") -> dict[str, str]:
    topics = topic_map(runtime.settings.car_id, runtime.settings.mqtt_namespace)
    inverse = {kind: topic for topic, kind in topics.items()}
    await runtime.on_mqtt_connect()
    await runtime.on_mqtt_message(inverse["state"], state)
    await runtime.on_mqtt_message(inverse["charging_state"], charging)
    return inverse
