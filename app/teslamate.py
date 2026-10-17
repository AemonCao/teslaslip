"""TeslaMate 只读查询。位置查询必须带 drive_id 和时间范围。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.formatutil import as_naive_utc, as_utc
from app.models import Address, Charge, ChargeSample, Drive, TrackPoint

READONLY_OPTIONS = "-c statement_timeout=15000 -c default_transaction_read_only=on"

_WRITE_WORDS = (
    "insert ",
    "update ",
    "delete ",
    "drop ",
    "alter ",
    "create ",
    "grant ",
    "truncate ",
    "copy ",
    "vacuum ",
    "reindex ",
    "comment ",
    "refresh ",
    "merge ",
)


class ReadOnlyError(RuntimeError):
    pass


def assert_select_only(sql: str) -> None:
    stripped = sql.strip().lstrip("(").strip()
    if not stripped:
        raise ReadOnlyError("空语句")
    head = stripped.split(None, 1)[0].lower()
    if head not in {"select", "with"}:
        raise ReadOnlyError("拒绝非只读语句")
    lowered = f" {stripped.lower()} "
    for word in _WRITE_WORDS:
        if word in lowered:
            raise ReadOnlyError("拒绝写入语句")


MAX_ENDED_DRIVE_SQL = """
SELECT COALESCE(MAX(id), 0) AS max_id
FROM drives
WHERE car_id = %(car_id)s
  AND end_date IS NOT NULL
"""

MAX_ENDED_CHARGE_SQL = """
SELECT COALESCE(MAX(id), 0) AS max_id
FROM charging_processes
WHERE car_id = %(car_id)s
  AND end_date IS NOT NULL
"""

DRIVE_COLUMNS = """
  d.id,
  d.start_date,
  d.end_date,
  d.distance,
  d.duration_min,
  d.speed_max,
  d.power_max,
  d.power_min,
  d.start_km,
  d.end_km,
  d.start_rated_range_km,
  d.end_rated_range_km,
  d.start_ideal_range_km,
  d.end_ideal_range_km,
  d.outside_temp_avg,
  d.inside_temp_avg,
  d.ascent,
  d.descent,
  sg.name AS start_geofence_name,
  eg.name AS end_geofence_name,
  sa.display_name AS start_display_name,
  sa.road AS start_road,
  sa.house_number AS start_house_number,
  sa.neighbourhood AS start_neighbourhood,
  sa.city AS start_city,
  sa.name AS start_place_name,
  ea.display_name AS end_display_name,
  ea.road AS end_road,
  ea.house_number AS end_house_number,
  ea.neighbourhood AS end_neighbourhood,
  ea.city AS end_city,
  ea.name AS end_place_name
"""

DRIVE_JOINS = """
FROM drives d
LEFT JOIN geofences sg ON sg.id = d.start_geofence_id
LEFT JOIN geofences eg ON eg.id = d.end_geofence_id
LEFT JOIN addresses sa ON sa.id = d.start_address_id
LEFT JOIN addresses ea ON ea.id = d.end_address_id
"""

DRIVES_AFTER_SQL = f"""
SELECT {DRIVE_COLUMNS}
{DRIVE_JOINS}
WHERE d.car_id = %(car_id)s
  AND d.end_date IS NOT NULL
  AND d.id > %(watermark)s
ORDER BY d.end_date ASC, d.id ASC
"""

DRIVE_BY_ID_SQL = f"""
SELECT {DRIVE_COLUMNS}
{DRIVE_JOINS}
WHERE d.car_id = %(car_id)s
  AND d.id = %(drive_id)s
"""

RECENT_DRIVES_SQL = f"""
SELECT {DRIVE_COLUMNS}
{DRIVE_JOINS}
WHERE d.car_id = %(car_id)s
  AND d.end_date IS NOT NULL
  AND d.id <= %(watermark)s
  AND (
    %(cursor_end)s IS NULL
    OR d.end_date < %(cursor_end)s
    OR (d.end_date = %(cursor_end)s AND d.id < %(cursor_id)s)
  )
ORDER BY d.end_date DESC, d.id DESC
LIMIT %(limit)s
"""

POSITIONS_SQL = """
SELECT
  date,
  latitude,
  longitude,
  battery_level,
  usable_battery_level,
  tpms_pressure_fl,
  tpms_pressure_fr,
  tpms_pressure_rl,
  tpms_pressure_rr,
  is_climate_on,
  driver_temp_setting,
  passenger_temp_setting,
  is_front_defroster_on,
  is_rear_defroster_on
FROM positions
WHERE drive_id = %(drive_id)s
  AND date >= %(start_date)s
  AND date <= %(end_date)s
ORDER BY date ASC
"""

CHARGES_AFTER_SQL = """
SELECT id
FROM charging_processes
WHERE car_id = %(car_id)s
  AND end_date IS NOT NULL
  AND id > %(watermark)s
ORDER BY end_date ASC, id ASC
"""

CHARGE_BY_ID_SQL = """
SELECT
  c.id,
  c.start_date,
  c.end_date,
  c.duration_min,
  c.charge_energy_added,
  c.charge_energy_used,
  c.cost,
  c.start_battery_level,
  c.end_battery_level,
  c.start_rated_range_km,
  c.end_rated_range_km,
  c.start_ideal_range_km,
  c.end_ideal_range_km,
  c.outside_temp_avg,
  g.name AS geofence_name,
  a.display_name,
  a.road,
  a.house_number,
  a.neighbourhood,
  a.city,
  a.name AS place_name,
  p.latitude,
  p.longitude
FROM charging_processes c
LEFT JOIN geofences g ON g.id = c.geofence_id
LEFT JOIN addresses a ON a.id = c.address_id
LEFT JOIN positions p ON p.id = c.position_id
WHERE c.car_id = %(car_id)s
  AND c.id = %(charge_id)s
"""

CHARGE_SAMPLES_SQL = """
SELECT
  date,
  charger_power,
  charger_voltage,
  charger_actual_current,
  charger_phases,
  conn_charge_cable,
  fast_charger_brand,
  fast_charger_type,
  fast_charger_present
FROM charges
WHERE charging_process_id = %(charge_id)s
ORDER BY date ASC
"""

SELECT_ONE_SQL = "SELECT 1"

ALL_SQL = (
    MAX_ENDED_DRIVE_SQL,
    MAX_ENDED_CHARGE_SQL,
    DRIVES_AFTER_SQL,
    DRIVE_BY_ID_SQL,
    RECENT_DRIVES_SQL,
    POSITIONS_SQL,
    CHARGES_AFTER_SQL,
    CHARGE_BY_ID_SQL,
    CHARGE_SAMPLES_SQL,
    SELECT_ONE_SQL,
)


def positions_query_is_bounded(sql: str = POSITIONS_SQL) -> bool:
    lowered = sql.lower()
    return (
        "drive_id = %(drive_id)s" in lowered
        and "date >= %(start_date)s" in lowered
        and "date <= %(end_date)s" in lowered
        and "count(*)" not in lowered
    )


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"t", "true", "1", "yes"}:
        return True
    if text in {"f", "false", "0", "no"}:
        return False
    return bool(value)


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return as_utc(value)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return as_utc(parsed)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def drive_from_row(row: dict[str, Any]) -> Drive:
    return Drive(
        id=int(row["id"]),
        start_date=_dt(row["start_date"]),
        end_date=_dt(row["end_date"]),
        distance_km=_float(row.get("distance")),
        duration_min=_float(row.get("duration_min")),
        speed_max=_float(row.get("speed_max")),
        power_max=_float(row.get("power_max")),
        power_min=_float(row.get("power_min")),
        start_km=_float(row.get("start_km")),
        end_km=_float(row.get("end_km")),
        start_rated_range_km=_float(row.get("start_rated_range_km")),
        end_rated_range_km=_float(row.get("end_rated_range_km")),
        start_ideal_range_km=_float(row.get("start_ideal_range_km")),
        end_ideal_range_km=_float(row.get("end_ideal_range_km")),
        outside_temp_avg=_float(row.get("outside_temp_avg")),
        inside_temp_avg=_float(row.get("inside_temp_avg")),
        ascent=_float(row.get("ascent")),
        descent=_float(row.get("descent")),
        start_geofence=_text(row.get("start_geofence_name")),
        end_geofence=_text(row.get("end_geofence_name")),
        start_address=Address(
            display_name=_text(row.get("start_display_name")),
            road=_text(row.get("start_road")),
            house_number=_text(row.get("start_house_number")),
            neighbourhood=_text(row.get("start_neighbourhood")),
            city=_text(row.get("start_city")),
            name=_text(row.get("start_place_name")),
        ),
        end_address=Address(
            display_name=_text(row.get("end_display_name")),
            road=_text(row.get("end_road")),
            house_number=_text(row.get("end_house_number")),
            neighbourhood=_text(row.get("end_neighbourhood")),
            city=_text(row.get("end_city")),
            name=_text(row.get("end_place_name")),
        ),
    )


def track_from_row(row: dict[str, Any]) -> TrackPoint:
    return TrackPoint(
        date=_dt(row["date"]),
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        battery_level=_int(row.get("battery_level")),
        usable_battery_level=_int(row.get("usable_battery_level")),
        tpms_fl=_float(row.get("tpms_pressure_fl")),
        tpms_fr=_float(row.get("tpms_pressure_fr")),
        tpms_rl=_float(row.get("tpms_pressure_rl")),
        tpms_rr=_float(row.get("tpms_pressure_rr")),
        climate_on=_bool(row.get("is_climate_on")),
        driver_temp=_float(row.get("driver_temp_setting")),
        passenger_temp=_float(row.get("passenger_temp_setting")),
        front_defrost=_bool(row.get("is_front_defroster_on")),
        rear_defrost=_bool(row.get("is_rear_defroster_on")),
    )


def charge_from_row(row: dict[str, Any], samples: list[ChargeSample]) -> Charge:
    return Charge(
        id=int(row["id"]),
        start_date=_dt(row["start_date"]),
        end_date=_dt(row["end_date"]),
        duration_min=_float(row.get("duration_min")),
        charge_energy_added=_float(row.get("charge_energy_added")),
        charge_energy_used=_float(row.get("charge_energy_used")),
        cost=_float(row.get("cost")),
        start_battery_level=_int(row.get("start_battery_level")),
        end_battery_level=_int(row.get("end_battery_level")),
        start_rated_range_km=_float(row.get("start_rated_range_km")),
        end_rated_range_km=_float(row.get("end_rated_range_km")),
        start_ideal_range_km=_float(row.get("start_ideal_range_km")),
        end_ideal_range_km=_float(row.get("end_ideal_range_km")),
        outside_temp_avg=_float(row.get("outside_temp_avg")),
        geofence=_text(row.get("geofence_name")),
        address=Address(
            display_name=_text(row.get("display_name")),
            road=_text(row.get("road")),
            house_number=_text(row.get("house_number")),
            neighbourhood=_text(row.get("neighbourhood")),
            city=_text(row.get("city")),
            name=_text(row.get("place_name")),
        ),
        latitude=_float(row.get("latitude")),
        longitude=_float(row.get("longitude")),
        samples=samples,
    )


def sample_from_row(row: dict[str, Any]) -> ChargeSample:
    return ChargeSample(
        date=_dt(row["date"]),
        charger_power=_float(row.get("charger_power")),
        charger_voltage=_float(row.get("charger_voltage")),
        charger_current=_float(row.get("charger_actual_current")),
        charger_phases=_int(row.get("charger_phases")),
        cable=_text(row.get("conn_charge_cable")),
        fast_charger_brand=_text(row.get("fast_charger_brand")),
        fast_charger_type=_text(row.get("fast_charger_type")),
        fast_charger_present=_bool(row.get("fast_charger_present")),
    )


class PostgresTeslaMate:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.track_calls = 0

    def ping(self) -> bool:
        try:
            self._fetch(SELECT_ONE_SQL, {})
            return True
        except Exception:
            return False

    def max_ended_drive_id(self, car_id: int) -> int:
        rows = self._fetch(MAX_ENDED_DRIVE_SQL, {"car_id": car_id})
        if not rows:
            return 0
        return int(rows[0]["max_id"] or 0)

    def max_ended_charge_id(self, car_id: int) -> int:
        rows = self._fetch(MAX_ENDED_CHARGE_SQL, {"car_id": car_id})
        if not rows:
            return 0
        return int(rows[0]["max_id"] or 0)

    def ended_drives_after(self, car_id: int, watermark: int) -> list[Drive]:
        rows = self._fetch(DRIVES_AFTER_SQL, {"car_id": car_id, "watermark": watermark})
        return [drive_from_row(row) for row in rows]

    def drives_by_ids(self, car_id: int, drive_ids: list[int]) -> list[Drive]:
        found: list[Drive] = []
        for drive_id in drive_ids:
            rows = self._fetch(DRIVE_BY_ID_SQL, {"car_id": car_id, "drive_id": drive_id})
            found.extend(drive_from_row(row) for row in rows)
        return sorted(found, key=lambda drive: (drive.end_date, drive.id))

    def recent_ended_drives(
        self,
        car_id: int,
        watermark: int,
        *,
        limit: int,
        cursor_end: datetime | None = None,
        cursor_id: int | None = None,
    ) -> list[Drive]:
        rows = self._fetch(
            RECENT_DRIVES_SQL,
            {
                "car_id": car_id,
                "watermark": watermark,
                "limit": limit,
                "cursor_end": as_naive_utc(cursor_end) if cursor_end else None,
                "cursor_id": cursor_id,
            },
        )
        return [drive_from_row(row) for row in rows]

    def track(self, drive_id: int, start: datetime, end: datetime) -> list[TrackPoint]:
        self.track_calls += 1
        rows = self._fetch(
            POSITIONS_SQL,
            {
                "drive_id": drive_id,
                "start_date": as_naive_utc(start),
                "end_date": as_naive_utc(end),
            },
        )
        return [track_from_row(row) for row in rows]

    def ended_charges_after(self, car_id: int, watermark: int) -> list[Charge]:
        rows = self._fetch(CHARGES_AFTER_SQL, {"car_id": car_id, "watermark": watermark})
        charges: list[Charge] = []
        for row in rows:
            charge = self.charge_by_id(car_id, int(row["id"]))
            if charge is not None:
                charges.append(charge)
        return charges

    def charge_by_id(self, car_id: int, charge_id: int) -> Charge | None:
        rows = self._fetch(CHARGE_BY_ID_SQL, {"car_id": car_id, "charge_id": charge_id})
        if not rows:
            return None
        samples = [
            sample_from_row(row)
            for row in self._fetch(CHARGE_SAMPLES_SQL, {"charge_id": charge_id})
        ]
        return charge_from_row(rows[0], samples)

    def _fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert_select_only(sql)
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            self.database_url,
            autocommit=True,
            connect_timeout=5,
            options=READONLY_OPTIONS,
        ) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
