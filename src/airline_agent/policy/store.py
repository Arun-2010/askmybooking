"""SQLite snapshot store: TTL cache, content-hash diffing, policy-change events."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airline_agent.schemas import PolicySnapshot

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id   TEXT PRIMARY KEY,
    airline       TEXT NOT NULL,
    topic         TEXT NOT NULL,
    url           TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    text          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap_url ON snapshots(airline, topic, url, fetched_at);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,      -- policy_changed | fetch_failed | fetch_ok | cache_hit
    airline     TEXT,
    topic       TEXT,
    url         TEXT,
    detail      TEXT
);
"""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SnapshotStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- snapshots -------------------------------------------------------
    def latest(self, airline: str, topic: str, url: str) -> PolicySnapshot | None:
        row = self.conn.execute(
            "SELECT * FROM snapshots WHERE airline=? AND topic=? AND url=? ORDER BY fetched_at DESC LIMIT 1",
            (airline, topic, url),
        ).fetchone()
        return self._row(row, from_cache=True) if row else None

    def get(self, snapshot_id: str) -> PolicySnapshot | None:
        row = self.conn.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        return self._row(row, from_cache=True) if row else None

    def is_fresh(self, snap: PolicySnapshot, ttl_hours: float, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return now - snap.fetched_at < timedelta(hours=ttl_hours)

    def save(self, airline: str, topic: str, url: str, text: str, fetched_at: datetime | None = None) -> PolicySnapshot:
        """Store a freshly fetched page. Logs a policy_changed event if the hash differs."""
        fetched_at = fetched_at or utcnow()
        h = sha256(text)
        prev = self.latest(airline, topic, url)
        if prev and prev.content_hash != h:
            self.log_event(
                "policy_changed", airline, topic, url,
                json.dumps({"previous_snapshot": prev.snapshot_id, "previous_hash": prev.content_hash, "new_hash": h}),
            )
            log.warning("policy changed: %s %s %s", airline, topic, url)
        snap = PolicySnapshot(
            snapshot_id=f"snap_{fetched_at:%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}",
            airline=airline, topic=topic, url=url, fetched_at=fetched_at,
            content_hash=h, text=text, from_cache=False,
        )
        self.insert(snap)
        return snap

    def insert(self, snap: PolicySnapshot) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?)",
            (snap.snapshot_id, snap.airline, snap.topic, snap.url, snap.fetched_at.isoformat(), snap.content_hash, snap.text),
        )
        self.conn.commit()

    # --- events ----------------------------------------------------------
    def log_event(self, kind: str, airline: str | None, topic: str | None, url: str | None, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events (at, kind, airline, topic, url, detail) VALUES (?,?,?,?,?,?)",
            (utcnow().isoformat(), kind, airline, topic, url, detail),
        )
        self.conn.commit()

    def events(self, kind: str | None = None) -> list[dict]:
        q = "SELECT * FROM events" + (" WHERE kind=?" if kind else "") + " ORDER BY id"
        rows = self.conn.execute(q, (kind,) if kind else ()).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row, from_cache: bool) -> PolicySnapshot:
        return PolicySnapshot(
            snapshot_id=row["snapshot_id"], airline=row["airline"], topic=row["topic"], url=row["url"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]), content_hash=row["content_hash"],
            text=row["text"], from_cache=from_cache,
        )
