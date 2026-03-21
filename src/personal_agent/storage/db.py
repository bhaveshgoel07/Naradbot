from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Database:
    """Small SQLite helper that owns schema creation and connections."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def ensure_parent(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.ensure_parent()
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the minimal persistence schema used by the agent."""
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS processed_stories (
                    story_id INTEGER PRIMARY KEY,
                    channel_keys TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hn_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_source TEXT NOT NULL,
                    requested_by TEXT,
                    status TEXT NOT NULL,
                    story_count INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )
