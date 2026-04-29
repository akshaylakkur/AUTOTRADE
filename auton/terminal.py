"""Terminal protocol for AEON — graceful death sequence."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auton.aeon import AEON


class TerminalProtocol:
    """Handles irreversible shutdown when AEON's balance reaches zero."""

    def __init__(self, aeon: AEON) -> None:
        self.aeon = aeon
        self.logger = logging.getLogger("aeon.terminal")

    async def execute(self, balance: float) -> None:
        """Execute the full terminal protocol sequence."""
        self.logger.critical(
            "TERMINAL PROTOCOL EXECUTING",
            extra={"event": "terminal", "step": "begin", "balance": balance},
        )
        try:
            await self.liquidate_all()
            await self.export_ledger()
            await self.revoke_keys()
            await self.generate_obituary(balance)
            await self.shutdown()
        except Exception as exc:
            self.logger.critical(
                "Terminal protocol error — forcing shutdown",
                extra={"error": str(exc), "event": "terminal_error"},
            )
            await self.shutdown()

    async def liquidate_all(self) -> None:
        """Emit liquidation for all non-locked positions."""
        self.logger.critical(
            "Liquidating all non-locked positions",
            extra={"event": "terminal", "step": "liquidate"},
        )
        if self.aeon.reflexes is not None and hasattr(self.aeon.reflexes, "liquidate_all"):
            await self.aeon.reflexes.liquidate_all()
        elif self.aeon.limbs is not None and hasattr(self.aeon.limbs, "liquidate_all"):
            await self.aeon.limbs.liquidate_all()
        else:
            self.logger.warning(
                "No liquidation handler available",
                extra={"event": "terminal", "step": "liquidate", "status": "skipped"},
            )

    async def export_ledger(self) -> None:
        """Copy SQLite ledger to cold storage path."""
        self.logger.critical(
            "Exporting ledger to cold storage",
            extra={"event": "terminal", "step": "export_ledger"},
        )
        if self.aeon.ledger is None:
            self.logger.warning("No ledger to export")
            return

        cold_storage = (
            Path(self.aeon.config.get("cold_storage_path", "./cold_storage"))
            if self.aeon.config else Path("./cold_storage")
        )
        cold_storage.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dest = cold_storage / f"ledger_final_{timestamp}.db"

        ledger_path = getattr(self.aeon.ledger, "db_path", None)
        if ledger_path and Path(ledger_path).exists():
            shutil.copy2(ledger_path, dest)
            self.logger.critical(
                "Ledger exported",
                extra={
                    "event": "terminal",
                    "step": "export_ledger",
                    "destination": str(dest),
                },
            )
        else:
            if hasattr(self.aeon.ledger, "export"):
                await self.aeon.ledger.export(str(dest))
                self.logger.critical(
                    "Ledger exported via ledger.export",
                    extra={
                        "event": "terminal",
                        "step": "export_ledger",
                        "destination": str(dest),
                    },
                )
            else:
                self.logger.warning("Ledger path not available for export")

    async def revoke_keys(self) -> None:
        """Delete all API keys via the vault."""
        self.logger.critical(
            "Revoking all API keys",
            extra={"event": "terminal", "step": "revoke_keys"},
        )
        if self.aeon.vault is not None and hasattr(self.aeon.vault, "revoke_all_keys"):
            await self.aeon.vault.revoke_all_keys()
        else:
            self.logger.warning("No vault available to revoke keys")

    async def generate_obituary(self, balance: float) -> None:
        """Write a JSON obituary summarizing AEON's lifespan."""
        self.logger.critical(
            "Generating obituary",
            extra={"event": "terminal", "step": "obituary"},
        )

        lifespan = "N/A"
        total_pnl = 0.0
        if self.aeon._start_time:
            lifespan_seconds = (datetime.utcnow() - self.aeon._start_time).total_seconds()
            lifespan = f"{lifespan_seconds:.0f}s"

        if self.aeon.ledger is not None and hasattr(self.aeon.ledger, "get_total_pnl"):
            total_pnl = self.aeon.ledger.get_total_pnl()

        tier = self.aeon.get_current_tier(balance)
        cause = self._determine_cause_of_death(balance, total_pnl)

        obituary = {
            "name": "AEON",
            "version": "1.0.0-alpha",
            "born": self.aeon._start_time.isoformat() + "Z" if self.aeon._start_time else None,
            "died": datetime.utcnow().isoformat() + "Z",
            "lifespan": lifespan,
            "final_balance": balance,
            "total_pnl": total_pnl,
            "tier_reached": tier,
            "cause_of_death": cause,
            "seed_balance": self.aeon.SEED_BALANCE,
        }

        cold_storage = (
            Path(self.aeon.config.get("cold_storage_path", "./cold_storage"))
            if self.aeon.config else Path("./cold_storage")
        )
        cold_storage.mkdir(parents=True, exist_ok=True)

        obituary_path = cold_storage / "obituary.json"
        with open(obituary_path, "w") as f:
            json.dump(obituary, f, indent=2)

        self.logger.critical(
            "Obituary written",
            extra={
                "event": "terminal",
                "step": "obituary",
                "path": str(obituary_path),
                "cause": cause,
                "tier": tier,
                "total_pnl": total_pnl,
            },
        )

    def _determine_cause_of_death(self, balance: float, total_pnl: float) -> str:
        if balance <= 0:
            if total_pnl < -self.aeon.SEED_BALANCE * 0.9:
                return "The Catastrophic Trade — balance wiped by single massive loss"
            return "The Slow Bleed — operating costs exceeded revenue until depletion"
        if total_pnl < -self.aeon.SEED_BALANCE * 0.5:
            return "The Death Spiral — panic realization of losses accelerated decline"
        return "Unknown — terminal protocol triggered without clear singular cause"

    async def shutdown(self) -> None:
        """Graceful exit."""
        self.logger.critical(
            "Terminal shutdown",
            extra={"event": "terminal", "step": "shutdown"},
        )
        await self.aeon.shutdown()
