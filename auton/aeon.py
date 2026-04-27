"""Main orchestrator for Project ÆON."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auton.analytics.alpha_engine import AlphaEngine
from auton.analytics.revenue_engine import RevenueEngine
from auton.analytics.risk_management import RiskManager
from auton.core.config import TierGate
from auton.core.constants import SEED_BALANCE
from auton.core.event_bus import EventBus
from auton.core.events import BalanceChanged, Hibernate, Shutdown, TradeSignal
from auton.core.state_machine import LifecycleState, StateMachine
from auton.cortex.executor import TacticalExecutor
from auton.cortex.meta_cognition import MetaCognition
from auton.cortex.model_router import ModelRouter
from auton.cortex.planner import StrategicPlanner
from auton.ledger.burn_analyzer import BurnAnalyzer
from auton.ledger.cost_tracker import CostTracker
from auton.ledger.exceptions import InsufficientFundsError
from auton.ledger.master_wallet import MasterWallet
from auton.ledger.pnl_engine import PnLEngine
from auton.metamind.adaption_engine import AdaptionEngine
from auton.reflexes.circuit_breakers import CircuitBreakers
from auton.reflexes.emergency_liquidator import EmergencyLiquidator
from auton.reflexes.position_sizer import PositionSizer
from auton.reflexes.stop_loss import StopLossEngine
from auton.security.spend_caps import SpendCaps
from auton.security.vault import Vault

logger = logging.getLogger("aeon")


class TerminalProtocol:
    """Handles the irreversible shutdown sequence when balance reaches zero."""

    def __init__(self, wallet: MasterWallet, vault: Vault, ledger_path: Path) -> None:
        self._wallet = wallet
        self._vault = vault
        self._ledger_path = ledger_path

    async def execute(self, cause: str) -> None:
        """Run the terminal protocol.

        Steps:
        1. Liquidate all positions.
        2. Export final ledger to cold storage.
        3. Revoke all API keys.
        4. Generate and print obituary.
        5. Shutdown.
        """
        logger.critical("TERMINAL PROTOCOL INITIATED. Cause: %s", cause)

        # 1. Liquidation is handled by emergency liquidator before this is called

        # 2. Export ledger
        cold_storage = Path("cold_storage")
        cold_storage.mkdir(exist_ok=True)
        export_path = cold_storage / f"final_ledger_{datetime.now(timezone.utc).isoformat()}.db"
        try:
            import shutil
            shutil.copy(self._ledger_path, export_path)
            logger.info("Ledger exported to %s", export_path)
        except Exception as e:
            logger.error("Failed to export ledger: %s", e)

        # 3. Revoke keys
        try:
            self._vault.revoke_all()
            logger.info("All API keys revoked.")
        except Exception as e:
            logger.error("Failed to revoke keys: %s", e)

        # 4. Obituary
        obituary = await self._generate_obituary(cause)
        obituary_path = cold_storage / "obituary.json"
        obituary_path.write_text(json.dumps(obituary, indent=2))
        logger.info("Obituary generated.\n%s", json.dumps(obituary, indent=2))

        # 5. Shutdown signal
        logger.critical("ÆON signing off.")

    async def _generate_obituary(self, cause: str) -> dict[str, Any]:
        balance = self._wallet.get_balance()
        history = self._wallet.get_transaction_history(limit=10000)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cause_of_death": cause,
            "final_balance": balance,
            "total_transactions": len(history),
            "first_transaction": history[0]["timestamp"] if history else None,
            "last_transaction": history[-1]["timestamp"] if history else None,
        }


class AEON:
    """Autonomous Economic Operating Node — the main runtime orchestrator."""

    def __init__(self, config_path: str | None = None) -> None:
        self._event_bus = EventBus()
        self._state_machine = StateMachine()
        self._wallet = MasterWallet(db_path="data/aeon_ledger.db")
        self._cost_tracker = CostTracker(self._wallet)
        self._pnl = PnLEngine()
        self._burn = BurnAnalyzer()
        self._tier_gate = TierGate()
        self._spend_caps = SpendCaps()
        self._vault = Vault(db_path="data/aeon_vault.db")
        self._terminal = TerminalProtocol(self._wallet, self._vault, Path("data/aeon_ledger.db"))

        # Reflexes
        self._stop_loss = StopLossEngine(event_bus=self._event_bus)
        self._liquidator = EmergencyLiquidator(event_bus=self._event_bus)
        self._position_sizer = PositionSizer()
        self._circuit_breakers = CircuitBreakers(event_bus=self._event_bus)

        # Cortex
        self._planner = StrategicPlanner()
        self._executor = TacticalExecutor(event_bus=self._event_bus)
        self._meta = MetaCognition()
        self._model_router = ModelRouter(meta_cognition=self._meta)

        # Analytics
        self._alpha = AlphaEngine()
        self._revenue = RevenueEngine()
        self._risk = RiskManager()

        # Metamind
        self._adaption = AdaptionEngine(source_dir="auton")

        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []

    async def initialize(self, seed_balance: float = SEED_BALANCE) -> None:
        """Initialize ledger with seed capital and transition to INIT."""
        Path("data").mkdir(exist_ok=True)
        self._wallet.credit(seed_balance, reason="seed_capital")
        self._state_machine.transition_to(LifecycleState.INIT)
        logger.info("ÆON initialized with balance $%.2f", seed_balance)

    async def start(self) -> None:
        """Enter RUNNING state and begin the economic loop."""
        if self._state_machine.get_current_state() != LifecycleState.INIT:
            raise RuntimeError("Must initialize before starting")

        self._state_machine.transition_to(LifecycleState.RUNNING)
        self._running = True

        # Register signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Subscribe to critical events
        self._event_bus.subscribe(BalanceChanged, self._on_balance_changed)
        self._event_bus.subscribe(Hibernate, self._on_hibernate)
        self._event_bus.subscribe(Shutdown, self._on_shutdown)

        # Launch background tasks
        self._tasks = [
            asyncio.create_task(self._planning_loop()),
            asyncio.create_task(self._reflex_loop()),
            asyncio.create_task(self._monitor_loop()),
            asyncio.create_task(self._adaptation_loop()),
        ]

        logger.info("ÆON is RUNNING.")
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _planning_loop(self) -> None:
        """Periodic strategic planning and execution."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Plan every minute
                balance = self._wallet.get_balance()
                tier = self._tier_gate.get_tier(balance)

                plan = self._planner.plan_objectives(balance, tier)
                logger.info("Plan: %s", plan)

                # Check terminal condition
                if balance <= 0:
                    await self._terminal.execute("balance_zero")
                    await self.shutdown()
                    return

                # Check hibernation
                if self._circuit_breakers.is_hibernating():
                    logger.warning("In hibernation — skipping planning cycle.")
                    continue

                # Evaluate opportunities (mock for skeleton)
                # In a full implementation, senses would feed data here
            except Exception as e:
                logger.exception("Planning loop error: %s", e)

    async def _reflex_loop(self) -> None:
        """Fast reflex monitoring."""
        while self._running:
            try:
                await asyncio.sleep(5)
                balance = self._wallet.get_balance()

                # Survival threshold check
                if balance < SEED_BALANCE * 0.2:
                    logger.critical("Balance below survival threshold! Triggering liquidation.")
                    await self._liquidator.liquidate_all_positions("survival_threshold")
            except Exception as e:
                logger.exception("Reflex loop error: %s", e)

    async def _monitor_loop(self) -> None:
        """Continuous burn rate and tier monitoring."""
        while self._running:
            try:
                await asyncio.sleep(30)
                balance = self._wallet.get_balance()
                burn = self._burn.get_burn_rate(days=1)
                runway = self._burn.project_time_to_death(balance, burn)
                logger.info("Balance=$%.2f | Burn=$%.4f/day | Runway=%.1f hours", balance, burn, runway)
            except Exception as e:
                logger.exception("Monitor loop error: %s", e)

    async def _adaptation_loop(self) -> None:
        """Periodic self-analysis and adaptation."""
        while self._running:
            try:
                await asyncio.sleep(3600)  # Once per hour
                balance = self._wallet.get_balance()
                if balance <= 0:
                    continue
                # Metamind reviews its own code for optimization opportunities
                logger.info("Running self-analysis...")
                # Skeleton: actual adaptation would trigger metamind pipeline
            except Exception as e:
                logger.exception("Adaptation loop error: %s", e)

    async def _on_balance_changed(self, event: BalanceChanged) -> None:
        """Handle balance change events."""
        logger.info("Balance changed: $%.2f → $%.2f", event.old_balance, event.new_balance)

    async def _on_hibernate(self, event: Hibernate) -> None:
        """Enter hibernation state."""
        logger.warning("Hibernation triggered: %s", event.reason)
        self._state_machine.transition_to(LifecycleState.HIBERNATE)

    async def _on_shutdown(self, event: Shutdown) -> None:
        """Handle shutdown event."""
        logger.info("Shutdown event received: %s", event.reason)
        await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        if not self._running:
            return
        self._running = False
        logger.info("Shutting down ÆON...")

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        self._state_machine.transition_to(LifecycleState.TERMINAL)
        logger.info("ÆON shutdown complete.")

    @property
    def balance(self) -> float:
        return self._wallet.get_balance()

    @property
    def state(self) -> LifecycleState:
        return self._state_machine.get_current_state()


async def main() -> None:
    """Entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    aeon = AEON()
    await aeon.initialize()
    await aeon.start()


if __name__ == "__main__":
    asyncio.run(main())
