import asyncio
import re
import sqlite3
from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE, digest_token, password_hash, safe_next, verify_password
from app.config import ConfigError
from app.main import create_app
from app.reports import period_for
from app.teslamate import HISTORY_CHARGES_SQL, HISTORY_DRIVES_SQL, PostgresTeslaMate
from tests.fakes import T0, boot, make_charge, make_drive, make_settings
from tests.test_sql import SCHEMA, fetch

PASSWORD = "test-private-password-2026"
PASSWORD_HASH = password_hash(PASSWORD)


def setup(tmp_path, **overrides):
    return asyncio.run(boot(tmp_path, admin_password_hash=PASSWORD_HASH, **overrides))


def csrf_from(response):
    return re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)


def login(client, target="/", password=PASSWORD):
    form = client.get("/login", params={"next": target})
    return client.post("/login", data={
        "username": "admin", "password": password, "csrf": csrf_from(form), "next": target,
    }, follow_redirects=False)


def insert_link(runtime, kind, token, *, expired=False):
    runtime.store.insert_link(
        token=token, kind=kind, drive_ids=[1] if kind == "drive" else None,
        charge_id=1 if kind == "charge" else None, destination=None,
        created_at=T0 - timedelta(days=2), expires_at=T0 - timedelta(days=1) if expired else T0 + timedelta(days=1),
        preview=False,
    )


def test_password_hash_config_and_redirection_validation(tmp_path):
    assert verify_password(PASSWORD, PASSWORD_HASH)
    assert not verify_password("wrong-password", PASSWORD_HASH)
    assert not verify_password("x" * 1025, PASSWORD_HASH)
    assert password_hash(PASSWORD) != PASSWORD_HASH
    with pytest.raises(ValueError):
        password_hash("short")
    with pytest.raises(ConfigError, match="ADMIN_PASSWORD_HASH"):
        make_settings(tmp_path, admin_password_hash="plain-password")
    for target in ("https://example.com", "//example.com", "/\\example.com", "/%2fexample.com", "/login", "/\nexample.com", "//[bad", "/" + "a" * 600):
        assert safe_next(target) == "/"
    assert safe_next("/r/example") == "/r/example"
    assert safe_next("/?tab=charges&page=2") == "/?tab=charges&page=2"


def test_unconfigured_portal_fails_closed_and_shares_stay_public(tmp_path):
    runtime, tm, _, _ = asyncio.run(boot(tmp_path))
    tm.drives.append(make_drive(1, T0 - timedelta(minutes=5), T0))
    insert_link(runtime, "drive", "public-share")
    with TestClient(create_app(runtime)) as client:
        assert client.get("/").status_code == 503
        assert client.get("/history/drives/1").status_code == 503
        assert client.get("/d/public-share").status_code == 200
        assert client.get("/d/unknown").text == "未找到"


def test_login_csrf_cookie_flags_and_logout_revocation(tmp_path):
    runtime, _, _, _ = setup(tmp_path)
    with TestClient(create_app(runtime)) as client:
        redirect = client.get("/?tab=charges", follow_redirects=False)
        assert redirect.status_code == 303 and redirect.headers["location"].startswith("/login?")
        assert client.post("/login", data={"password": PASSWORD}).status_code == 403
        assert login(client, password="incorrect-password").status_code == 401
        response = login(client, target="/?tab=charges")
        assert response.status_code == 303 and response.headers["location"] == "/?tab=charges"
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=604800" in cookie
        session = client.cookies.get(SESSION_COOKIE)
        row = runtime.store._read("SELECT token_hash FROM web_sessions")
        assert row["token_hash"] == digest_token(session) and session not in row["token_hash"]
        overview = client.get("/")
        assert overview.status_code == 200 and "历史总览" in overview.text
        assert overview.headers["cache-control"] == "no-store"
        assert "cookie" in overview.headers["vary"].lower()
        assert client.post("/logout", data={"csrf": "错误"}).status_code == 403
        assert client.post("/logout", data={"csrf": csrf_from(overview)}, follow_redirects=False).status_code == 303
        client.cookies.set(SESSION_COOKIE, session)
        assert client.get("/", follow_redirects=False).status_code == 303


def test_https_cookie_is_secure(tmp_path):
    runtime, _, _, _ = setup(tmp_path, public_base_url="https://testserver")
    with TestClient(create_app(runtime), base_url="https://testserver") as client:
        assert "Secure" in login(client).headers["set-cookie"]
        assert client.get("/").status_code == 200


def test_login_throttling_is_persistent_and_recovers(tmp_path):
    runtime, _, _, clock = setup(tmp_path)
    with TestClient(create_app(runtime)) as client:
        for _ in range(10):
            assert login(client, password="wrong-password").status_code == 401
        runtime.store.init()
        blocked = login(client)
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == "300"
        clock.advance(timedelta(minutes=5))
        assert login(client).status_code == 303


def test_session_survives_store_reopen_but_not_expiry_or_password_change(tmp_path):
    runtime, _, _, clock = setup(tmp_path)
    with TestClient(create_app(runtime)) as client:
        login(client)
        runtime.store.init()
        assert client.get("/").status_code == 200
        clock.advance(timedelta(days=7))
        assert client.get("/", follow_redirects=False).status_code == 303
        login(client)
        runtime.settings = replace(runtime.settings, admin_password_hash=password_hash("changed-password-2026"))
        with TestClient(create_app(runtime)) as changed:
            changed.cookies.set(SESSION_COOKIE, client.cookies.get(SESSION_COOKIE))
            assert changed.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("kind,prefix", [("drive", "d"), ("charge", "c"), ("report", "r")])
def test_shared_page_expiry_only_applies_to_anonymous_visitors(tmp_path, kind, prefix):
    runtime, tm, _, clock = setup(tmp_path, trip_link_ttl_days=1)
    tm.drives.append(make_drive(1, T0 - timedelta(minutes=5), T0))
    tm.charges.append(make_charge(1, T0 - timedelta(hours=1), T0))
    if kind == "report":
        token = runtime.reports.generate(period_for("month", date(2026, 9, 1)), clock.now())
    else:
        token = "shared-item"
        insert_link(runtime, kind, token)
    with TestClient(create_app(runtime)) as client:
        assert client.get(f"/{prefix}/{token}").status_code == 200
        clock.advance(timedelta(days=2))
        expired = client.get(f"/{prefix}/{token}")
        assert expired.status_code == 404 and "登录后查看" in expired.text
        queries = tm.query_calls
        assert client.get(f"/{prefix}/{token}").status_code == 404
        assert tm.query_calls == queries
        assert login(client, target=f"/{prefix}/{token}").headers["location"] == f"/{prefix}/{token}"
        assert client.get(f"/{prefix}/{token}").status_code == 200
        assert client.get(f"/{prefix}/unknown").status_code == 404
        csrf = csrf_from(client.get("/"))
        client.post("/logout", data={"csrf": csrf})
        assert client.get(f"/{prefix}/{token}").status_code == 404


def test_history_queries_are_authenticated_paginated_and_do_not_create_shares(tmp_path):
    runtime, tm, bark, _ = setup(tmp_path)
    tm.drives.extend(make_drive(index, T0 - timedelta(minutes=5), T0) for index in range(1, 23))
    tm.charges.append(make_charge(1, T0 - timedelta(hours=1), T0))
    with TestClient(create_app(runtime)) as client:
        queries = tm.query_calls
        assert client.get("/?tab=drives", follow_redirects=False).status_code == 303
        assert client.get("/history/drives/22", follow_redirects=False).status_code == 303
        assert tm.query_calls == queries
        login(client)
        count = runtime.store._read("SELECT COUNT(*) AS total FROM links")["total"]
        first = client.get("/?tab=drives&year=2026&start=2026-10-17&end=2026-10-17")
        assert first.status_code == 200 and first.text.count('class="card portal-record"') == 20
        assert first.text.index('href="/history/drives/22"') < first.text.index('href="/history/drives/21"')
        second = client.get("/?tab=drives&page=2")
        assert second.text.count('class="card portal-record"') == 2
        assert client.get("/?tab=charges").text.count('class="card portal-record"') == 1
        assert "暂无记录" in client.get("/?tab=drives&year=2025").text
        for query in ("page=0", "page=10001", "tab=invalid", "start=bad", "start=2027-01-01&end=2026-01-01", "year=9999"):
            assert client.get("/?" + query).status_code == 400
        assert tm.track_calls == 0
        assert runtime.store._read("SELECT COUNT(*) AS total FROM links")["total"] == count
        assert bark.payloads == []


def test_report_list_filters_generated_snapshots_without_teslamate_queries(tmp_path):
    runtime, tm, _, clock = setup(tmp_path)
    for kind, day in (("week", date(2026, 9, 14)), ("month", date(2026, 9, 1)), ("year", date(2025, 1, 1))):
        runtime.reports.generate(period_for(kind, day), clock.now())
    with TestClient(create_app(runtime)) as client:
        login(client)
        queries = tm.query_calls
        assert client.get("/").text.count('class="card portal-record"') == 3
        monthly = client.get("/?kind=month&year=2026")
        assert monthly.text.count('class="card portal-record"') == 1 and "2026 年 9 月用车月报" in monthly.text
        assert "暂无记录" in client.get("/?kind=year&year=2026").text
        assert tm.query_calls == queries


def test_historical_detail_and_explicit_share_do_not_change_notification_state(tmp_path):
    runtime, tm, bark, clock = setup(tmp_path, trip_link_ttl_days=1)
    tm.drives.append(make_drive(1, T0 - timedelta(minutes=5), T0))
    tm.charges.append(make_charge(1, T0 - timedelta(hours=1), T0))
    with TestClient(create_app(runtime)) as client:
        login(client)
        for kind, prefix in (("drives", "d"), ("charges", "c")):
            detail = client.get(f"/history/{kind}/1")
            assert detail.status_code == 200 and "生成分享链接" in detail.text
            assert client.post(f"/history/{kind}/1/share", data={"csrf": "wrong"}).status_code == 403
            response = client.post(f"/history/{kind}/1/share", data={"csrf": csrf_from(detail)}, follow_redirects=False)
            assert response.status_code == 303
            share_url = response.headers["location"]
            assert share_url.startswith(f"/{prefix}/")
            with TestClient(create_app(runtime)) as anonymous:
                shared = anonymous.get(share_url)
                assert shared.status_code == 200 and "生成分享链接" not in shared.text
                assert anonymous.get("/history/charges/1", follow_redirects=False).status_code == 303
                clock.advance(timedelta(days=2))
                assert anonymous.get(share_url).status_code == 404
            assert client.get(share_url).status_code == 200
        assert client.get("/history/drives/999").status_code == 404
        assert bark.payloads == []
        assert runtime.store.decided_drive_ids() == set()
        assert runtime.store.decided_charge_ids() == set()


def test_cross_origin_login_bad_csrf_and_oversized_forms_are_rejected(tmp_path):
    runtime, _, _, _ = setup(tmp_path)
    with TestClient(create_app(runtime)) as client:
        form = client.get("/login")
        values = {"username": "admin", "password": PASSWORD, "csrf": csrf_from(form)}
        assert client.post("/login", data=values, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/login", data={**values, "csrf": "无效"}).status_code == 403
        assert client.post("/login", data={**values, "password": "x" * 9000}).status_code == 413
        assert client.post("/login", json=values).status_code == 415
        assert client.post("/login", content="csrf=a&csrf=b", headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 400


def test_history_sql_filters_car_dates_and_ended_records_with_stable_pagination():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    for record_id, car_id, end in ((1, 1, "2026-10-17T02:00:00"), (2, 1, "2026-10-17T02:00:00"), (3, 2, "2026-10-17T02:00:00"), (4, 1, None), (5, 1, "2026-10-18T00:00:00")):
        for table in ("drives", "charging_processes"):
            conn.execute(f"INSERT INTO {table}(id, car_id, start_date, end_date) VALUES (?, ?, '2026-10-17T00:00:00', ?)", (record_id, car_id, end))
    params = {"car_id": 1, "start_date": "2026-10-17T00:00:00", "end_date": "2026-10-18T00:00:00", "limit": 1, "offset": 0}
    for sql in (HISTORY_DRIVES_SQL, HISTORY_CHARGES_SQL):
        assert [row["id"] for row in fetch(conn, sql, params)] == [2]
        assert [row["id"] for row in fetch(conn, sql, {**params, "offset": 1})] == [1]


def test_postgres_history_mapping_does_not_fetch_tracks_or_charge_samples(monkeypatch):
    reader = PostgresTeslaMate("unused")
    calls = []

    def query(sql, params):
        calls.append((sql, params))
        return [{"id": 1, "start_date": T0, "end_date": T0, "charge_energy_added": 10}]

    monkeypatch.setattr(reader, "_fetch", query)
    for kind in ("drives", "charges"):
        records = reader.history_records(kind, 1, T0, T0 + timedelta(days=1), 21, 0)
        assert records[0].id == 1
    assert len(calls) == 2 and reader.track_calls == 0
    assert calls[0][1]["start_date"].tzinfo is None
    with pytest.raises(ValueError):
        reader.history_records("drives", 1, T0, T0, 500, 0)


@pytest.mark.parametrize("kind,prefix", [("drive", "d"), ("charge", "c"), ("report", "r")])
def test_all_notification_types_include_private_overview_without_changing_detail_url(tmp_path, kind, prefix):
    runtime, tm, _, clock = setup(tmp_path, public_base_url="https://example.test/")
    tm.drives.append(make_drive(1, T0 - timedelta(minutes=5), T0))
    tm.charges.append(make_charge(1, T0 - timedelta(hours=1), T0))
    if kind == "report":
        token = runtime.reports.generate(period_for("month", date(2026, 9, 1)), clock.now())
    else:
        token = "notification-item"
        insert_link(runtime, kind, token)
    payload = runtime._payload_for(runtime.store.get_link(token))
    assert payload["url"] == f"https://example.test/{prefix}/{token}"
    assert f"[查看详情](https://example.test/{prefix}/{token})" in payload["markdown"]
    assert "[历史总览](https://example.test/)" in payload["markdown"]
    assert payload["body"].endswith("\n历史总览：https://example.test/")
    with TestClient(create_app(runtime)) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"].startswith("/login?")
