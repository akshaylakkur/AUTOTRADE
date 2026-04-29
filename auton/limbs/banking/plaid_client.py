"""Plaid banking integration for Project ÆON.

All credentials are read from environment variables. The limb supports
both sandbox and live Plaid environments and implements safety gates:
daily transfer limits and confirmation delays for large transfers.
"""

from __future__ import annotations

import os
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from auton.limbs.base_limb import BaseLimb
from auton.security.spend_caps import SpendGuard
from auton.security.audit_trail import AuditLog


_PLAID_BASE_URLS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


@dataclass(frozen=True, slots=True)
class BankAccount:
    account_id: str
    name: str
    mask: str
    subtype: str
    balances: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BankTransaction:
    transaction_id: str
    account_id: str
    amount: float
    iso_currency_code: str
    date: str
    name: str
    pending: bool
    category: list[str] | None = None
    merchant_name: str | None = None
    payment_channel: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ACHTransfer:
    transfer_id: str
    status: str
    amount: float
    origination_account_id: str
    description: str
    scheduled_date: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class PlaidLimb(BaseLimb):
    """Async limb for Plaid banking operations.

    Credentials are read from environment variables:
      - PLAID_CLIENT_ID
      - PLAID_SECRET
      - PLAID_ENV (sandbox | development | production)
      - PLAID_ACCESS_TOKEN (optional; if absent, skeleton mode)

    Safety features:
      - Daily transfer limits via SpendGuard
      - Confirmation delays for large transfers
      - Audit trail logging for every operation
    """

    # Large transfer threshold requiring confirmation delay
    LARGE_TRANSFER_THRESHOLD: float = 100.0
    CONFIRMATION_DELAY_SECONDS: float = 300.0  # 5 minutes

    def __init__(
        self,
        *,
        client_id: str | None = None,
        secret: str | None = None,
        env: str | None = None,
        access_token: str | None = None,
        spend_guard: SpendGuard | None = None,
        audit_log: AuditLog | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._client_id = client_id or os.environ.get("PLAID_CLIENT_ID")
        self._secret = secret or os.environ.get("PLAID_SECRET")
        self._env = (env or os.environ.get("PLAID_ENV", "sandbox")).lower()
        self._access_token = access_token or os.environ.get("PLAID_ACCESS_TOKEN")
        self._spend_guard = spend_guard
        self._audit_log = audit_log or AuditLog()

        self._base_url = _PLAID_BASE_URLS.get(self._env, _PLAID_BASE_URLS["sandbox"])
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=30.0)
        self._pending_confirmations: dict[str, dict[str, Any]] = {}

        # Skeleton / mock state
        self._mock_accounts: dict[str, dict[str, Any]] = {}
        self._mock_transactions: list[dict[str, Any]] = []
        self._mock_transfers: dict[str, dict[str, Any]] = {}
        self._mock_counter = 0

    # ------------------------------------------------------------------ #
    # BaseLimb interface
    # ------------------------------------------------------------------ #

    async def execute(self, action: Any) -> Any:
        method = action.get("method")
        kwargs = action.get("kwargs", {})
        if method == "get_balance":
            return await self.get_balance(**kwargs)
        if method == "get_transactions":
            return await self.get_transactions(**kwargs)
        if method == "initiate_ach_transfer":
            return await self.initiate_ach_transfer(**kwargs)
        if method == "get_transfer_status":
            return await self.get_transfer_status(**kwargs)
        raise ValueError(f"Unknown action: {method}")

    async def get_cost_estimate(self, action: Any) -> float:
        return 0.0

    def is_available(self, tier: int) -> bool:
        return tier >= 1

    async def health_check(self) -> dict[str, Any]:
        if self._client_id is None or self._secret is None:
            return {"status": "skeleton", "mode": "mock"}
        try:
            start = asyncio.get_event_loop().time()
            r = await self._post("/item/get", {"access_token": self._access_token or "mock"})
            latency = (asyncio.get_event_loop().time() - start) * 1000
            status = "healthy" if r.status_code in (200, 400) else "degraded"
            return {
                "status": status,
                "mode": self._env,
                "latency_ms": round(latency, 2),
                "status_code": r.status_code,
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Account & balance
    # ------------------------------------------------------------------ #

    async def get_balance(self, account_ids: list[str] | None = None) -> list[BankAccount]:
        """Fetch current balances for linked accounts."""
        self._emit("limb.bank.balance.requested", {"account_ids": account_ids})
        log_id = self._audit_log.pre_log("plaid.balance", {"account_ids": account_ids})

        try:
            if self._access_token:
                accounts = await self._live_get_balance(account_ids)
            else:
                accounts = self._mock_get_balance(account_ids)

            self._audit_log.post_log(log_id, {"accounts": [a.account_id for a in accounts]})
            self._emit("limb.bank.balance.fetched", {"count": len(accounts)})
            return accounts
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    # ------------------------------------------------------------------ #
    # Transactions
    # ------------------------------------------------------------------ #

    async def get_transactions(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        account_ids: list[str] | None = None,
        count: int = 100,
        offset: int = 0,
    ) -> list[BankTransaction]:
        """Fetch transaction history within a date range."""
        end = end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start = start_date or (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

        self._emit("limb.bank.transactions.requested", {"start": start, "end": end})
        log_id = self._audit_log.pre_log("plaid.transactions", {"start": start, "end": end, "count": count})

        try:
            if self._access_token:
                txs = await self._live_get_transactions(start, end, account_ids, count, offset)
            else:
                txs = self._mock_get_transactions(start, end, account_ids, count, offset)

            self._audit_log.post_log(log_id, {"count": len(txs)})
            self._emit("limb.bank.transactions.fetched", {"count": len(txs)})
            return txs
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    # ------------------------------------------------------------------ #
    # ACH transfers
    # ------------------------------------------------------------------ #

    async def initiate_ach_transfer(
        self,
        amount: float,
        account_id: str,
        direction: str,  # "credit" or "debit"
        description: str,
        skip_confirmation: bool = False,
    ) -> ACHTransfer | dict[str, Any]:
        """Initiate an ACH transfer with safety gates.

        Daily transfer limits are enforced via SpendGuard.
        Large transfers (>$100) require a confirmation delay unless
        *skip_confirmation* is True (e.g. after the delay has elapsed).

        Returns a dict with ``status: "pending_confirmation"`` when a
        confirmation delay is required, otherwise an :class:`ACHTransfer`.
        """
        if amount <= 0:
            raise ValueError("Transfer amount must be positive")

        self._emit("limb.bank.transfer.requested", {
            "amount": amount,
            "direction": direction,
            "account_id": account_id,
        })
        log_id = self._audit_log.pre_log("plaid.transfer", {
            "amount": amount,
            "direction": direction,
            "account_id": account_id,
            "description": description,
        })

        # 1. Daily transfer limit check
        if self._spend_guard:
            allowed, reason = self._spend_guard.quote_spend("bank_transfer", amount)
            if not allowed:
                self._audit_log.post_log(log_id, {"blocked": True, "reason": reason})
                raise TransferLimitExceeded(f"Transfer blocked by spend guard: {reason}")

        # 2. Large transfer confirmation delay
        if amount >= self.LARGE_TRANSFER_THRESHOLD and not skip_confirmation:
            confirmation_id = f"confirm_{account_id}_{int(datetime.now(timezone.utc).timestamp())}"
            self._pending_confirmations[confirmation_id] = {
                "amount": amount,
                "account_id": account_id,
                "direction": direction,
                "description": description,
                "requested_at": datetime.now(timezone.utc).isoformat(),
            }
            self._audit_log.post_log(log_id, {"pending_confirmation": True, "confirmation_id": confirmation_id})
            self._emit("limb.bank.transfer.pending_confirmation", {
                "confirmation_id": confirmation_id,
                "amount": amount,
                "delay_seconds": self.CONFIRMATION_DELAY_SECONDS,
            })
            return {
                "status": "pending_confirmation",
                "confirmation_id": confirmation_id,
                "amount": amount,
                "delay_seconds": self.CONFIRMATION_DELAY_SECONDS,
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=self.CONFIRMATION_DELAY_SECONDS)).isoformat(),
            }

        try:
            if self._access_token:
                transfer = await self._live_initiate_ach_transfer(amount, account_id, direction, description)
            else:
                transfer = self._mock_initiate_ach_transfer(amount, account_id, direction, description)

            # Record spend if guard is present
            if self._spend_guard:
                self._spend_guard.record_spend("bank_transfer", amount)

            self._audit_log.post_log(log_id, {"transfer_id": transfer.transfer_id, "status": transfer.status})
            self._emit("limb.bank.transfer.executed", {
                "transfer_id": transfer.transfer_id,
                "amount": amount,
                "direction": direction,
            })
            return transfer
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    async def confirm_transfer(self, confirmation_id: str) -> ACHTransfer | dict[str, Any]:
        """Confirm a pending transfer after the delay has elapsed."""
        pending = self._pending_confirmations.get(confirmation_id)
        if not pending:
            raise ValueError(f"No pending transfer found for confirmation_id: {confirmation_id}")

        requested_at = datetime.fromisoformat(pending["requested_at"])
        elapsed = (datetime.now(timezone.utc) - requested_at).total_seconds()
        if elapsed < self.CONFIRMATION_DELAY_SECONDS:
            raise TransferConfirmationError(
                f"Confirmation delay not elapsed: {elapsed:.0f}s < {self.CONFIRMATION_DELAY_SECONDS:.0f}s"
            )

        # Remove from pending and re-initiate with skip_confirmation
        del self._pending_confirmations[confirmation_id]
        return await self.initiate_ach_transfer(
            amount=pending["amount"],
            account_id=pending["account_id"],
            direction=pending["direction"],
            description=pending["description"],
            skip_confirmation=True,
        )

    async def get_transfer_status(self, transfer_id: str) -> dict[str, Any]:
        if self._access_token:
            r = await self._post("/transfer/get", {"transfer_id": transfer_id})
            r.raise_for_status()
            return r.json()
        return self._mock_transfers.get(transfer_id, {})

    async def handle_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process a Plaid webhook payload for real-time transaction updates."""
        webhook_type = payload.get("webhook_type")
        webhook_code = payload.get("webhook_code")
        item_id = payload.get("item_id")

        self._emit("limb.bank.webhook.received", {
            "webhook_type": webhook_type,
            "webhook_code": webhook_code,
            "item_id": item_id,
        })
        log_id = self._audit_log.pre_log("plaid.webhook", {"type": webhook_type, "code": webhook_code})

        result = {
            "webhook_type": webhook_type,
            "webhook_code": webhook_code,
            "handled": True,
        }

        if webhook_type == "TRANSACTIONS":
            if webhook_code == "INITIAL_UPDATE":
                result["action"] = "fetch_new_transactions"
            elif webhook_code == "HISTORICAL_UPDATE":
                result["action"] = "fetch_historical_transactions"
            elif webhook_code == "DEFAULT_UPDATE":
                result["action"] = "fetch_default_update"
            elif webhook_code == "REMOVED":
                result["action"] = "remove_transactions"
                result["removed_transaction_ids"] = payload.get("removed_transaction_ids", [])
        elif webhook_type == "TRANSFER":
            result["action"] = "update_transfer_status"
            result["transfer_id"] = payload.get("transfer_id")
        elif webhook_type == "BALANCE":
            result["action"] = "refresh_balances"

        self._audit_log.post_log(log_id, result)
        self._emit("limb.bank.webhook.processed", result)
        return result

    # ------------------------------------------------------------------ #
    # Live implementations
    # ------------------------------------------------------------------ #

    async def _live_get_balance(self, account_ids: list[str] | None = None) -> list[BankAccount]:
        body: dict[str, Any] = {"access_token": self._access_token}
        if account_ids:
            body["options"] = {"account_ids": account_ids}
        r = await self._post("/accounts/balance/get", body)
        r.raise_for_status()
        data = r.json()
        return [_to_bank_account(a) for a in data.get("accounts", [])]

    async def _live_get_transactions(
        self,
        start_date: str,
        end_date: str,
        account_ids: list[str] | None,
        count: int,
        offset: int,
    ) -> list[BankTransaction]:
        body: dict[str, Any] = {
            "access_token": self._access_token,
            "start_date": start_date,
            "end_date": end_date,
            "options": {"count": count, "offset": offset},
        }
        if account_ids:
            body["options"]["account_ids"] = account_ids

        r = await self._post("/transactions/get", body)
        r.raise_for_status()
        data = r.json()
        return [_to_bank_transaction(t) for t in data.get("transactions", [])]

    async def _live_initiate_ach_transfer(
        self,
        amount: float,
        account_id: str,
        direction: str,
        description: str,
    ) -> ACHTransfer:
        # Plaid Transfer API requires a Plaid client ID + secret + access token.
        # This is a simplified pattern; production code would use the Transfer API.
        body = {
            "access_token": self._access_token,
            "account_id": account_id,
            "amount": str(amount),
            "description": description,
            "ach_class": "ccd",
        }
        r = await self._post("/transfer/authorization/create", body)
        r.raise_for_status()
        auth = r.json()

        if auth.get("authorization") and auth["authorization"].get("id"):
            transfer_body = {
                "access_token": self._access_token,
                "account_id": account_id,
                "authorization_id": auth["authorization"]["id"],
                "description": description,
            }
            tr = await self._post("/transfer/create", transfer_body)
            tr.raise_for_status()
            transfer_data = tr.json().get("transfer", {})
            return ACHTransfer(
                transfer_id=transfer_data.get("id", ""),
                status=transfer_data.get("status", "unknown"),
                amount=float(transfer_data.get("amount", amount)),
                origination_account_id=account_id,
                description=description,
                raw=transfer_data,
            )
        raise TransferError(f"Transfer authorization failed: {auth}")

    # ------------------------------------------------------------------ #
    # Mock implementations
    # ------------------------------------------------------------------ #

    def _mock_get_balance(self, account_ids: list[str] | None = None) -> list[BankAccount]:
        if not self._mock_accounts:
            self._mock_accounts["mock_acc_1"] = {
                "account_id": "mock_acc_1",
                "name": "Mock Checking",
                "mask": "0000",
                "subtype": "checking",
                "balances": {
                    "available": 5000.0,
                    "current": 5100.0,
                    "iso_currency_code": "USD",
                },
            }
        accounts = list(self._mock_accounts.values())
        if account_ids:
            accounts = [a for a in accounts if a["account_id"] in account_ids]
        return [_to_bank_account(a) for a in accounts]

    def _mock_get_transactions(
        self,
        start_date: str,
        end_date: str,
        account_ids: list[str] | None,
        count: int,
        offset: int,
    ) -> list[BankTransaction]:
        if not self._mock_transactions:
            now = datetime.now(timezone.utc)
            for i in range(10):
                self._mock_transactions.append({
                    "transaction_id": f"mock_tx_{i}",
                    "account_id": "mock_acc_1",
                    "amount": 25.0 + i,
                    "iso_currency_code": "USD",
                    "date": (now - timedelta(days=i)).strftime("%Y-%m-%d"),
                    "name": f"Mock Merchant {i}",
                    "pending": False,
                    "category": ["Transfer", "Debit"],
                    "merchant_name": f"Mock Merchant {i}",
                    "payment_channel": "online",
                })
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        txs = [
            t for t in self._mock_transactions
            if start_dt <= datetime.strptime(t["date"], "%Y-%m-%d").date() <= end_dt
        ]
        txs = txs[offset:offset + count]
        if account_ids:
            txs = [t for t in txs if t["account_id"] in account_ids]
        return [_to_bank_transaction(t) for t in txs]

    def _mock_initiate_ach_transfer(
        self,
        amount: float,
        account_id: str,
        direction: str,
        description: str,
    ) -> ACHTransfer:
        self._mock_counter += 1
        transfer_id = f"mock_transfer_{self._mock_counter}"
        self._mock_transfers[transfer_id] = {
            "transfer_id": transfer_id,
            "status": "pending",
            "amount": amount,
            "account_id": account_id,
            "direction": direction,
            "description": description,
        }
        return ACHTransfer(
            transfer_id=transfer_id,
            status="pending",
            amount=amount,
            origination_account_id=account_id,
            description=description,
            raw=self._mock_transfers[transfer_id],
        )

    # ------------------------------------------------------------------ #
    # HTTP helpers
    # ------------------------------------------------------------------ #

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        body = dict(body)
        body["client_id"] = self._client_id
        body["secret"] = self._secret
        return await self._client.post(path, json=body)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> PlaidLimb:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _to_bank_account(raw: dict[str, Any]) -> BankAccount:
    return BankAccount(
        account_id=raw["account_id"],
        name=raw["name"],
        mask=raw.get("mask", "****"),
        subtype=raw.get("subtype", ""),
        balances=raw.get("balances", {}),
    )


def _to_bank_transaction(raw: dict[str, Any]) -> BankTransaction:
    return BankTransaction(
        transaction_id=raw["transaction_id"],
        account_id=raw["account_id"],
        amount=raw.get("amount", 0.0),
        iso_currency_code=raw.get("iso_currency_code", "USD"),
        date=raw.get("date", ""),
        name=raw.get("name", ""),
        pending=raw.get("pending", False),
        category=raw.get("category"),
        merchant_name=raw.get("merchant_name"),
        payment_channel=raw.get("payment_channel"),
        raw=raw,
    )


class TransferError(Exception):
    """Raised when a transfer operation fails."""


class TransferLimitExceeded(Exception):
    """Raised when a transfer exceeds daily or configured limits."""


class TransferConfirmationError(Exception):
    """Raised when a transfer confirmation fails or delay is not elapsed."""
