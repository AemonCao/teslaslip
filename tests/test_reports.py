import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError
from app import report as report_command
from app.db import Store
from app.formatutil import SHANGHAI
from app.main import create_app
from app.models import Address
from app.redact import redact_text
from app.reports import ReportService, build_report, latest_due_period, period_for, previous_period
from app.teslamate import FIRST_RECORD_SQL, REPORT_CHARGES_SQL, REPORT_DRIVES_SQL, PostgresTeslaMate
from tests.fakes import FakeBark, FakeClock, MemoryTeslaMate, boot, make_charge, make_drive, make_settings
from tests.test_sql import SCHEMA, fetch


def local(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=SHANGHAI).astimezone(timezone.utc)


@pytest.mark.parametrize("kind,day,start,end", [
    ("week", date(2027, 1, 1), local(2026, 12, 28), local(2027, 1, 4)),
    ("month", date(2024, 2, 29), local(2024, 2, 1), local(2024, 3, 1)),
    ("month", date(2026, 12, 31), local(2026, 12, 1), local(2027, 1, 1)),
    ("year", date(2024, 6, 1), local(2024, 1, 1), local(2025, 1, 1)),
])
def test_natural_period_boundaries(kind, day, start, end):
    period = period_for(kind, day)
    assert (period.start, period.end) == (start, end)
    assert previous_period(period).end == start


def test_shanghai_clock_and_due_hour():
    assert period_for("month", datetime(2026, 10, 31, 16, tzinfo=timezone.utc)).start == local(2026, 11, 1)
    assert latest_due_period("week", local(2026, 11, 2, 8, 59), 9).start == local(2026, 10, 19)
    assert latest_due_period("week", local(2026, 11, 2, 9), 9).start == local(2026, 10, 26)
    assert latest_due_period("year", local(2027, 1, 1, 8), 9).start == local(2025, 1, 1)
    assert latest_due_period("year", local(2027, 1, 1, 9), 9).start == local(2026, 1, 1)


def test_totals_missing_values_zero_cost_grouping_and_comparisons(tmp_path):
    settings = make_settings(tmp_path)
    period = period_for("month", date(2026, 10, 15))
    start = local(2026, 10, 5, 8)
    drives = [
        make_drive(1, local(2026, 9, 1), local(2026, 9, 1, 1), distance=10),
        make_drive(2, start, start + timedelta(minutes=10), distance=10, duration=10),
        make_drive(3, start + timedelta(minutes=19), start + timedelta(minutes=29), distance=20, duration=10),
        make_drive(4, local(2026, 10, 6), local(2026, 10, 6, 1), distance=None, duration=None),
        make_drive(5, local(2026, 10, 7), local(2026, 10, 7, 0, 1), distance=0.1, duration=1),
        make_drive(6, period.end - timedelta(minutes=10), period.end, distance=999),
    ]
    charges = [
        {"end_date": local(2026, 10, 5), "charge_energy_added": 20, "duration_min": 60, "cost": 0},
        {"end_date": local(2026, 10, 6), "charge_energy_added": None, "duration_min": 30, "cost": None},
    ]
    report = build_report(period, drives, charges, local(2026, 8, 1), settings, local(2026, 11, 1, 9))
    metrics = {row["label"]: row for row in report["metrics"]}
    assert metrics["行驶里程"]["value"] == "30.1 km"
    assert metrics["出门次数"]["value"] == "2 次"
    assert metrics["用车天数"]["value"] == "3 天"
    assert metrics["已记录充电费用"]["value"] == "0.00"
    assert report["cost_coverage"] == "1/2 次有费用记录"
    assert report["comparisons"][0]["text"] == "存在缺失记录，暂不比较"
    assert len(report["trend"]) == 31
    assert report["trend"][4]["distance"] == 30
    assert report["trend"][5]["distance"] is None
    assert report["trend"][0]["has_records"] is False
    assert "采集是否连续" in report["notes"][0]
    json.dumps(report, allow_nan=False)


def test_empty_period_partial_coverage_and_invalid_metrics(tmp_path):
    settings = make_settings(tmp_path)
    period = period_for("year", date(2026, 10, 1))
    report = build_report(period, [], [], None, settings, local(2027, 1, 1))
    assert "没有已结束" in report["opening"]
    assert report["metrics"][0]["value"] == "0.0 km"
    assert report["metrics"][-1]["value"] == "未知"
    assert len(report["trend"]) == 12
    assert report["highlights"] == []
    drive = make_drive(1, local(2026, 6, 1), local(2026, 6, 1, 1), distance=float("nan"), duration=-1)
    report = build_report(period, [drive], [], drive.start_date, settings, local(2027, 1, 1))
    assert "不完整" in report["coverage"]
    assert report["metrics"][0]["value"] == "未知"
    assert all("不足" in item["text"] for item in report["comparisons"])
    json.dumps(report, allow_nan=False)


def test_comparison_zero_baseline_and_percentage(tmp_path):
    settings = make_settings(tmp_path)
    period = period_for("week", date(2026, 10, 26))
    previous = previous_period(period)
    drives = [
        make_drive(1, previous.start, previous.start + timedelta(hours=1), distance=10),
        make_drive(2, period.start, period.start + timedelta(hours=1), distance=15),
    ]
    report = build_report(period, drives, [], local(2020, 1, 1), settings, period.end)
    assert report["comparisons"][0]["text"] == "比上期增加 50.0%"
    assert report["comparisons"][2]["text"] == "均为 0"
    report = build_report(period, drives[1:], [], local(2020, 1, 1), settings, period.end)
    assert "不计算百分比" in report["comparisons"][0]["text"]


def test_report_sql_filters_car_time_and_ended_records_without_samples():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    for drive_id, car_id, end in ((1, 1, "2026-09-30T16:00:00"), (2, 1, "2026-10-31T16:00:00"), (3, 2, "2026-10-10T00:00:00"), (4, 1, None)):
        conn.execute("INSERT INTO drives(id, car_id, start_date, end_date) VALUES (?, ?, '2026-09-01T00:00:00', ?)", (drive_id, car_id, end))
        conn.execute("INSERT INTO charging_processes(id, car_id, start_date, end_date, charge_energy_added, cost) VALUES (?, ?, '2026-09-01T00:00:00', ?, 10, 0)", (drive_id, car_id, end))
    params = {"car_id": 1, "start_date": "2026-09-30T16:00:00", "end_date": "2026-10-31T16:00:00"}
    assert [row["id"] for row in fetch(conn, REPORT_DRIVES_SQL, params)] == [1]
    assert [row["id"] for row in fetch(conn, REPORT_CHARGES_SQL, params)] == [1]
    assert fetch(conn, FIRST_RECORD_SQL, {"car_id": 1})[0]["first_date"] == "2026-09-01T00:00:00"


def test_postgres_report_mapping_uses_three_bounded_summary_queries(monkeypatch):
    reader = PostgresTeslaMate("unused")
    calls = []

    def query(sql, params):
        calls.append((sql, params))
        if sql == FIRST_RECORD_SQL:
            return [{"first_date": datetime(2026, 9, 1)}]
        if sql == REPORT_CHARGES_SQL:
            return [{"id": 1, "start_date": datetime(2026, 10, 1), "end_date": datetime(2026, 10, 1, 1), "duration_min": 60, "charge_energy_added": 10, "cost": 0, "geofence_name": None}]
        return []

    monkeypatch.setattr(reader, "_fetch", query)
    drives, charges, first = reader.report_records(1, local(2026, 9, 1), local(2026, 11, 1))
    assert drives == []
    assert charges[0]["end_date"].utcoffset() == timedelta(0)
    assert charges[0]["cost"] == 0
    assert first.utcoffset() == timedelta(0)
    assert len(calls) == 3
    assert calls[0][1]["start_date"].tzinfo is None
    assert reader.track_calls == 0


def test_schedule_overlap_retry_restart_snapshot_and_watermarks(tmp_path):
    async def scenario():
        clock = FakeClock(local(2028, 12, 31, 12))
        bark = FakeBark(fail_times=1)
        runtime, tm, _, _ = await boot(tmp_path, clock=clock, bark=bark)
        assert bark.payloads == []
        start = local(2028, 12, 30, 8)
        tm.drives.append(make_drive(1, start, start + timedelta(hours=1), distance=30, end_geofence="私密地点"))
        tm.charges.append(make_charge(1, start, start + timedelta(hours=2), cost=12))
        clock.set(local(2029, 1, 1, 8, 59))
        await runtime.on_reports()
        assert bark.attempts == 0
        clock.advance(timedelta(minutes=1))
        await runtime.on_reports()
        assert bark.attempts == 3
        assert len(bark.payloads) == 2
        assert runtime.store.get_meta("drive_watermark") == "0"
        assert runtime.store.get_meta("charge_watermark") == "0"
        pending = runtime.store.next_outbox_at()
        assert pending == clock.now() + timedelta(seconds=30)
        runtime.store = Store(runtime.settings.sqlite_path)
        runtime.store.init()
        runtime.reports = ReportService(runtime.settings, runtime.store, tm)
        runtime.reports.initialize(clock.now())
        clock.advance(timedelta(seconds=30))
        await runtime.on_reports()
        assert len(bark.payloads) == 3
        assert len({payload["url"] for payload in bark.payloads}) == 3
        assert all("私密地点" not in payload["body"] for payload in bark.payloads)
        queries = tm.query_calls
        await runtime.on_reports()
        assert tm.query_calls == queries
        assert tm.track_calls == 0
        assert runtime.store.next_outbox_at() is None
        token = bark.payloads[0]["url"].split("/")[-1]
        frozen = runtime.store.get_report(token)
        tm.drives.clear()
        assert runtime.store.get_report(token) == frozen

    asyncio.run(scenario())


def test_generation_failure_does_not_advance_cursor_and_catches_up_latest_only(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    store = Store(settings.sqlite_path)
    store.init()
    tm = MemoryTeslaMate()
    service = ReportService(settings, store, tm)
    service.initialize(local(2026, 1, 1, 12))
    key = service._cursor_key("week")
    original = store.get_meta(key)
    loader = tm.report_records
    monkeypatch.setattr(tm, "report_records", lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        service.generate_due(local(2027, 2, 1, 9))
    assert store.get_meta(key) == original
    monkeypatch.setattr(tm, "report_records", loader)
    service.generate_due(local(2027, 2, 1, 9))
    assert len(store.due_outbox(local(2027, 2, 1, 9))) == 3
    assert store.get_meta(key) == local(2027, 1, 25).isoformat()


def test_private_report_routes_expiry_escaping_and_snapshot_only_reads(tmp_path):
    async def prepare():
        runtime, tm, _, clock = await boot(tmp_path, clock=FakeClock(local(2026, 11, 2, 12)), trip_link_ttl_days=1)
        start = local(2026, 10, 5)
        tm.drives.append(make_drive(1, start, start + timedelta(hours=1), end_geofence='<script>alert("x")</script>', end_address=Address(city="上海市")))
        token = runtime.reports.generate(period_for("month", date(2026, 10, 1)), clock.now())
        return runtime, tm, clock, token

    runtime, tm, clock, token = asyncio.run(prepare())
    with TestClient(create_app(runtime)) as client:
        queries = tm.query_calls
        response = client.get(f"/r/{token}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert 'data-page="report"' in response.text
        assert "<script>alert" not in response.text
        assert "&lt;script&gt;" in response.text
        assert 'aria-label="行驶里程趋势' in response.text
        assert tm.query_calls == queries
        assert client.get(f"/d/{token}").status_code == 404
        assert client.get(f"/c/{token}").status_code == 404
        assert client.get("/r/unknown-token").text == "未找到"
        assert client.get("/r").status_code == 404
        clock.advance(timedelta(days=2))
        assert client.get(f"/r/{token}").status_code == 404
    assert token not in redact_text(f"GET /r/{token}", [], redact_tokens=True)


def test_manual_generation_reuses_snapshot_and_rejects_open_period(tmp_path):
    async def scenario():
        runtime, tm, bark, clock = await boot(tmp_path, clock=FakeClock(local(2026, 11, 2, 12)))
        period = period_for("month", date(2026, 10, 1))
        token = runtime.reports.generate(period, clock.now())
        count = tm.query_calls
        assert runtime.reports.generate(period, clock.now(), enqueue=True) == token
        assert tm.query_calls == count
        await runtime.on_timer()
        assert len(bark.payloads) == 1
        runtime.reports.generate(period, clock.now(), enqueue=True)
        await runtime.on_timer()
        assert len(bark.payloads) == 1
        with pytest.raises(ValueError, match="已经结束"):
            runtime.reports.generate(period_for("month", date(2026, 11, 1)), clock.now())

    asyncio.run(scenario())


def test_reports_can_be_disabled_and_hour_is_validated(tmp_path):
    async def scenario():
        runtime, tm, bark, clock = await boot(tmp_path, reports_enabled=False)
        queries = tm.query_calls
        clock.advance(timedelta(days=400))
        await runtime.on_reports()
        assert bark.payloads == []
        assert tm.query_calls == queries

    asyncio.run(scenario())
    with pytest.raises(ConfigError, match="REPORT_HOUR"):
        make_settings(tmp_path, report_hour=24)


def test_report_schedule_next_deadline_and_no_work_between_periods(tmp_path):
    settings = make_settings(tmp_path)
    store = Store(settings.sqlite_path)
    store.init()
    tm = MemoryTeslaMate()
    service = ReportService(settings, store, tm)
    now = local(2026, 10, 31, 12)
    service.initialize(now)
    service.generate_due(now)
    assert tm.query_calls == 0
    assert service.next_due(now) == local(2026, 11, 1, 9)
    assert service.next_due(local(2026, 11, 1, 9)) == local(2026, 11, 2, 9)


def test_report_snapshot_link_outbox_cursor_commit_atomically(tmp_path):
    store = Store(str(tmp_path / "atomic.sqlite"))
    store.init()
    period = period_for("month", date(2026, 10, 1))
    now = local(2026, 11, 1, 9)
    with pytest.raises(ValueError):
        store.save_report(
            token="failed-report-token", car_id=1, kind=period.kind, start=period.start,
            end=period.end, snapshot={"bad": float("nan")}, created_at=now,
            expires_at=None, enqueue=True, cursor_key="test_cursor",
        )
    assert store.get_link("failed-report-token") is None
    assert store.get_report("failed-report-token") is None
    assert store.get_meta("test_cursor") is None
    assert store.due_outbox(now) == []


def test_manual_cli_outputs_private_link_and_queues_only_when_requested(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    tm = MemoryTeslaMate()
    monkeypatch.setattr(report_command.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(report_command, "PostgresTeslaMate", lambda _url: tm)
    monkeypatch.setattr("sys.argv", ["app.report", "month", "2025-09-15"])
    report_command.main()
    url = capsys.readouterr().out.strip()
    assert url.startswith(settings.public_base_url + "/r/")
    store = Store(settings.sqlite_path)
    store.init()
    assert store.next_outbox_at() is None
    monkeypatch.setattr("sys.argv", ["app.report", "month", "2025-09-15", "--push"])
    report_command.main()
    assert capsys.readouterr().out.strip() == url
    assert store.next_outbox_at() is not None
    monkeypatch.setattr("sys.argv", ["app.report", "year", "2099-01-01"])
    with pytest.raises(SystemExit) as exc:
        report_command.main()
    assert exc.value.code == 2
