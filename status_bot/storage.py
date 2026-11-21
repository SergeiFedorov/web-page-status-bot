"""SQLite storage for check history and bot state (survives restarts)."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from status_bot.checkers.base import PageStatus


@dataclass
class State:
    enabled: bool = True
    last_status: PageStatus | None = None  # last successful check result
    last_available_notice_at: datetime | None = None
    consecutive_errors: int = 0


@dataclass(frozen=True)
class CheckRecord:
    checked_at: datetime
    status: PageStatus | None  # None means the check failed
    error: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    status TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path | str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # --- state -------------------------------------------------------------

    def load_state(self) -> State:
        row = self._conn.execute("SELECT data FROM state WHERE id = 1").fetchone()
        if row is None:
            return State()
        data = json.loads(row[0])
        return State(
            enabled=data["enabled"],
            last_status=_status(data["last_status"]),
            last_available_notice_at=_dt(data["last_available_notice_at"]),
            consecutive_errors=data["consecutive_errors"],
        )

    def save_state(self, state: State) -> None:
        data = {
            "enabled": state.enabled,
            "last_status": state.last_status.value if state.last_status else None,
            "last_available_notice_at": (
                state.last_available_notice_at.isoformat()
                if state.last_available_notice_at
                else None
            ),
            "consecutive_errors": state.consecutive_errors,
        }
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO state (id, data) VALUES (1, ?)",
                (json.dumps(data),),
            )

    # --- history -----------------------------------------------------------

    def add_check(self, record: CheckRecord) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO checks (checked_at, status, error) VALUES (?, ?, ?)",
                (
                    record.checked_at.isoformat(),
                    record.status.value if record.status else None,
                    record.error,
                ),
            )

    def last_checks(self, limit: int = 10) -> list[CheckRecord]:
        """Most recent checks first."""
        rows = self._conn.execute(
            "SELECT checked_at, status, error FROM checks ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            CheckRecord(_dt(at), _status(status), error) for at, status, error in rows
        ]


def _status(value: str | None) -> PageStatus | None:
    return PageStatus(value) if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
