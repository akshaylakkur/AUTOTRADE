"""Ledger stub for AEON."""

from typing import Any


class Ledger:
    """Minimal ledger stub."""

    def __init__(self, event_bus: Any, db_path: str = "./data/ledger.db") -> None:
        self.event_bus = event_bus
        self.db_path = db_path
        self._balance = 0.0
        self._total_pnl = 0.0
        self._burn_rate = 0.0

    async def initialize(self, seed_balance: float = 50.0) -> None:
        self._balance = seed_balance
        self._total_pnl = 0.0

    def get_balance(self) -> float:
        return self._balance

    def get_total_pnl(self) -> float:
        return self._total_pnl

    def get_burn_rate(self) -> float:
        return self._burn_rate

    async def reconcile(self) -> None:
        pass

    async def export(self, dest: str) -> None:
        import shutil
        from pathlib import Path
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        if Path(self.db_path).exists():
            shutil.copy2(self.db_path, dest)

    async def shutdown(self) -> None:
        pass
