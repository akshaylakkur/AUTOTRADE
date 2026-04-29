"""SQLite-backed research storage for the Metamind to learn from."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResearchTask:
    """A research task descriptor."""

    query: str
    sources: list[str] = field(default_factory=list)
    budget: float = 0.0
    deadline: datetime | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class StoredResult:
    """A stored research result."""

    id: int
    query: str
    summary: str
    confidence: float
    opportunity_score: float
    domain: str
    data: dict[str, Any]
    timestamp: datetime


class ResearchStore:
    """SQLite research database with WAL mode.

    Stores research tasks, raw results, and sources so the Metamind
    can query historical intelligence and learn from past discoveries.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS research_tasks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        query       TEXT    NOT NULL,
        sources     TEXT    DEFAULT '[]',
        budget      REAL    DEFAULT 0.0,
        deadline    TEXT,
        timestamp   TEXT    NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_query ON research_tasks(query);
    CREATE INDEX IF NOT EXISTS idx_tasks_time  ON research_tasks(timestamp);

    CREATE TABLE IF NOT EXISTS research_results (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id             INTEGER REFERENCES research_tasks(id),
        query               TEXT    NOT NULL,
        summary             TEXT    DEFAULT '',
        confidence          REAL    DEFAULT 0.0,
        opportunity_score   REAL    DEFAULT 0.0,
        domain              TEXT    DEFAULT '',
        data                TEXT    DEFAULT '{}',
        timestamp           TEXT    NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_results_domain ON research_results(domain);
    CREATE INDEX IF NOT EXISTS idx_results_opp    ON research_results(opportunity_score);
    CREATE INDEX IF NOT EXISTS idx_results_time    ON research_results(timestamp);

    CREATE TABLE IF NOT EXISTS research_sources (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        result_id   INTEGER REFERENCES research_results(id),
        url         TEXT    NOT NULL,
        title       TEXT    DEFAULT '',
        credibility REAL    DEFAULT 0.0,
        summary     TEXT    DEFAULT '',
        timestamp   TEXT    NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sources_url ON research_sources(url);
    """

    def __init__(self, db_path: str | Path = "data/research.db") -> None:
        self._db_path = str(db_path)
        self._local = threading.local()
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _ensure_schema(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(self._DDL)
            conn.commit()

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def save_task(self, task: ResearchTask) -> int:
        """Persist a research task and return its row id."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO research_tasks (query, sources, budget, deadline, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task.query,
                    json.dumps(task.sources),
                    task.budget,
                    task.deadline.isoformat() if task.deadline else None,
                    task.timestamp.isoformat(),
                ),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_tasks(self, limit: int = 100) -> list[ResearchTask]:
        """Return recent research tasks."""
        rows = (
            self._conn()
            .execute(
                "SELECT query, sources, budget, deadline, timestamp FROM research_tasks ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            .fetchall()
        )
        tasks: list[ResearchTask] = []
        for row in rows:
            query, sources_json, budget, deadline, timestamp = row
            tasks.append(
                ResearchTask(
                    query=query,
                    sources=json.loads(sources_json),
                    budget=budget,
                    deadline=datetime.fromisoformat(deadline) if deadline else None,
                    timestamp=datetime.fromisoformat(timestamp),
                )
            )
        return tasks

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def save_result(
        self,
        task_id: int | None,
        query: str,
        summary: str,
        confidence: float,
        opportunity_score: float,
        domain: str,
        data: dict[str, Any],
        sources: list[dict[str, Any]] | None = None,
    ) -> int:
        """Persist a research result and optional sources."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO research_results
                    (task_id, query, summary, confidence, opportunity_score, domain, data, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    query,
                    summary,
                    confidence,
                    opportunity_score,
                    domain,
                    json.dumps(data),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            result_id = cursor.lastrowid or 0

            if sources:
                for src in sources:
                    conn.execute(
                        """
                        INSERT INTO research_sources
                            (result_id, url, title, credibility, summary, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            result_id,
                            src.get("url", ""),
                            src.get("title", ""),
                            src.get("credibility", 0.0),
                            src.get("summary", ""),
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
            conn.commit()
            return result_id

    def get_results(
        self,
        domain: str | None = None,
        min_confidence: float = 0.0,
        min_opportunity_score: float = 0.0,
        limit: int = 100,
    ) -> list[StoredResult]:
        """Query stored research results with optional filters."""
        sql = """
            SELECT id, query, summary, confidence, opportunity_score, domain, data, timestamp
            FROM research_results
            WHERE confidence >= ? AND opportunity_score >= ?
        """
        params: list[Any] = [min_confidence, min_opportunity_score]
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        sql += " ORDER BY opportunity_score DESC, confidence DESC LIMIT ?"
        params.append(limit)

        rows = self._conn().execute(sql, params).fetchall()
        results: list[StoredResult] = []
        for row in rows:
            results.append(
                StoredResult(
                    id=row[0],
                    query=row[1],
                    summary=row[2],
                    confidence=row[3],
                    opportunity_score=row[4],
                    domain=row[5],
                    data=json.loads(row[6]),
                    timestamp=datetime.fromisoformat(row[7]),
                )
            )
        return results

    def get_top_opportunities(self, limit: int = 20) -> list[StoredResult]:
        """Return the highest-scoring opportunities."""
        return self.get_results(min_opportunity_score=0.5, limit=limit)
