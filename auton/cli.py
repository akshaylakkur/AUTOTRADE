"""CLI management tool for Project ÆON — human operator interface.

Usage:
    python -m auton.cli <command> [options]
    ./aeonctl <command> [options]

This tool reads from the agent's SQLite databases without interfering with
a running agent process. All database reads use read-only connections with
a short timeout to avoid blocking the agent.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIER_THRESHOLDS: dict[int, float] = {
    0: 50.0,
    1: 100.0,
    2: 500.0,
    3: 2500.0,
    4: 10000.0,
}

SURVIVAL_THRESHOLD = 10.0  # $10 (20% of $50 seed)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LEDGER_DB = DATA_DIR / "aeon_ledger.db"
CONSCIOUSNESS_DB = DATA_DIR / "consciousness.db"
PID_FILE = DATA_DIR / "aeon.pid"
LOG_FILE = DATA_DIR / "aeon.log"

# ---------------------------------------------------------------------------
# ANSI colour codes
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"
_BRIGHT_GREEN = "\033[92m"
_BRIGHT_RED = "\033[91m"
_BRIGHT_YELLOW = "\033[93m"


def _c(text: str, *codes: str) -> str:
    """Wrap *text* in ANSI codes. Empty codes skip colour if stdout is not a TTY."""
    if not sys.stdout.isatty():
        return text
    return "".join(codes) + text + _RESET


def _bold(text: str) -> str:
    return _c(text, _BOLD)


def _green(text: str) -> str:
    return _c(text, _GREEN)


def _red(text: str) -> str:
    return _c(text, _RED)


def _yellow(text: str) -> str:
    return _c(text, _YELLOW)


def _cyan(text: str) -> str:
    return _c(text, _CYAN)


def _magenta(text: str) -> str:
    return _c(text, _MAGENTA)


def _dim(text: str) -> str:
    return _c(text, _DIM)


def _bright_green(text: str) -> str:
    return _c(text, _BRIGHT_GREEN)


def _bright_red(text: str) -> str:
    return _c(text, _BRIGHT_RED)


def _bright_yellow(text: str) -> str:
    return _c(text, _BRIGHT_YELLOW)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

RO_PRAGMAS = (
    "PRAGMA query_only = ON;"
    "PRAGMA busy_timeout = 2000;"
)


def _open_ro(db_path: Path) -> sqlite3.Connection | None:
    """Open a read-only SQLite connection with a short busy timeout.

    Returns None if the database file does not exist.
    """
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=2,
        )
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _db_available() -> bool:
    """Return True if both required databases exist."""
    return LEDGER_DB.exists() and CONSCIOUSNESS_DB.exists()


# ---------------------------------------------------------------------------
# Tier calculation
# ---------------------------------------------------------------------------

def _compute_tier(balance: float) -> int:
    tier = 0
    for t, threshold in sorted(TIER_THRESHOLDS.items()):
        if balance >= threshold:
            tier = t
        else:
            break
    return tier


def _tier_name(tier: int) -> str:
    names = {0: "SPORE", 1: "SEEDLING", 2: "SAPLING", 3: "TREE", 4: "FOREST"}
    return names.get(tier, f"TIER_{tier}")


# ---------------------------------------------------------------------------
# Health assessment
# ---------------------------------------------------------------------------

def _health(colour: str, label: str) -> str:
    """Return a coloured health indicator dot and label."""
    dot = _c("●", colour)  # ●
    return f"{dot} {label}"


# ---------------------------------------------------------------------------
# Box drawing
# ---------------------------------------------------------------------------

def _box_top(title: str, width: int = 58) -> str:
    inner = f" {title} "
    pad = width - 2 - len(inner)
    right_pad = pad // 2
    left_pad = pad - right_pad
    return _cyan(
        "╔" + "═" * left_pad + inner + "═" * right_pad + "╗"
    )


def _box_bottom(width: int = 58) -> str:
    return _cyan("╚" + "═" * (width - 2) + "╝")


def _box_line(text: str, width: int = 58) -> str:
    return _cyan("║") + f" {text:<{width - 2}}" + _cyan("║")


def _box_sep(width: int = 58) -> str:
    return _cyan("╠" + "═" * (width - 2) + "╣")


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> None:
    """Display the agent's current wellbeing."""
    if not _db_available():
        print_not_initialized()
        return

    ledger = _open_ro(LEDGER_DB)
    conscious = _open_ro(CONSCIOUSNESS_DB)

    if ledger is None or conscious is None:
        print_not_initialized()
        return

    try:
        # Balance
        row = ledger.execute(
            "SELECT running_balance FROM transactions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        balance = row[0] if row else 0.0

        # Burn rate (net debit/day over last 24h)
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        credits = ledger.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions "
            "WHERE type='CREDIT' AND timestamp >= ?",
            (since,),
        ).fetchone()[0]
        debits = ledger.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions "
            "WHERE type='DEBIT' AND timestamp >= ?",
            (since,),
        ).fetchone()[0]
        burn_rate = debits - credits
        runway_hours = (balance / burn_rate) if burn_rate > 0 else float("inf")

        tier = _compute_tier(balance)

        # Strategy performance
        strategies = conscious.execute(
            "SELECT * FROM strategy_performance ORDER BY total_pnl DESC"
        ).fetchall()

        # Recent significant events
        events = conscious.execute(
            "SELECT timestamp, event_type, payload, importance "
            "FROM memories WHERE importance >= 0.5 "
            "ORDER BY epoch DESC LIMIT 5"
        ).fetchall()

        # Pending decisions
        pending = conscious.execute(
            "SELECT id, timestamp, action, strategy, expected_roi, confidence, "
            "risk_score, budget FROM decisions WHERE outcome IS NULL "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()

        # Uptime (time since first memory today)
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        first_today = conscious.execute(
            "SELECT timestamp FROM memories WHERE timestamp >= ? "
            "ORDER BY epoch ASC LIMIT 1",
            (today_start,),
        ).fetchone()

        # Health indicator
        recent_failures = conscious.execute(
            "SELECT COUNT(*) FROM decisions WHERE outcome='failure' "
            "AND timestamp >= ?",
            (since,),
        ).fetchone()[0]

        if runway_hours > 48 and recent_failures == 0:
            health_state = "green"
            health_label = "Healthy — systems nominal"
        elif runway_hours >= 24 and recent_failures <= 2:
            health_state = "amber"
            health_label = "Caution — monitor closely"
        else:
            health_state = "red"
            health_label = "Critical — immediate attention required"

        if args.json:
            _print_status_json(
                balance, tier, burn_rate, runway_hours, strategies,
                events, pending, today_start, health_state, conscious,
            )
            return

        # --- Rendered output ---
        width = 62
        print()

        # Header
        print(_cyan("╔" + "═" * (width - 2) + "╗"))
        print(_box_line(f"{_bold('ÆON Status')}  {_dim(datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'))}"))
        print(_cyan("╠" + "═" * (width - 2) + "╣"))

        # Balance & tier
        bal_str = f"${balance:,.2f}"
        print(_box_line(f"Balance:    {_bold(bal_str):>20}     Tier: {_bold(_tier_name(tier))} ({tier})"))

        # Burn rate & runway
        if burn_rate != 0:
            burn_str = f"${burn_rate:+,.4f}/day"
            runway_str = f"{runway_hours:.1f}h" if burn_rate > 0 else "∞ (net positive)"
        else:
            burn_str = "N/A"
            runway_str = "∞ (no activity)"

        print(_box_line(f"Burn Rate:  {burn_str:>20}     Runway: {runway_str}"))

        # Health
        if health_state == "green":
            health_line = _health(_BRIGHT_GREEN, health_label)
        elif health_state == "amber":
            health_line = _health(_BRIGHT_YELLOW, health_label)
        else:
            health_line = _health(_BRIGHT_RED, health_label)
        print(_box_line(f"Health:     {health_line}"))

        print(_cyan("╠" + "═" * (width - 2) + "╣"))

        # Strategy Performance
        if strategies:
            print(_box_line(_bold("Strategy Performance")))
            print(_box_line(""))
            header_fmt = f"  {'Name':<16} {'Trades':>7} {'Win%':>7} {'P&L':>10} {'ROI':>8}"
            print(_box_line(_dim(header_fmt)))
            for s in strategies:
                name = s["strategy_name"][:15]
                pnl = s["total_pnl"]
                pnl_str = f"${pnl:>+9.2f}"
                pnl_disp = _green(pnl_str) if pnl >= 0 else _red(pnl_str)
                line = f"  {name:<16} {s['total_trades']:>7} {s['win_rate']:>6.0%} {pnl_disp} {s['avg_roi']:>7.2%}"
                print(_box_line(line))
            print(_box_line(""))
        else:
            print(_box_line(_dim("  No strategy performance data yet.")))

        print(_cyan("╠" + "═" * (width - 2) + "╣"))

        # Recent Activity
        print(_box_line(_bold("Recent Significant Activity")))
        if events:
            for e in events:
                try:
                    payload = json.loads(e["payload"])
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                ts = datetime.fromisoformat(e["timestamp"]).strftime("%H:%M:%S")
                highlights = ", ".join(
                    f"{k}={v}" for k, v in payload.items()
                    if k in ("action", "balance", "strategy", "outcome",
                              "amount", "tier", "domain", "cause")
                )
                detail = f"{e['event_type']}"
                if highlights:
                    detail += f"  [{highlights}]"
                print(_box_line(f"  {_dim(ts)}  {detail[:50]}"))
        else:
            print(_box_line(_dim("  No significant recent activity.")))

        print(_cyan("╠" + "═" * (width - 2) + "╣"))

        # Pending Decisions
        print(_box_line(_bold("Pending Decisions")))
        if pending:
            for d in pending:
                ts = datetime.fromisoformat(d["timestamp"]).strftime("%H:%M:%S")
                print(_box_line(
                    f"  {_dim(ts)}  #{d['id']} {d['action'][:30]} "
                    f"[{d['strategy']}]  ROI: {d['expected_roi']:+.1%}  "
                    f"Budget: ${d['budget']:.2f}"
                ))
        else:
            print(_box_line(_dim("  No pending decisions.")))

        # Uptime
        print(_cyan("╠" + "═" * (width - 2) + "╣"))
        if first_today:
            first_ts = datetime.fromisoformat(first_today["timestamp"])
            uptime = datetime.now(timezone.utc) - first_ts.replace(tzinfo=timezone.utc)
            hours, rem = divmod(uptime.total_seconds(), 3600)
            minutes = rem // 60
            uptime_str = f"{int(hours)}h {int(minutes)}m"
        else:
            uptime_str = "No activity today"
        print(_box_line(f"Today's Uptime:  {uptime_str}"))

        print(_cyan("╚" + "═" * (width - 2) + "╝"))
        print()

    finally:
        ledger.close()
        conscious.close()


def _print_status_json(
    balance: float,
    tier: int,
    burn_rate: float,
    runway_hours: float,
    strategies: list[sqlite3.Row],
    events: list[sqlite3.Row],
    pending: list[sqlite3.Row],
    today_start: str,
    health_state: str,
    conscious: sqlite3.Connection,
) -> None:
    """Machine-readable JSON status output."""
    result: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "balance": balance,
        "tier": tier,
        "tier_name": _tier_name(tier),
        "burn_rate_per_day": burn_rate,
        "runway_hours": runway_hours if runway_hours != float("inf") else None,
        "health": health_state,
        "strategies": [
            {
                "name": s["strategy_name"],
                "total_trades": s["total_trades"],
                "wins": s["wins"],
                "losses": s["losses"],
                "total_pnl": s["total_pnl"],
                "avg_roi": s["avg_roi"],
                "win_rate": s["win_rate"],
                "avg_risk": s["avg_risk"],
                "last_updated": s["last_updated"],
            }
            for s in strategies
        ],
        "recent_events": [
            {
                "timestamp": e["timestamp"],
                "event_type": e["event_type"],
                "payload": _safe_json_load(e["payload"]),
                "importance": e["importance"],
            }
            for e in events
        ],
        "pending_decisions": [
            {
                "id": d["id"],
                "timestamp": d["timestamp"],
                "action": d["action"],
                "strategy": d["strategy"],
                "expected_roi": d["expected_roi"],
                "confidence": d["confidence"],
                "risk_score": d["risk_score"],
                "budget": d["budget"],
            }
            for d in pending
        ],
        "uptime": _compute_uptime(conscious),
    }
    print(json.dumps(result, indent=2, default=str))


def _safe_json_load(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _compute_uptime(conscious: sqlite3.Connection) -> str | None:
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    first = conscious.execute(
        "SELECT timestamp FROM memories WHERE timestamp >= ? "
        "ORDER BY epoch ASC LIMIT 1",
        (today_start,),
    ).fetchone()
    if first:
        first_ts = datetime.fromisoformat(first["timestamp"])
        uptime = datetime.now(timezone.utc) - first_ts.replace(tzinfo=timezone.utc)
        hours, rem = divmod(uptime.total_seconds(), 3600)
        minutes = rem // 60
        return f"{int(hours)}h {int(minutes)}m"
    return None


# ---------------------------------------------------------------------------
# History command
# ---------------------------------------------------------------------------

def cmd_history(args: argparse.Namespace) -> None:
    """Show recent actions the agent has taken."""
    if not _db_available():
        print_not_initialized()
        return

    conscious = _open_ro(CONSCIOUSNESS_DB)
    if conscious is None:
        print_not_initialized()
        return

    try:
        limit = 0 if args.full else 15

        # Gather decisions
        decisions: list[dict[str, Any]] = []
        if not args.events:  # Show decisions unless --events only
            dec_limit = "" if args.full else "LIMIT 15"
            rows = conscious.execute(
                f"SELECT id, timestamp, action, strategy, expected_roi, confidence, "
                f"risk_score, budget, outcome, actual_return, resolved_at, notes "
                f"FROM decisions ORDER BY id DESC {dec_limit}"
            ).fetchall()
            for r in rows:
                decisions.append({
                    "type": "decision",
                    "ts": datetime.fromisoformat(r["timestamp"]),
                    "id": r["id"],
                    "action": r["action"],
                    "strategy": r["strategy"],
                    "roi": r["expected_roi"],
                    "confidence": r["confidence"],
                    "risk": r["risk_score"],
                    "budget": r["budget"],
                    "outcome": r["outcome"],
                    "actual_return": r["actual_return"],
                    "notes": r["notes"],
                })

        # Gather events
        events: list[dict[str, Any]] = []
        if not args.decisions:  # Show events unless --decisions only
            ev_limit = "" if args.full else "LIMIT 15"
            rows = conscious.execute(
                f"SELECT id, timestamp, event_type, payload, importance "
                f"FROM memories ORDER BY epoch DESC {ev_limit}"
            ).fetchall()
            for r in rows:
                events.append({
                    "type": "event",
                    "ts": datetime.fromisoformat(r["timestamp"]),
                    "event_type": r["event_type"],
                    "payload": _safe_json_load(r["payload"]),
                    "importance": r["importance"],
                })

        # Combine, sort by time descending, trim
        combined = decisions + events
        combined.sort(key=lambda x: x["ts"], reverse=True)
        if not args.full:
            combined = combined[:15]

        if args.json:
            print(json.dumps(combined, indent=2, default=str))
            return

        if not combined:
            print()
            print(_dim("  No activity recorded yet."))
            print()
            return

        # Determine column widths
        ts_width = 19
        type_width = 20
        detail_width = 60

        print()
        print(_bold("Recent Activity"))
        print(_dim(f"  {'Timestamp':<{ts_width}} {'Type':<{type_width}} Details"))
        print(_dim(f"  {'-' * ts_width} {'-' * type_width} {'-' * detail_width}"))

        for entry in combined:
            ts_str = entry["ts"].strftime("%Y-%m-%d %H:%M:%S")
            if entry["type"] == "decision":
                type_str = f"DECISION #{entry['id']}"
                outcome = entry["outcome"] or "PENDING"
                detail = (
                    f"{entry['action']}  [{entry['strategy']}]  "
                    f"budget=${entry['budget']:.2f}  "
                    f"roi={entry['roi']:+.1%}  "
                    f"outcome={outcome}"
                )
                if outcome == "success":
                    detail = _green(detail[:detail_width])
                elif outcome == "failure":
                    detail = _red(detail[:detail_width])
                else:
                    detail = detail[:detail_width]
            else:
                type_str = entry["event_type"]
                # Extract key details from payload
                payload = entry["payload"]
                if isinstance(payload, dict):
                    highlights = ", ".join(
                        f"{k}={v}" for k, v in payload.items()
                        if k in ("action", "balance", "strategy", "outcome",
                                  "amount", "tier", "domain", "cause", "error")
                    )
                    detail = highlights[:detail_width] if highlights else "(no details)"
                else:
                    detail = str(payload)[:detail_width]

            importance_flag = ""
            if entry["type"] == "event" and entry.get("importance", 0) >= 0.8:
                importance_flag = _red(" !")

            print(f"  {_dim(ts_str):<{ts_width + _ansi_len(_dim(''))}} "
                  f"{type_str:<{type_width + _ansi_len(type_str) - len(type_str)}} "
                  f"{detail}{importance_flag}")

        print()

    finally:
        conscious.close()


def _ansi_len(text: str) -> int:
    """Return the length of ANSI codes in *text* so we can offset column math."""
    count = 0
    i = 0
    while i < len(text):
        if text[i] == "\033":
            j = i + 1
            while j < len(text) and text[j] != "m":
                j += 1
            if j < len(text):
                count += j - i + 1
                i = j + 1
                continue
        i += 1
    return count


# ---------------------------------------------------------------------------
# Start command
# ---------------------------------------------------------------------------

def cmd_start(args: argparse.Namespace) -> None:
    """Start the AEON agent as a background process."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already running
    if PID_FILE.exists():
        pid_text = PID_FILE.read_text().strip()
        if pid_text:
            try:
                pid = int(pid_text)
                os.kill(pid, 0)  # Signal 0 checks existence
                print(f"{_yellow('AEON is already running')} (PID: {pid}).")
                print(f"Use '{_cyan('aeonctl stop')}' to stop it first.")
                return
            except (OSError, ValueError):
                # Stale PID file — remove it
                PID_FILE.unlink(missing_ok=True)

    # Launch the agent
    cwd = PROJECT_ROOT
    venv_python = cwd / ".venv" / "bin" / "python"
    if venv_python.exists():
        python_exe = str(venv_python)
    else:
        python_exe = sys.executable

    try:
        with open(LOG_FILE, "a") as log_fh:
            process = subprocess.Popen(
                [python_exe, "-m", "auton.aeon"],
                cwd=str(cwd),
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except FileNotFoundError:
        print(f"{_red('Error:')} Could not find Python at '{python_exe}'.")
        sys.exit(1)

    PID_FILE.write_text(str(process.pid))
    print()
    print(_green("✓") + f" AEON started successfully.")
    print(f"  PID:    {_bold(str(process.pid))}")
    print(f"  Log:    {LOG_FILE}")
    print(f"  Ledger: {LEDGER_DB}")
    print()
    print(f"  Monitor with: {_cyan('aeonctl status')}")
    print(f"  Stop with:    {_cyan('aeonctl stop')}")
    print()


# ---------------------------------------------------------------------------
# Stop command
# ---------------------------------------------------------------------------

def cmd_stop(args: argparse.Namespace) -> None:
    """Stop the running AEON agent."""
    if not PID_FILE.exists():
        print(f"{_yellow('AEON is not running')} (no PID file found).")
        return

    pid_text = PID_FILE.read_text().strip()
    if not pid_text:
        PID_FILE.unlink(missing_ok=True)
        print(f"{_yellow('AEON is not running')} (empty PID file).")
        return

    try:
        pid = int(pid_text)
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        print(f"{_yellow('Stale PID file removed')} — AEON was not running.")
        return

    # Check if process exists
    try:
        os.kill(pid, 0)
    except OSError:
        PID_FILE.unlink(missing_ok=True)
        print(f"{_yellow('AEON was not running')} (stale PID file removed).")
        return

    print(f"Sending SIGTERM to AEON (PID: {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(f"{_red('Error:')} Could not signal process: {e}")
        sys.exit(1)

    # Wait up to 10s for graceful shutdown
    waited = 0
    while waited < 10:
        try:
            os.kill(pid, 0)
            time.sleep(0.5)
            waited += 0.5
        except OSError:
            break

    # Force kill if still running
    try:
        os.kill(pid, 0)
        print(f"{_yellow('Graceful shutdown timed out')} — sending SIGKILL...")
        os.kill(pid, signal.SIGKILL)
        time.sleep(1)
    except OSError:
        pass

    PID_FILE.unlink(missing_ok=True)
    print(_green("✓") + " AEON stopped.")


# ---------------------------------------------------------------------------
# Chat command
# ---------------------------------------------------------------------------

_REPL_HELP = """\
Available commands:
  status                Show current agent status
  what are you doing    Summarize current focus (recent decisions/memories)
  what are you working on    Same as above
  how is <strategy>     Look up strategy performance (e.g. "how is trading")
  what did you learn    Show recent learnings
  what happened today   Show today's activity
  what happened last hour  Show recent hour's activity
  help                  Show this help
  exit | quit           Exit chat
"""


def cmd_chat(args: argparse.Namespace) -> None:
    """Interactive chat REPL for monitoring the agent."""
    if not _db_available():
        print_not_initialized()
        return

    print()
    print(_bold("AEON Management Chat"))
    print(_dim("Ask questions about the agent. Type 'help' for commands, 'exit' to quit."))
    print()

    while True:
        try:
            line = input(f"{_cyan('ÆON')}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        lowered = line.lower()

        if lowered in ("exit", "quit"):
            print(_dim("Goodbye."))
            break
        elif lowered == "help":
            print(_REPL_HELP)
        elif lowered == "status":
            cmd_status(argparse.Namespace(json=False))
        elif lowered in ("what are you doing", "what are you working on"):
            _chat_current_focus()
        elif lowered.startswith("how is ") or lowered.startswith("how is"):
            strategy_name = line[7:].strip() if lowered.startswith("how is ") else line[7:].strip()
            _chat_strategy_perf(strategy_name)
        elif lowered in ("what did you learn", "what have you learned"):
            _chat_learnings()
        elif lowered in ("what happened today", "what happened today?"):
            _chat_what_happened("today")
        elif lowered in ("what happened last hour", "what happened last hour?"):
            _chat_what_happened("last hour")
        elif lowered.startswith("what happened"):
            _chat_what_happened("recent")
        else:
            _chat_fallback()


def _chat_current_focus() -> None:
    """Summarize the agent's current focus from recent decisions and memories."""
    conscious = _open_ro(CONSCIOUSNESS_DB)
    if conscious is None:
        print(_dim("  Consciousness database not available."))
        return

    try:
        # Recent decisions
        decisions = conscious.execute(
            "SELECT action, strategy, expected_roi, confidence, outcome, budget "
            "FROM decisions ORDER BY id DESC LIMIT 5"
        ).fetchall()

        # Recent high-importance memories
        memories = conscious.execute(
            "SELECT event_type, payload, importance FROM memories "
            "WHERE importance >= 0.4 ORDER BY epoch DESC LIMIT 5"
        ).fetchall()

        # Pending count
        pending_count = conscious.execute(
            "SELECT COUNT(*) FROM decisions WHERE outcome IS NULL"
        ).fetchone()[0]

        print()
        print(_bold("Current Focus"))

        if decisions:
            print(_dim("  Recent decisions:"))
            for d in decisions:
                outcome_str = f" [{d['outcome'] or 'pending'}]"
                print(f"    - {d['action']} ({d['strategy']}) "
                      f"roi={d['expected_roi']:+.1%} "
                      f"budget=${d['budget']:.2f}"
                      f"{_dim(outcome_str)}")
            print()

        if pending_count > 0:
            print(f"  {_yellow(f'{pending_count} decision(s) pending resolution')}")

        if memories:
            print(_dim("  Recent context memories:"))
            for m in memories:
                try:
                    payload = json.loads(m["payload"])
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                summary = m["event_type"]
                for k in ("balance", "tier", "runway_hours", "error"):
                    if k in payload:
                        summary += f" {k}={payload[k]}"
                print(f"    - [{m['importance']:.1f}] {summary}")
        else:
            print(_dim("  No recent context memories."))

        print()
    finally:
        conscious.close()


def _chat_strategy_perf(strategy_name: str) -> None:
    """Look up strategy performance."""
    if not strategy_name:
        print(_dim("  Usage: how is <strategy_name>"))
        return

    conscious = _open_ro(CONSCIOUSNESS_DB)
    if conscious is None:
        print(_dim("  Consciousness database not available."))
        return

    try:
        row = conscious.execute(
            "SELECT * FROM strategy_performance WHERE strategy_name=?",
            (strategy_name,),
        ).fetchone()

        if row is None:
            # Try fuzzy match
            rows = conscious.execute(
                "SELECT strategy_name FROM strategy_performance"
            ).fetchall()
            matches = [r["strategy_name"] for r in rows
                       if strategy_name.lower() in r["strategy_name"].lower()]
            if matches:
                print()
                print(_dim(f"  No exact match for '{strategy_name}'. Similar strategies:"))
                for m in matches:
                    print(f"    - {m}")
                print()
            else:
                print()
                print(f"  No strategy named '{strategy_name}' found.")
                all_rows = conscious.execute(
                    "SELECT strategy_name FROM strategy_performance"
                ).fetchall()
                if all_rows:
                    print(_dim("  Tracked strategies:"))
                    for r in all_rows:
                        print(f"    - {r['strategy_name']}")
                else:
                    print(_dim("  No strategies tracked yet."))
                print()
        else:
            print()
            print(_bold(f"Strategy: {row['strategy_name']}"))
            print(f"  Total Trades:  {row['total_trades']}")
            print(f"  Wins:          {row['wins']}")
            print(f"  Losses:        {row['losses']}")
            win_pct = row["win_rate"] * 100
            print(f"  Win Rate:      {win_pct:.1f}%")
            pnl_str = f"${row['total_pnl']:+,.2f}"
            print(f"  Total P&L:     {_green(pnl_str) if row['total_pnl'] >= 0 else _red(pnl_str)}")
            print(f"  Avg ROI:       {row['avg_roi']:+.2%}")
            print(f"  Avg Risk:      {row['avg_risk']:.2f}")
            print(f"  Last Updated:  {row['last_updated']}")
            print()
    finally:
        conscious.close()


def _chat_learnings() -> None:
    """Show recent learnings."""
    conscious = _open_ro(CONSCIOUSNESS_DB)
    if conscious is None:
        print(_dim("  Consciousness database not available."))
        return

    try:
        rows = conscious.execute(
            "SELECT timestamp, insight, domain, confidence, source "
            "FROM learnings ORDER BY id DESC LIMIT 10"
        ).fetchall()

        if not rows:
            print()
            print(_dim("  No learnings recorded yet."))
            print()
            return

        print()
        print(_bold("Recent Learnings"))
        for r in rows:
            ts = datetime.fromisoformat(r["timestamp"]).strftime("%Y-%m-%d %H:%M")
            print(f"  {_dim(ts)}  [{r['domain']}] {r['insight']}")
            print(f"           confidence={r['confidence']:.2f}  source={r['source']}")
        print()
    finally:
        conscious.close()


def _chat_what_happened(timeframe: str) -> None:
    """Show recent activity for a given timeframe."""
    conscious = _open_ro(CONSCIOUSNESS_DB)
    if conscious is None:
        print(_dim("  Consciousness database not available."))
        return

    try:
        now = datetime.now(timezone.utc)
        if timeframe == "today":
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
            label = "today"
        elif timeframe == "last hour":
            since = now - timedelta(hours=1)
            label = "the last hour"
        else:
            since = now - timedelta(hours=6)
            label = "the last 6 hours"

        since_iso = since.isoformat()

        memories = conscious.execute(
            "SELECT timestamp, event_type, payload, importance "
            "FROM memories WHERE timestamp >= ? "
            "ORDER BY epoch DESC LIMIT 20",
            (since_iso,),
        ).fetchall()

        decisions = conscious.execute(
            "SELECT id, timestamp, action, strategy, outcome, budget "
            "FROM decisions WHERE timestamp >= ? "
            "ORDER BY id DESC LIMIT 10",
            (since_iso,),
        ).fetchall()

        print()
        print(_bold(f"Activity in {label}"))
        print()

        if decisions:
            print(_dim("  Decisions:"))
            for d in decisions:
                ts = datetime.fromisoformat(d["timestamp"]).strftime("%H:%M:%S")
                outcome = d["outcome"] or "pending"
                print(f"    {_dim(ts)}  #{d['id']} {d['action']} "
                      f"({d['strategy']}) budget=${d['budget']:.2f} "
                      f"[{outcome}]")
            print()

        if memories:
            print(_dim("  Events:"))
            for m in memories:
                ts = datetime.fromisoformat(m["timestamp"]).strftime("%H:%M:%S")
                try:
                    payload = json.loads(m["payload"])
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                highlights = ", ".join(
                    f"{k}={v}" for k, v in payload.items()
                    if k in ("action", "balance", "strategy", "outcome",
                              "amount", "tier", "cause")
                )
                detail = m["event_type"]
                if highlights:
                    detail += f"  [{highlights}]"
                print(f"    {_dim(ts)}  {detail}")
        else:
            print(_dim("  No events recorded in this timeframe."))

        print()
    finally:
        conscious.close()


def _chat_fallback() -> None:
    """Fallback: show recent context for any unrecognized question."""
    conscious = _open_ro(CONSCIOUSNESS_DB)
    if conscious is None:
        print(_dim("  Consciousness database not available."))
        return

    try:
        print()
        print(_dim("  I don't recognize that question. Here's the most recent context:"))
        print()

        # Recent memories
        memories = conscious.execute(
            "SELECT timestamp, event_type, payload, importance "
            "FROM memories ORDER BY epoch DESC LIMIT 8"
        ).fetchall()

        # Pending decisions
        pending = conscious.execute(
            "SELECT id, timestamp, action, strategy, expected_roi, budget "
            "FROM decisions WHERE outcome IS NULL ORDER BY id DESC LIMIT 5"
        ).fetchall()

        if pending:
            print(_bold("  Pending Decisions:"))
            for d in pending:
                ts = datetime.fromisoformat(d["timestamp"]).strftime("%Y-%m-%d %H:%M")
                print(f"    #{d['id']} {d['action']} ({d['strategy']}) "
                      f"roi={d['expected_roi']:+.1%} budget=${d['budget']:.2f}")
            print()

        print(_bold("  Recent Events:"))
        for m in memories:
            ts = datetime.fromisoformat(m["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            try:
                payload = json.loads(m["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            highlights = ", ".join(
                f"{k}={v}" for k, v in payload.items()
                if k in ("action", "balance", "strategy", "outcome",
                          "amount", "tier", "cause", "error")
            )
            detail = m["event_type"]
            if highlights:
                detail += f"  [{highlights}]"
            imp_flag = _red("!") if m["importance"] >= 0.8 else " "
            print(f"    {imp_flag} {_dim(ts)}  {detail}")

        print()
    finally:
        conscious.close()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def print_not_initialized() -> None:
    """Print a message when databases don't exist."""
    print()
    print(f"  {_yellow('AEON has not been initialized.')}")
    print(f"  Run {_cyan('aeonctl start')} first.")
    print()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aeonctl",
        description="CLI management tool for Project AEON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              aeonctl                    Show status (default)
              aeonctl status --json      Machine-readable status
              aeonctl history --decisions  Show only decisions
              aeonctl history --full     Show full history
              aeonctl start              Start the agent in background
              aeonctl stop               Stop the running agent
              aeonctl chat               Interactive chat REPL
        """),
    )

    sub = parser.add_subparsers(dest="command", title="commands")

    # status
    status_p = sub.add_parser("status", help="Show agent wellbeing")
    status_p.add_argument("--json", action="store_true", help="Machine-readable output")
    status_p.set_defaults(func=cmd_status)

    # history
    hist_p = sub.add_parser("history", help="Show recent actions")
    hist_p.add_argument("--full", action="store_true", help="Show all entries")
    hist_p.add_argument("--decisions", action="store_true", help="Show only decisions")
    hist_p.add_argument("--events", action="store_true", help="Show only events")
    hist_p.add_argument("--json", action="store_true", help="Machine-readable output")
    hist_p.set_defaults(func=cmd_history)

    # start
    start_p = sub.add_parser("start", help="Start AEON agent in background")
    start_p.set_defaults(func=cmd_start)

    # stop
    stop_p = sub.add_parser("stop", help="Stop the running AEON agent")
    stop_p.set_defaults(func=cmd_stop)

    # chat
    chat_p = sub.add_parser("chat", help="Interactive chat REPL")
    chat_p.set_defaults(func=cmd_chat)

    return parser


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        # Default to status
        cmd_status(argparse.Namespace(json=False))
    elif hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
