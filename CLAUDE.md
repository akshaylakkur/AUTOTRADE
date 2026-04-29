# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **Project ÆON** (Autonomous Economic Operating Node) v1.0.0-alpha — a Python-based autonomous economic agent. ÆON starts with a $50.00 seed balance and must generate profit through trading or other economic activity to survive. If the balance reaches zero, the system executes an irreversible terminal protocol (shutdown, ledger export, key revocation, obituary generation). There are no human bailouts.

## Common Commands

- **Run tests**: `pytest`
- **Run single test**: `pytest tests/test_<module>.py -k <test_name>`
- **Run the orchestrator**: `python -m auton.aeon`
- **Build Docker image**: `docker build -t auton-aeon .`
- **Run Docker container**: `docker run --rm -v $(pwd)/data:/app/data auton-aeon`

The project uses `pyproject.toml` only for pytest configuration (`asyncio_mode = "auto"`). Dependencies are listed in `requirements.txt`. There is no Makefile or other build automation.

## Architecture

ÆON follows a modular, event-driven architecture organized around anatomical metaphors. The main orchestrator `auton/aeon.py` instantiates and coordinates all subsystems.

### Subsystems

| Module | Path | Responsibility |
|--------|------|----------------|
| **Core** | `auton/core/` | Event bus (`event_bus.py`), lifecycle state machine (`state_machine.py`), constants (`constants.py`), configuration (`config.py`), and typed event definitions (`events.py`). |
| **Ledger** | `auton/ledger/` | `MasterWallet` (SQLite-backed, WAL mode), P&L engine, burn rate analyzer, cost tracker. The single source of truth for all balances. |
| **Cortex** | `auton/cortex/` | Strategic planner, tactical executor, meta-cognition module, model router, failure recovery. The reasoning engine. |
| **Reflexes** | `auton/reflexes/` | Circuit breakers, emergency liquidator, stop-loss engine, position sizer, API health monitor. Fast execution without deliberation. |
| **Senses** | `auton/senses/` | Data ingestion connectors. `market_data/` has exchange feeds (Binance, Coinbase Pro); `sentiment/` has social streams (Twitter/X). |
| **Limbs** | `auton/limbs/` | External action interfaces. `trading/` has exchange executors (Binance spot); `commerce/` has payment/product connectors (Stripe). |
| **Analytics** | `auton/analytics/` | Alpha generation engine, revenue engine, risk management, backtester. |
| **Metamind** | `auton/metamind/` | Self-analysis, adaptation engine, strategy journal, code generator, evolution gate. |
| **Security** | `auton/security/` | Encrypted vault, spend caps, sandboxing, audit trail. |
| **Terminal** | `auton/terminal.py` | Irreversible shutdown protocol triggered at zero balance. |

### Key Architectural Patterns

1. **Lifecycle State Machine**: `StateMachine` in `auton/core/state_machine.py` enforces valid transitions between `INIT`, `RUNNING`, `HIBERNATE`, and `TERMINAL`. The orchestrator transitions through these states during startup, circuit breaker triggers, and shutdown.

2. **Event Bus**: `auton/core/event_bus.py` provides async pub/sub for decoupled inter-module communication. Events are typed classes (defined in `auton/core/events.py`). There is also a legacy string-based event bus in `auton/events.py` that should not be used for new code.

3. **Tier-Gated Capabilities**: `auton/core/constants.py` defines balance thresholds (`TIER_THRESHOLDS`), compute budgets (`TIER_COMPUTE_BUDGETS`), and risk limits (`RISK_LIMITS`) for five operational tiers. Demotion locks new capabilities but preserves existing positions.

4. **SQLite Ledger**: `MasterWallet` (`auton/ledger/master_wallet.py`) uses SQLite with WAL mode and one connection per thread. Every credit/debit returns an immutable `CostReceipt`. The ledger lives at `data/aeon_ledger.db` by default.

5. **Terminal Protocol**: When balance reaches zero, `TerminalProtocol` (orchestrator version in `auton/aeon.py`, standalone version in `auton/terminal.py`) liquidates positions, exports the ledger to `cold_storage/`, revokes API keys, writes an `obituary.json`, and shuts down.

## Testing

Tests are in `tests/` and use `pytest` with `pytest-asyncio`. Each major module has a corresponding `test_<module>.py` file. The tests for `test_aeon.py` mock subsystems heavily to test the orchestrator in isolation.

- The project uses `.venv` for its virtual environment.
- `data/` and `cold_storage/` are in `.gitignore` and created at runtime.

## Important Files

- `auton/aeon.py` — Main orchestrator. Entry point is `python -m auton.aeon`.
- `auton/core/constants.py` — All tier thresholds, risk limits, and seed balance ($50.00).
- `SPECIFICATION.md` — Full architectural specification and design philosophy.
