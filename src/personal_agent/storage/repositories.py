from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from personal_agent.storage.db import Database


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ProcessedStoryRepository:
    """Persistence operations for deduping Hacker News stories."""

    database: Database

    def filter_unprocessed_ids(self, story_ids: list[int]) -> list[int]:
        if not story_ids:
            return []

        placeholders = ", ".join("?" for _ in story_ids)
        query = f"SELECT story_id FROM processed_stories WHERE story_id IN ({placeholders})"
        with self.database.connection() as connection:
            rows = connection.execute(query, story_ids).fetchall()

        processed_ids = {row["story_id"] for row in rows}
        return [story_id for story_id in story_ids if story_id not in processed_ids]

    def mark_processed(self, channel_membership: dict[int, list[str]]) -> None:
        if not channel_membership:
            return

        first_seen_at = utc_now_iso()
        processed_at = utc_now_iso()
        rows = [
            (story_id, ",".join(sorted(set(channel_keys))), first_seen_at, processed_at)
            for story_id, channel_keys in channel_membership.items()
        ]
        with self.database.connection() as connection:
            connection.executemany(
                """
                INSERT INTO processed_stories (story_id, channel_keys, first_seen_at, processed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(story_id) DO UPDATE SET
                    channel_keys = excluded.channel_keys,
                    processed_at = excluded.processed_at
                """,
                rows,
            )

    def processed_count(self) -> int:
        with self.database.connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM processed_stories").fetchone()
        return int(row["count"])


@dataclass(slots=True)
class HNRunRepository:
    """Persistence operations for pipeline execution metadata."""

    database: Database

    def record_run(
        self,
        *,
        trigger_source: str,
        requested_by: str | None,
        status: str,
        story_count: int,
        started_at: str,
        finished_at: str,
        details: dict[str, Any],
    ) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO hn_runs (
                    trigger_source,
                    requested_by,
                    status,
                    story_count,
                    started_at,
                    finished_at,
                    details_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trigger_source,
                    requested_by,
                    status,
                    story_count,
                    started_at,
                    finished_at,
                    json.dumps(details, sort_keys=True),
                ),
            )

    def recent_runs(self, limit: int = 5) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT trigger_source, requested_by, status, story_count, started_at, finished_at, details_json
                FROM hn_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            {
                "trigger_source": row["trigger_source"],
                "requested_by": row["requested_by"],
                "status": row["status"],
                "story_count": row["story_count"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]
