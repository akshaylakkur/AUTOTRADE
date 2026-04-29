"""SimulatedWallet — in-memory wallet that mirrors MasterWallet API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from auton.ledger.exceptions import InsufficientFundsError, LedgerError
from auton.ledger.master_wallet import CostReceipt


class SimulatedWallet:
    """In-memory wallet for simulations.  Mirrors :class:`MasterWallet` API.

    Uses fake currency and can be fully reset between simulation runs.
    """

    def __init__(self, initial_balance: float = 0.0) -> None:
        self._txs: list[_SimTx] = []
        self._next_id = 1
        if initial_balance > 0:
            self.credit(initial_balance, reason="seed")

    # ------------------------------------------------------------------ #
    # Public API (mirrors MasterWallet)
    # ------------------------------------------------------------------ #
    def get_balance(self) -> float:
        """Return the current running balance."""
        if not self._txs:
            return 0.0
        return self._txs[-1].running_balance

    def credit(self, amount: float, reason: str) -> CostReceipt:
        """Add fake funds and return a receipt."""
        if amount <= 0:
            raise LedgerError("Credit amount must be positive")

        new_balance = self.get_balance() + amount
        tx = _SimTx(
            id=self._next_id,
            timestamp=datetime.now(timezone.utc),
            amount=amount,
            reason=reason,
            running_balance=new_balance,
            type="CREDIT",
        )
        self._txs.append(tx)
        self._next_id += 1
        return tx.to_receipt()

    def debit(self, amount: float, reason: str) -> CostReceipt:
        """Deduct fake funds and return a receipt.

        Raises:
            InsufficientFundsError: If the debit would drop the balance below zero.
        """
        if amount <= 0:
            raise LedgerError("Debit amount must be positive")

        current = self.get_balance()
        if current < amount:
            raise InsufficientFundsError(
                f"Balance {current:.4f} insufficient for debit {amount:.4f}"
            )
        new_balance = current - amount
        tx = _SimTx(
            id=self._next_id,
            timestamp=datetime.now(timezone.utc),
            amount=amount,
            reason=reason,
            running_balance=new_balance,
            type="DEBIT",
        )
        self._txs.append(tx)
        self._next_id += 1
        return tx.to_receipt()

    def get_transaction_history(self, limit: int = 100) -> Iterable[CostReceipt]:
        """Yield the most recent transactions, newest first."""
        for tx in reversed(self._txs[-limit:]):
            yield tx.to_receipt()

    # ------------------------------------------------------------------ #
    # Simulation-specific helpers
    # ------------------------------------------------------------------ #
    def reset(self, initial_balance: float = 0.0) -> None:
        """Clear all state and optionally seed a new balance."""
        self._txs.clear()
        self._next_id = 1
        if initial_balance > 0:
            self.credit(initial_balance, reason="seed")

    def get_transaction_count(self) -> int:
        """Return the total number of transactions recorded."""
        return len(self._txs)


@dataclass
class _SimTx:
    """Internal transaction record for the simulated wallet."""

    id: int
    timestamp: datetime
    amount: float
    reason: str
    running_balance: float
    type: str  # 'CREDIT' or 'DEBIT'

    def to_receipt(self) -> CostReceipt:
        return CostReceipt(
            id=self.id,
            timestamp=self.timestamp,
            amount=self.amount,
            reason=self.reason,
            running_balance=self.running_balance,
        )
