"""本地 SQLite：水位、待决出门、链接、Bark 发件箱。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime

from app.models import Link, OutboxItem, PendingCharge, PendingOuting

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS pending_outing (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    phase TEXT NOT NULL,
    armed_at TEXT,
    deadline_at TEXT,
    next_check_at TEXT,
    attempts_done INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_charge (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    armed_at TEXT,
    next_check_at TEXT,
    attempts_done INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS links (
    token TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    drive_ids TEXT,
    charge_id INTEGER,
    destination TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    preview INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bark_outbox (
    token TEXT PRIMARY KEY,
    next_attempt_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decided_drives (
    drive_id INTEGER PRIMARY KEY,
    token TEXT,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decided_charges (
    charge_id INTEGER PRIMARY KEY,
    token TEXT NOT NULL
);
"""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def init(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn

    def probe_writable(self) -> bool:
        try:
            conn = sqlite3.connect(self.path, timeout=3)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('__health_probe', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1'"
            )
            conn.execute("ROLLBACK")
            conn.close()
            return True
        except sqlite3.Error:
            return False

    def get_meta(self, key: str) -> str | None:
        row = self._read("SELECT value FROM meta WHERE key = ?", (key,))
        if row is None:
            return None
        return row["value"]

    def set_meta(self, key: str, value: str) -> None:
        def write() -> None:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

        self._write(write)

    def get_pending_outing(self) -> PendingOuting | None:
        row = self._read("SELECT * FROM pending_outing WHERE id = 1")
        if row is None:
            return None
        return PendingOuting(
            phase=row["phase"],
            armed_at=_parse(row["armed_at"]),
            deadline_at=_parse(row["deadline_at"]),
            next_check_at=_parse(row["next_check_at"]),
            attempts_done=int(row["attempts_done"]),
        )

    def set_pending_outing(
        self,
        *,
        phase: str,
        armed_at: datetime | None,
        deadline_at: datetime | None,
        next_check_at: datetime | None,
        attempts_done: int,
    ) -> None:
        def write() -> None:
            self._conn.execute("DELETE FROM pending_outing")
            self._conn.execute(
                """
                INSERT INTO pending_outing(id, phase, armed_at, deadline_at, next_check_at, attempts_done)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (phase, _iso(armed_at), _iso(deadline_at), _iso(next_check_at), attempts_done),
            )

        self._write(write)

    def clear_pending_outing(self) -> None:
        self._write(lambda: self._conn.execute("DELETE FROM pending_outing"))

    def get_pending_charge(self) -> PendingCharge | None:
        row = self._read("SELECT * FROM pending_charge WHERE id = 1")
        if row is None:
            return None
        return PendingCharge(
            armed_at=_parse(row["armed_at"]),
            next_check_at=_parse(row["next_check_at"]),
            attempts_done=int(row["attempts_done"]),
        )

    def set_pending_charge(
        self,
        *,
        armed_at: datetime | None,
        next_check_at: datetime | None,
        attempts_done: int,
    ) -> None:
        def write() -> None:
            self._conn.execute("DELETE FROM pending_charge")
            self._conn.execute(
                """
                INSERT INTO pending_charge(id, armed_at, next_check_at, attempts_done)
                VALUES (1, ?, ?, ?)
                """,
                (_iso(armed_at), _iso(next_check_at), attempts_done),
            )

        self._write(write)

    def clear_pending_charge(self) -> None:
        self._write(lambda: self._conn.execute("DELETE FROM pending_charge"))

    def insert_link(
        self,
        *,
        token: str,
        kind: str,
        drive_ids: list[int] | None,
        charge_id: int | None,
        destination: str | None,
        created_at: datetime,
        expires_at: datetime | None,
        preview: bool,
    ) -> None:
        def write() -> None:
            self._conn.execute(
                """
                INSERT INTO links(
                    token, kind, drive_ids, charge_id, destination, created_at, expires_at, preview
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    kind,
                    json.dumps(drive_ids or []),
                    charge_id,
                    destination,
                    _iso(created_at),
                    _iso(expires_at),
                    1 if preview else 0,
                ),
            )

        self._write(write)

    def get_link(self, token: str) -> Link | None:
        row = self._read("SELECT * FROM links WHERE token = ?", (token,))
        if row is None:
            return None
        return self._link(row)

    def find_drive_link(self, drive_ids: list[int]) -> Link | None:
        wanted = sorted(drive_ids)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM links WHERE kind = 'drive' AND preview = 0"
            ).fetchall()
        for row in rows:
            ids = sorted(json.loads(row["drive_ids"] or "[]"))
            if ids == wanted:
                return self._link(row)
        return None

    def find_charge_link(self, charge_id: int) -> Link | None:
        row = self._read(
            "SELECT * FROM links WHERE kind = 'charge' AND charge_id = ? AND preview = 0",
            (charge_id,),
        )
        if row is None:
            return None
        return self._link(row)

    def ensure_outbox(self, token: str, when: datetime) -> None:
        def write() -> None:
            existing = self._conn.execute(
                "SELECT token FROM bark_outbox WHERE token = ?",
                (token,),
            ).fetchone()
            if existing is not None:
                return
            self._conn.execute(
                """
                INSERT INTO bark_outbox(token, next_attempt_at, attempts, created_at)
                VALUES (?, ?, 0, ?)
                """,
                (token, _iso(when), _iso(when)),
            )

        self._write(write)

    def due_outbox(self, now: datetime) -> list[OutboxItem]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM bark_outbox ORDER BY next_attempt_at ASC"
            ).fetchall()
        items = [
            OutboxItem(
                token=row["token"],
                next_attempt_at=_parse(row["next_attempt_at"]),
                attempts=int(row["attempts"]),
            )
            for row in rows
        ]
        return [item for item in items if item.next_attempt_at is not None and item.next_attempt_at <= now]

    def schedule_outbox(self, token: str, when: datetime, attempts: int) -> None:
        def write() -> None:
            self._conn.execute(
                "UPDATE bark_outbox SET next_attempt_at = ?, attempts = ? WHERE token = ?",
                (_iso(when), attempts, token),
            )

        self._write(write)

    def delete_outbox(self, token: str) -> None:
        self._write(lambda: self._conn.execute("DELETE FROM bark_outbox WHERE token = ?", (token,)))

    def next_outbox_at(self) -> datetime | None:
        row = self._read("SELECT MIN(next_attempt_at) AS next_at FROM bark_outbox")
        if row is None:
            return None
        return _parse(row["next_at"])

    def mark_drives(self, drive_ids: list[int], token: str | None, reason: str) -> None:
        def write() -> None:
            for drive_id in drive_ids:
                self._conn.execute(
                    """
                    INSERT INTO decided_drives(drive_id, token, reason) VALUES (?, ?, ?)
                    ON CONFLICT(drive_id) DO UPDATE SET token = excluded.token, reason = excluded.reason
                    """,
                    (drive_id, token, reason),
                )

        self._write(write)

    def decided_drive_ids(self) -> set[int]:
        with self._lock:
            rows = self._conn.execute("SELECT drive_id FROM decided_drives").fetchall()
        return {int(row["drive_id"]) for row in rows}

    def drives_decided(self, drive_ids: list[int]) -> bool:
        decided = self.decided_drive_ids()
        return all(drive_id in decided for drive_id in drive_ids)

    def mark_charge(self, charge_id: int, token: str) -> None:
        def write() -> None:
            self._conn.execute(
                """
                INSERT INTO decided_charges(charge_id, token) VALUES (?, ?)
                ON CONFLICT(charge_id) DO UPDATE SET token = excluded.token
                """,
                (charge_id, token),
            )

        self._write(write)

    def charge_decided(self, charge_id: int) -> bool:
        row = self._read("SELECT charge_id FROM decided_charges WHERE charge_id = ?", (charge_id,))
        return row is not None

    def decided_charge_ids(self) -> set[int]:
        with self._lock:
            rows = self._conn.execute("SELECT charge_id FROM decided_charges").fetchall()
        return {int(row["charge_id"]) for row in rows}

    def _link(self, row: sqlite3.Row) -> Link:
        return Link(
            token=row["token"],
            kind=row["kind"],
            drive_ids=json.loads(row["drive_ids"] or "[]"),
            charge_id=row["charge_id"],
            destination=row["destination"] or None,
            created_at=_parse(row["created_at"]),
            expires_at=_parse(row["expires_at"]),
            preview=bool(row["preview"]),
        )

    def _read(self, sql: str, params: tuple = ()):
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _write(self, fn) -> None:
        with self._lock:
            try:
                fn()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
