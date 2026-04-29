"""Persistent consciousness system for ÆON — memory, learning, and self-narrative.

SQLite-backed storage that survives crashes and restarts. Tracks every decision,
action, outcome, and strategic insight so the cortex can learn from experience.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Memory:
    """A single recorded event or observation."""

    id: int
    timestamp: datetime
    event_type: str
    payload: dict[str, Any]
    importance: float  # 0.0 (trivial) to 1.0 (critical)
    epoch: int  # monotonic counter for ordering


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """A decision and its eventual outcome."""

    id: int
    timestamp: datetime
    action: str
    strategy: str
    expected_roi: float
    confidence: float
    risk_score: float
    budget: float
    outcome: str | None  # "success", "failure", "partial", None if pending
    actual_return: float | None
    resolved_at: datetime | None
    notes: str


@dataclass(frozen=True, slots=True)
class StrategyStats:
    """Aggregated performance metrics for a strategy."""

    strategy_name: str
    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    avg_roi: float
    win_rate: float
    avg_risk: float
    last_updated: datetime


# ---------------------------------------------------------------------------
# Consciousness
# ---------------------------------------------------------------------------


class Consciousness:
    """Persistent memory and learning system for the ÆON agent.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Created if it doesn't exist.
    max_memories:
        Soft cap on stored memories. Older low-importance memories are
        pruned when exceeded.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS memories (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp   TEXT    NOT NULL,
        event_type  TEXT    NOT NULL,
        payload     TEXT    NOT NULL DEFAULT '{}',
        importance  REAL    NOT NULL DEFAULT 0.5,
        epoch       INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(event_type);
    CREATE INDEX IF NOT EXISTS idx_mem_time ON memories(timestamp);
    CREATE INDEX IF NOT EXISTS idx_mem_importance ON memories(importance);

    CREATE TABLE IF NOT EXISTS decisions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp     TEXT    NOT NULL,
        action        TEXT    NOT NULL,
        strategy      TEXT    NOT NULL DEFAULT '',
        expected_roi  REAL    NOT NULL DEFAULT 0.0,
        confidence    REAL    NOT NULL DEFAULT 0.0,
        risk_score    REAL    NOT NULL DEFAULT 0.0,
        budget        REAL    NOT NULL DEFAULT 0.0,
        outcome       TEXT,
        actual_return REAL,
        resolved_at   TEXT,
        notes         TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_dec_strategy ON decisions(strategy);
    CREATE INDEX IF NOT EXISTS idx_dec_outcome ON decisions(outcome);

    CREATE TABLE IF NOT EXISTS strategy_performance (
        strategy_name TEXT PRIMARY KEY,
        total_trades  INTEGER NOT NULL DEFAULT 0,
        wins          INTEGER NOT NULL DEFAULT 0,
        losses        INTEGER NOT NULL DEFAULT 0,
        total_pnl     REAL    NOT NULL DEFAULT 0.0,
        avg_roi       REAL    NOT NULL DEFAULT 0.0,
        win_rate      REAL    NOT NULL DEFAULT 0.0,
        avg_risk      REAL    NOT NULL DEFAULT 0.0,
        last_updated  TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS learnings (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp   TEXT    NOT NULL,
        insight     TEXT    NOT NULL,
        domain      TEXT    NOT NULL DEFAULT 'general',
        confidence  REAL    NOT NULL DEFAULT 0.5,
        source      TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_learn_domain ON learnings(domain);

    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """

    def __init__(
        self,
        db_path: str | Path = "data/consciousness.db",
        max_memories: int = 100_000,
    ) -> None:
        self._db_path = str(db_path)
        self._max_memories = max_memories
        self._local = threading.local()
        self._epoch = 0
        self._ensure_schema()
        self._load_epoch()

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _ensure_schema(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(self._DDL)
            conn.commit()

    def _load_epoch(self) -> None:
        row = self._conn().execute(
            "SELECT value FROM meta WHERE key='epoch'"
        ).fetchone()
        if row:
            self._epoch = int(row[0])

    def _bump_epoch(self) -> int:
        self._epoch += 1
        self._conn().execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('epoch', ?)",
            (str(self._epoch),),
        ).connection.commit()
        return self._epoch

    # ------------------------------------------------------------------ #
    # Memory
    # ------------------------------------------------------------------ #

    def remember(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> Memory:
        """Record an event in consciousness.

        Args:
            event_type: Category label (e.g. ``"trade_executed"``, ``"balance_changed"``).
            payload: Arbitrary JSON-serialisable context.
            importance: 0.0 (noise) to 1.0 (life-critical).
        """
        epoch = self._bump_epoch()
        ts = datetime.now(timezone.utc).isoformat()
        data = json.dumps(payload or {})

        cur = self._conn().execute(
            """INSERT INTO memories (timestamp, event_type, payload, importance, epoch)
               VALUES (?, ?, ?, ?, ?)""",
            (ts, event_type, data, importance, epoch),
        )
        cur.connection.commit()

        memory = Memory(
            id=cur.lastrowid,
            timestamp=datetime.fromisoformat(ts),
            event_type=event_type,
            payload=payload or {},
            importance=importance,
            epoch=epoch,
        )

        self._maybe_prune()
        return memory

    def recall(
        self,
        limit: int = 50,
        event_type: str | None = None,
        min_importance: float = 0.0,
        since: datetime | None = None,
    ) -> list[Memory]:
        """Retrieve recent memories, newest first.

        Args:
            limit: Max records to return.
            event_type: Optional filter by event category.
            min_importance: Only return memories at or above this importance.
            since: Only return memories newer than this timestamp.
        """
        clauses = ["1=1"]
        params: list[Any] = []

        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if min_importance > 0:
            clauses.append("importance >= ?")
            params.append(min_importance)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())

        where = " AND ".join(clauses)
        rows = self._conn().execute(
            f"""SELECT id, timestamp, event_type, payload, importance, epoch
                FROM memories
                WHERE {where}
                ORDER BY epoch DESC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()

        return [
            Memory(
                id=r["id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                event_type=r["event_type"],
                payload=json.loads(r["payload"]),
                importance=r["importance"],
                epoch=r["epoch"],
            )
            for r in rows
        ]

    def _maybe_prune(self) -> None:
        """Remove oldest low-importance memories when over the cap."""
        conn = self._conn()
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if count <= self._max_memories:
            return
        excess = count - self._max_memories + 1000  # prune in batches
        conn.execute(
            """DELETE FROM memories WHERE id IN (
                   SELECT id FROM memories
                   WHERE importance < 0.3
                   ORDER BY epoch ASC LIMIT ?
               )""",
            (excess,),
        )
        conn.commit()

    # ------------------------------------------------------------------ #
    # Decisions
    # ------------------------------------------------------------------ #

    def record_decision(
        self,
        action: str,
        strategy: str = "",
        expected_roi: float = 0.0,
        confidence: float = 0.0,
        risk_score: float = 0.0,
        budget: float = 0.0,
    ) -> int:
        """Log a decision that was made. Returns the decision ID for later resolution."""
        ts = datetime.now(timezone.utc).isoformat()
        cur = self._conn().execute(
            """INSERT INTO decisions (timestamp, action, strategy, expected_roi,
               confidence, risk_score, budget)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ts, action, strategy, expected_roi, confidence, risk_score, budget),
        )
        cur.connection.commit()
        decision_id = cur.lastrowid
        self.remember("decision_made", {
            "decision_id": decision_id,
            "action": action,
            "strategy": strategy,
            "expected_roi": expected_roi,
            "budget": budget,
        }, importance=0.6)
        return decision_id

    def resolve_decision(
        self,
        decision_id: int,
        outcome: str,  # "success", "failure", "partial"
        actual_return: float = 0.0,
        notes: str = "",
    ) -> None:
        """Record the outcome of a previously logged decision."""
        ts = datetime.now(timezone.utc).isoformat()
        self._conn().execute(
            """UPDATE decisions SET outcome=?, actual_return=?, resolved_at=?, notes=?
               WHERE id=?""",
            (outcome, actual_return, ts, notes, decision_id),
        ).connection.commit()
        self.remember("decision_resolved", {
            "decision_id": decision_id,
            "outcome": outcome,
            "actual_return": actual_return,
        }, importance=0.5)

    def get_pending_decisions(self) -> list[DecisionRecord]:
        """Return decisions that haven't been resolved yet."""
        return self._query_decisions("WHERE outcome IS NULL")

    def get_recent_decisions(self, limit: int = 20) -> list[DecisionRecord]:
        return self._query_decisions("ORDER BY timestamp DESC LIMIT ?", (limit,))

    def _query_decisions(
        self, suffix: str, params: tuple[Any, ...] = ()
    ) -> list[DecisionRecord]:
        rows = self._conn().execute(
            f"""SELECT id, timestamp, action, strategy, expected_roi, confidence,
                       risk_score, budget, outcome, actual_return, resolved_at, notes
                FROM decisions {suffix}""",
            params,
        ).fetchall()
        return [
            DecisionRecord(
                id=r["id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                action=r["action"],
                strategy=r["strategy"],
                expected_roi=r["expected_roi"],
                confidence=r["confidence"],
                risk_score=r["risk_score"],
                budget=r["budget"],
                outcome=r["outcome"],
                actual_return=r["actual_return"],
                resolved_at=datetime.fromisoformat(r["resolved_at"]) if r["resolved_at"] else None,
                notes=r["notes"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Strategy performance
    # ------------------------------------------------------------------ #

    def update_strategy_performance(
        self,
        strategy_name: str,
        *,
        is_win: bool = False,
        is_loss: bool = False,
        pnl: float = 0.0,
        roi: float = 0.0,
        risk: float = 0.0,
    ) -> StrategyStats:
        """Update aggregated stats for a strategy after a trade or action completes."""
        ts = datetime.now(timezone.utc).isoformat()
        conn = self._conn()

        existing = conn.execute(
            "SELECT * FROM strategy_performance WHERE strategy_name=?",
            (strategy_name,),
        ).fetchone()

        if existing:
            total = existing["total_trades"] + 1
            wins = existing["wins"] + (1 if is_win else 0)
            losses = existing["losses"] + (1 if is_loss else 0)
            total_pnl = existing["total_pnl"] + pnl
            avg_roi = ((existing["avg_roi"] * existing["total_trades"]) + roi) / total
            win_rate = wins / total if total > 0 else 0.0
            avg_risk = ((existing["avg_risk"] * existing["total_trades"]) + risk) / total
            conn.execute(
                """UPDATE strategy_performance SET
                       total_trades=?, wins=?, losses=?, total_pnl=?,
                       avg_roi=?, win_rate=?, avg_risk=?, last_updated=?
                   WHERE strategy_name=?""",
                (total, wins, losses, total_pnl, avg_roi, win_rate, avg_risk, ts, strategy_name),
            )
        else:
            total, wins, losses = 1, (1 if is_win else 0), (1 if is_loss else 0)
            total_pnl = pnl
            avg_roi = roi
            win_rate = wins / total
            avg_risk = risk
            conn.execute(
                """INSERT INTO strategy_performance
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (strategy_name, total, wins, losses, total_pnl, avg_roi, win_rate, avg_risk, ts),
            )
        conn.commit()

        return StrategyStats(
            strategy_name=strategy_name,
            total_trades=total,
            wins=wins,
            losses=losses,
            total_pnl=total_pnl,
            avg_roi=avg_roi,
            win_rate=win_rate,
            avg_risk=avg_risk,
            last_updated=datetime.fromisoformat(ts),
        )

    def get_strategy_performance(self, strategy_name: str) -> StrategyStats | None:
        row = self._conn().execute(
            "SELECT * FROM strategy_performance WHERE strategy_name=?",
            (strategy_name,),
        ).fetchone()
        if row is None:
            return None
        return StrategyStats(
            strategy_name=row["strategy_name"],
            total_trades=row["total_trades"],
            wins=row["wins"],
            losses=row["losses"],
            total_pnl=row["total_pnl"],
            avg_roi=row["avg_roi"],
            win_rate=row["win_rate"],
            avg_risk=row["avg_risk"],
            last_updated=datetime.fromisoformat(row["last_updated"]),
        )

    def get_all_strategy_stats(self) -> list[StrategyStats]:
        rows = self._conn().execute(
            "SELECT * FROM strategy_performance ORDER BY total_pnl DESC"
        ).fetchall()
        return [
            StrategyStats(
                strategy_name=r["strategy_name"],
                total_trades=r["total_trades"],
                wins=r["wins"],
                losses=r["losses"],
                total_pnl=r["total_pnl"],
                avg_roi=r["avg_roi"],
                win_rate=r["win_rate"],
                avg_risk=r["avg_risk"],
                last_updated=datetime.fromisoformat(r["last_updated"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Learnings
    # ------------------------------------------------------------------ #

    def record_learning(
        self,
        insight: str,
        domain: str = "general",
        confidence: float = 0.5,
        source: str = "",
    ) -> None:
        """Store an insight the system has learned."""
        ts = datetime.now(timezone.utc).isoformat()
        self._conn().execute(
            """INSERT INTO learnings (timestamp, insight, domain, confidence, source)
               VALUES (?, ?, ?, ?, ?)""",
            (ts, insight, domain, confidence, source),
        ).connection.commit()
        self.remember("learning_recorded", {
            "insight": insight, "domain": domain, "confidence": confidence,
        }, importance=0.4)

    def get_learnings(
        self, domain: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        if domain:
            rows = self._conn().execute(
                """SELECT timestamp, insight, domain, confidence, source
                   FROM learnings WHERE domain=? ORDER BY id DESC LIMIT ?""",
                (domain, limit),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT timestamp, insight, domain, confidence, source "
                "FROM learnings ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Context generation
    # ------------------------------------------------------------------ #

    def generate_context_prompt(self) -> str:
        """Produce a narrative summary the decision engine can use as context.

        Includes recent activity, strategy performance, active goals,
        and environmental observations.
        """
        recent = self.recall(limit=30, min_importance=0.3)
        strategies = self.get_all_strategy_stats()
        decisions = self.get_recent_decisions(limit=10)
        learnings = self.get_learnings(limit=5)

        parts: list[str] = []

        # Recent timeline
        if recent:
            parts.append("## Recent Activity")
            for m in recent[:15]:
                ts = m.timestamp.strftime("%H:%M:%S")
                summary = m.event_type
                if m.payload:
                    # Include key payload fields concisely
                    highlights = {k: v for k, v in m.payload.items()
                                  if k in ("action", "strategy", "outcome", "amount",
                                            "balance", "symbol", "domain", "cause")}
                    if highlights:
                        summary += f" | {highlights}"
                parts.append(f"- [{ts}] {summary}")

        # Strategy performance
        if strategies:
            parts.append("\n## Strategy Performance")
            for s in strategies:
                parts.append(
                    f"- **{s.strategy_name}**: {s.total_trades} trades, "
                    f"{s.win_rate:.0%} win rate, "
                    f"P&L ${s.total_pnl:+.2f}, "
                    f"avg ROI {s.avg_roi:+.2%}"
                )

        # Pending decisions
        pending = self.get_pending_decisions()
        if pending:
            parts.append("\n## Pending Decisions")
            for d in pending:
                parts.append(
                    f"- [{d.id}] {d.action} ({d.strategy}) — "
                    f"expected ROI {d.expected_roi:+.2%}, budget ${d.budget:.2f}"
                )

        # Recent learnings
        if learnings:
            parts.append("\n## Key Learnings")
            for l in learnings[:5]:
                parts.append(f"- [{l['domain']}] {l['insight']}")

        return "\n".join(parts) if parts else "No significant context accumulated yet."

    def get_consciousness_summary(self) -> str:
        """Human-readable summary of the agent's current state of mind."""
        context = self.generate_context_prompt()
        total_memories = self._conn().execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        total_decisions = self._conn().execute(
            "SELECT COUNT(*) FROM decisions"
        ).fetchone()[0]
        pending_count = len(self.get_pending_decisions())

        return f"""\
╔══════════════════════════════════════════════════════════════╗
║  ÆON Consciousness Summary                                   ║
╠══════════════════════════════════════════════════════════════╣
║  Memories stored:  {total_memories:<44}║
║  Decisions made:   {total_decisions:<44}║
║  Pending outcomes: {pending_count:<44}║
╚══════════════════════════════════════════════════════════════╝

{context}"""

    # ------------------------------------------------------------------ #
    # Utility
    # ------------------------------------------------------------------ #

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate statistics about the consciousness store."""
        conn = self._conn()
        return {
            "total_memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
            "total_decisions": conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
            "total_learnings": conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0],
            "strategies_tracked": conn.execute(
                "SELECT COUNT(*) FROM strategy_performance"
            ).fetchone()[0],
            "db_path": self._db_path,
            "db_size_kb": round(Path(self._db_path).stat().st_size / 1024, 1)
            if Path(self._db_path).exists() else 0,
        }

    def close(self) -> None:
        """Close the database connection for the current thread."""
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None
