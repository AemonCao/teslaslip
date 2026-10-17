import sqlite3
from pathlib import Path

import psycopg
import pytest

from app.teslamate import PostgresTeslaMate, ReadOnlyError, assert_select_only


def test_write_statements_fail_before_connect(monkeypatch):
    calls = []

    def refuse(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("写入语句不应连接数据库")

    monkeypatch.setattr(psycopg, "connect", refuse)
    reader = PostgresTeslaMate("postgresql://teslaslip_ro:db-secret-value@127.0.0.1/teslamate")
    with pytest.raises(ReadOnlyError):
        reader._fetch("INSERT INTO drives (id) VALUES (1)", {})
    with pytest.raises(ReadOnlyError):
        reader._fetch("UPDATE drives SET distance = 1", {})
    with pytest.raises(ReadOnlyError):
        assert_select_only("DELETE FROM drives")
    assert calls == []


def test_readonly_connection_rejects_insert(tmp_path: Path):
    path = tmp_path / "ro.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    ro = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")


def test_source_does_not_create_postgres_role():
    root = Path(__file__).resolve().parents[1] / "app"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "CREATE ROLE" not in text.upper()
    assert "teslamate-db" not in text
