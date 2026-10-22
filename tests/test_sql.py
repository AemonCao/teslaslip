import re
import sqlite3

from app.models import Car
from app.teslamate import (
    ALL_SQL,
    CAR_SQL,
    CHARGE_BY_ID_SQL,
    CHARGE_SAMPLES_SQL,
    MAX_ENDED_DRIVE_SQL,
    POSITIONS_SQL,
    assert_select_only,
    car_from_row,
    positions_query_is_bounded,
)


SCHEMA = """
CREATE TABLE drives (
    id integer primary key,
    car_id integer,
    start_date text,
    end_date text,
    distance real,
    duration_min integer,
    speed_max integer,
    power_max integer,
    power_min integer,
    start_km real,
    end_km real,
    start_rated_range_km real,
    end_rated_range_km real,
    start_ideal_range_km real,
    end_ideal_range_km real,
    outside_temp_avg real,
    inside_temp_avg real,
    ascent integer,
    descent integer,
    start_geofence_id integer,
    end_geofence_id integer,
    start_address_id integer,
    end_address_id integer
);
CREATE TABLE geofences (id integer primary key, name text);
CREATE TABLE addresses (
    id integer primary key,
    display_name text,
    road text,
    house_number text,
    neighbourhood text,
    city text,
    name text
);
CREATE TABLE positions (
    id integer primary key,
    date text,
    latitude real,
    longitude real,
    battery_level integer,
    usable_battery_level integer,
    tpms_pressure_fl real,
    tpms_pressure_fr real,
    tpms_pressure_rl real,
    tpms_pressure_rr real,
    is_climate_on integer,
    driver_temp_setting real,
    passenger_temp_setting real,
    is_front_defroster_on integer,
    is_rear_defroster_on integer,
    drive_id integer,
    car_id integer
);
CREATE TABLE charging_processes (
    id integer primary key,
    car_id integer,
    start_date text,
    end_date text,
    duration_min integer,
    charge_energy_added real,
    charge_energy_used real,
    cost real,
    start_battery_level integer,
    end_battery_level integer,
    start_rated_range_km real,
    end_rated_range_km real,
    start_ideal_range_km real,
    end_ideal_range_km real,
    outside_temp_avg real,
    geofence_id integer,
    address_id integer,
    position_id integer
);
CREATE TABLE charges (
    id integer primary key,
    date text,
    charger_power integer,
    charger_voltage integer,
    charger_actual_current integer,
    charger_phases integer,
    conn_charge_cable text,
    fast_charger_brand text,
    fast_charger_type text,
    fast_charger_present integer,
    charging_process_id integer
);
"""


def to_sqlite(sql: str) -> str:
    return re.sub(r"%\((\w+)\)s", r":\1", sql)


def fetch(conn, sql, params):
    conn.row_factory = sqlite3.Row
    rows = conn.execute(to_sqlite(sql), params).fetchall()
    return [dict(row) for row in rows]


def test_shipped_sql_is_select_only_and_positions_are_bounded():
    for sql in ALL_SQL:
        assert_select_only(sql)
    assert positions_query_is_bounded()
    assert "count(*)" not in POSITIONS_SQL.lower()


def test_car_query_reads_model_without_vin():
    compact = " ".join(CAR_SQL.lower().split())
    assert compact.startswith("select model, marketing_name from cars where id = %(car_id)s")
    assert "vin" not in compact
    assert car_from_row({"model": " 3 ", "marketing_name": " SR+ "}) == Car(model="3", marketing_name="SR+")
    assert car_from_row({"model": "", "marketing_name": None}).model is None


def test_position_query_uses_drive_id_and_time_window():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO positions(id, date, latitude, longitude, drive_id) VALUES (1, '2026-10-17T02:00:00+00:00', 31.2, 121.4, 7)"
    )
    conn.execute(
        "INSERT INTO positions(id, date, latitude, longitude, drive_id) VALUES (2, '2026-10-17T03:00:00+00:00', 32.2, 122.4, 7)"
    )
    conn.execute(
        "INSERT INTO positions(id, date, latitude, longitude, drive_id) VALUES (3, '2026-10-17T02:10:00+00:00', 33.2, 123.4, 8)"
    )
    rows = fetch(
        conn,
        POSITIONS_SQL,
        {
            "drive_id": 7,
            "start_date": "2026-10-17T02:00:00+00:00",
            "end_date": "2026-10-17T02:30:00+00:00",
        },
    )
    assert len(rows) == 1
    assert rows[0]["latitude"] == 31.2
    assert rows[0]["longitude"] == 121.4


def test_watermark_sql_ignores_open_drive_even_if_id_is_larger():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO drives(id, car_id, end_date) VALUES (1, 1, '2026-10-17T01:00:00+00:00')")
    conn.execute("INSERT INTO drives(id, car_id, end_date) VALUES (2, 1, NULL)")
    conn.execute("INSERT INTO drives(id, car_id, end_date) VALUES (4, 1, '2026-10-17T02:00:00+00:00')")
    conn.execute("INSERT INTO drives(id, car_id, end_date) VALUES (9, 1, NULL)")
    rows = fetch(conn, MAX_ENDED_DRIVE_SQL, {"car_id": 1})
    assert rows[0]["max_id"] == 4


def test_charge_query_is_by_id_and_samples_do_not_cross_sessions():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO positions(id, date, latitude, longitude) VALUES (10, '2026-10-17T01:00:00+00:00', 31.1, 121.1)")
    conn.execute("INSERT INTO positions(id, date, latitude, longitude) VALUES (11, '2026-10-17T01:00:00+00:00', 39.9, 116.3)")
    conn.execute(
        """
        INSERT INTO charging_processes(
            id, car_id, start_date, end_date, position_id, charge_energy_added
        ) VALUES (3, 1, '2026-10-17T01:00:00+00:00', '2026-10-17T02:00:00+00:00', 10, 18.5)
        """
    )
    conn.execute(
        """
        INSERT INTO charging_processes(
            id, car_id, start_date, end_date, position_id, charge_energy_added
        ) VALUES (4, 1, '2026-10-17T03:00:00+00:00', '2026-10-17T04:00:00+00:00', 11, 2)
        """
    )
    conn.execute(
        "INSERT INTO charges(id, date, charger_power, charging_process_id) VALUES (1, '2026-10-17T01:10:00+00:00', 7, 3)"
    )
    conn.execute(
        "INSERT INTO charges(id, date, charger_power, charging_process_id) VALUES (2, '2026-10-17T03:10:00+00:00', 120, 4)"
    )
    charge = fetch(conn, CHARGE_BY_ID_SQL, {"car_id": 1, "charge_id": 3})
    samples = fetch(conn, CHARGE_SAMPLES_SQL, {"charge_id": 3})
    assert charge[0]["latitude"] == 31.1
    assert charge[0]["longitude"] == 121.1
    assert [row["charger_power"] for row in samples] == [7]
