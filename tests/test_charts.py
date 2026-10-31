import asyncio
import json
import math
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.charts import charge_charts, drive_charts, millis, safe_json
from app.main import create_app
from app.models import ChargeSample, TrackPoint
from app.teslamate import sample_from_row, track_from_row
from tests.fakes import T0, boot, make_charge, make_drive


def point(seconds, **values):
    return TrackPoint(date=T0 + timedelta(seconds=seconds), latitude=30, longitude=120, **values)


def metric(data, key):
    return next(series for series in data["metrics"] if series["key"] == key)


def values(series):
    return [item for chunk in series["chunks"] for item in chunk["points"]]


def test_independent_validation_sorting_duplicates_bounds_and_utc():
    drive = make_drive(1, T0, T0 + timedelta(seconds=100))
    rows = [
        point(-1, speed=999), point(11, speed=11, power=None),
        point(10, speed=10, power=-20), point(10, speed=None, power=0),
        point(10, speed=0, power=math.nan), point(12, speed=-1, power=math.inf),
        point(13, speed=math.nan, power=-42), point(101, speed=999),
    ]
    data = drive_charts([drive], [rows])
    assert values(metric(data, "speed")) == [[millis(T0) + 10000, 0], [millis(T0) + 11000, 11]]
    assert values(metric(data, "power")) == [[millis(T0) + 10000, 0], [millis(T0) + 13000, -42]]
    assert metric(data, "power")["min"] == -42
    assert metric(data, "battery_level")["count"] == 0
    assert millis(T0.replace(tzinfo=None)) == millis(T0)
    encoded = safe_json(data)
    assert "NaN" not in encoded and "Infinity" not in encoded
    assert "latitude" not in encoded and "longitude" not in encoded
    assert "drive_id" not in encoded


def test_long_gaps_segments_sparse_states_and_singletons():
    drives = [make_drive(1, T0, T0 + timedelta(seconds=200)), make_drive(2, T0 + timedelta(seconds=300), T0 + timedelta(seconds=400))]
    first = [point(second, speed=second, inside_temp=21 if second in (0, 1, 2, 100) else None) for second in (0, 1, 2, 100, 101, 102)]
    first.extend([point(0, climate_on=True), point(1), point(2, climate_on=False)])
    data = drive_charts(drives, [first, [point(320, speed=0)]])
    speed = metric(data, "speed")
    assert [len(chunk["points"]) for chunk in speed["chunks"]] == [3, 3, 1]
    assert [chunk["segment"] for chunk in speed["chunks"]] == [1, 1, 2]
    assert speed["chunks"][0]["threshold"] == 30000
    assert len(metric(data, "climate_on")["chunks"]) == 2
    assert metric(data, "inside_temp")["count"] == 4
    assert len(metric(data, "inside_temp")["chunks"]) == 2
    assert data["start"] == millis(T0) and data["end"] == millis(drives[-1].end_date)


def test_time_bucket_thinning_keeps_extrema_gaps_and_step_transitions():
    drive = make_drive(1, T0, T0 + timedelta(seconds=20000))
    rows = [point(index / 4, speed=30, power=1, battery_level=80 if index < 12345 else 79) for index in range(40000)]
    rows[12345].speed = 170
    rows[12346].power = -199
    rows[12347].power = 301
    rows.append(point(19000, speed=90))
    data = drive_charts([drive], [rows])
    power = metric(data, "power")
    assert {item[1] for item in values(power)} >= {-199, 301}
    assert power["min"] == -199 and power["max"] == 301 and power["count"] == 40000
    assert len(values(power)) <= 2000
    speed = metric(data, "speed")
    assert speed["max"] == 170
    assert len(speed["chunks"]) == 2
    assert values(speed)[0][0] == millis(T0)
    assert values(speed)[-1][0] == millis(T0) + 19000000
    assert all(chunk["points"] == sorted(chunk["points"]) for chunk in speed["chunks"])
    battery = values(metric(data, "battery_level"))
    assert [millis(T0) + 3086000, 80] in battery
    assert [millis(T0) + 3086250, 79] in battery


def test_charge_reset_and_metrics_degrade_independently():
    charge = make_charge(1, T0, T0 + timedelta(seconds=100), samples=[
        ChargeSample(date=T0 + timedelta(seconds=second), charger_power=power, charge_energy_added=energy)
        for second, power, energy in [(0, 7, 0), (1, 7, 0.1), (2, None, 0.01), (3, 0, 0.02)]
    ])
    data = charge_charts(charge)
    energy = metric(data, "charge_energy_added")
    assert energy["anomalies"] == [millis(T0) + 2000]
    assert [len(chunk["points"]) for chunk in energy["chunks"]] == [2, 2]
    assert metric(data, "battery_level")["count"] == 0
    assert metric(data, "charger_power")["count"] == 3
    assert values(metric(data, "charger_power"))[-1][1] == 0
    assert data["distribution"] is None


def test_sparse_metric_threshold_is_five_times_its_own_median():
    drive = make_drive(1, T0, T0 + timedelta(seconds=1000))
    rows = [point(second, inside_temp=21) for second in (0, 20, 40, 60, 80, 500)]
    series = metric(drive_charts([drive], [rows]), "inside_temp")
    assert len(series["chunks"]) == 2
    assert series["chunks"][0]["threshold"] == 100000


@pytest.mark.parametrize("power,fast", [(7, False), (120, True)])
def test_charge_charts_stay_within_the_valid_session(tmp_path, power, fast):
    async def body():
        runtime, tm, _bark, _clock = await boot(tmp_path)
        tm.charges.extend([
            make_charge(1, T0, T0 + timedelta(minutes=1), samples=[ChargeSample(date=T0, charger_power=power, battery_level=30, charge_energy_added=0, fast_charger_present=fast)]),
            make_charge(2, T0, T0 + timedelta(minutes=1), samples=[ChargeSample(date=T0, charger_power=999)]),
        ])
        runtime.store.insert_link(token="charge-chart-test", kind="charge", drive_ids=None, charge_id=1, destination=None, created_at=T0, expires_at=None, preview=True)
        with TestClient(create_app(runtime)) as client:
            response = client.get("/c/charge-chart-test")
            assert response.status_code == 200
            match = re.search(r'<script id="chart-data" type="application/json">(.*?)</script>', response.text, re.S)
            chart = json.loads(match.group(1))
            assert chart["kind"] == "charge"
            assert metric(chart, "charger_power")["max"] == power
            assert values(metric(chart, "charge_energy_added"))[0][1] == 0
            assert client.get("/d/charge-chart-test").status_code == 404
            assert tm.track_calls == 0 and tm.write_attempts == []
    asyncio.run(body())


def test_distribution_is_time_weighted_boundaries_gaps_and_waiting():
    drive = make_drive(1, T0, T0 + timedelta(seconds=300))
    rows = [point(second, speed=speed) for second, speed in [(0, 0), (10, 0), (30, 0), (31, 20), (32, 40), (33, 60), (34, 80), (35, 100), (36, 1), (40, 1), (45, 5), (46, 0), (200, 0), (201, 0)]]
    data = drive_charts([drive], [rows])["distribution"]
    assert [item["seconds"] for item in data["bins"]] == [32, 10, 1, 1, 1, 1, 1]
    assert data["coveredSeconds"] == 47
    assert data["uncoveredSeconds"] == 253
    assert sum(item["percent"] for item in data["bins"]) == pytest.approx(100)
    assert data["waiting"] == [{"segment": 1, "start": millis(T0), "end": millis(T0) + 30000, "seconds": 30}]
    next_drive = make_drive(2, T0 + timedelta(seconds=1000), T0 + timedelta(seconds=1020))
    split = drive_charts([drive, next_drive], [[point(290, speed=0), point(300, speed=0)], [point(1000, speed=0), point(1020, speed=0)]])["distribution"]
    assert split["waiting"] == []
    assert split["coveredSeconds"] == 30
    assert split["uncoveredSeconds"] == 290


def test_safe_json_and_new_model_mapping():
    hostile = "</script><script>alert('x')</script>&\u2028\u2029"
    encoded = safe_json({"text": hostile})
    assert "<" not in encoded and "&" not in encoded
    assert json.loads(encoded)["text"] == hostile
    row = {"date": T0, "latitude": 30, "longitude": 120, "power": -17, "elevation": 22, "inside_temp": 21.5, "outside_temp": 18, "odometer": 1000, "rated_battery_range_km": 400, "ideal_battery_range_km": 450, "battery_heater_on": False}
    track = track_from_row(row)
    assert (track.power, track.elevation, track.rated_range, track.ideal_range, track.battery_heater_on) == (-17, 22, 400, 450, False)
    sample = sample_from_row({**row, "battery_level": 80, "usable_battery_level": 79, "charge_energy_added": 0})
    assert (sample.battery_level, sample.usable_battery_level, sample.charge_energy_added, sample.outside_temp) == (80, 79, 0, 18)


def test_charts_only_on_existing_valid_links_and_no_extra_queries(tmp_path):
    async def body():
        runtime, tm, _bark, _clock = await boot(tmp_path)
        drive = make_drive(1, T0, T0 + timedelta(seconds=50))
        tm.drives.append(drive)
        tm.tracks[1] = [point(0, speed=0), point(50, speed=20, power=-10)]
        runtime.store.insert_link(token="chart-test-token", kind="drive", drive_ids=[1], charge_id=None, destination=None, created_at=T0, expires_at=T0 + timedelta(days=1), preview=True)
        with TestClient(create_app(runtime)) as client:
            before = tm.track_calls
            response = client.get("/d/chart-test-token")
            assert response.status_code == 200
            assert tm.track_calls == before + 1
            match = re.search(r'<script id="chart-data" type="application/json">(.*?)</script>', response.text, re.S)
            assert match and json.loads(match.group(1))["kind"] == "drive"
            assert "交互图表需要 JavaScript" in response.text
            assert client.get("/d/1").status_code == 404
            assert client.get("/c/chart-test-token").status_code == 404
            assert client.get("/api/charts/1").status_code == 404
            _clock.advance(timedelta(days=2))
            assert client.get("/d/chart-test-token").status_code == 404
            assert tm.write_attempts == []
    asyncio.run(body())
