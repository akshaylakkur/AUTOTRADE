"""Tests for the banking and reconciliation modules."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from auton.ledger.master_wallet import CostReceipt, MasterWallet
from auton.limbs.banking.plaid_client import (
    ACHTransfer,
    BankAccount,
    BankTransaction,
    PlaidLimb,
    TransferConfirmationError,
    TransferLimitExceeded,
)
from auton.limbs.banking.reconciler import (
    BankReconciler,
    ReconciliationError,
    ReconciliationReport,
    UnmatchedBankTx,
)
from auton.security.spend_caps import SpendGuard


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def wallet(tmp_path):
    db = tmp_path / "wallet.db"
    w = MasterWallet(db)
    w.credit(1000.0, "seed")
    return w


@pytest.fixture
def spend_guard(tmp_path):
    db = tmp_path / "spend.db"
    return SpendGuard(db_path=str(db))


@pytest.fixture
def plaid_limb():
    # Skeleton mode — no env vars required
    return PlaidLimb()


@pytest.fixture
def plaid_limb_with_guard(spend_guard):
    spend_guard.set_cap("bank_transfer", daily=200.0)
    return PlaidLimb(spend_guard=spend_guard)


@pytest.fixture
def reconciler(wallet, tmp_path):
    db = tmp_path / "reconciler.db"
    return BankReconciler(wallet, db_path=db)


# ------------------------------------------------------------------ #
# PlaidLimb — skeleton mode
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_plaid_health_check_skeleton(plaid_limb):
    health = await plaid_limb.health_check()
    assert health["status"] == "skeleton"
    assert health["mode"] == "mock"


@pytest.mark.asyncio
async def test_plaid_get_balance_mock(plaid_limb):
    accounts = await plaid_limb.get_balance()
    assert len(accounts) == 1
    assert isinstance(accounts[0], BankAccount)
    assert accounts[0].account_id == "mock_acc_1"
    assert accounts[0].balances.get("available") == 5000.0


@pytest.mark.asyncio
async def test_plaid_get_transactions_mock(plaid_limb):
    txs = await plaid_limb.get_transactions()
    assert len(txs) == 10
    assert isinstance(txs[0], BankTransaction)
    assert txs[0].account_id == "mock_acc_1"
    assert txs[0].amount == 25.0


@pytest.mark.asyncio
async def test_plaid_get_transactions_with_date_range(plaid_limb):
    start = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    txs = await plaid_limb.get_transactions(start_date=start, end_date=end)
    assert len(txs) == 6


@pytest.mark.asyncio
async def test_plaid_ach_transfer_mock(plaid_limb):
    transfer = await plaid_limb.initiate_ach_transfer(
        amount=50.0,
        account_id="mock_acc_1",
        direction="credit",
        description="Test transfer",
    )
    assert isinstance(transfer, ACHTransfer)
    assert transfer.status == "pending"
    assert transfer.amount == 50.0


@pytest.mark.asyncio
async def test_plaid_large_transfer_requires_confirmation(plaid_limb):
    result = await plaid_limb.initiate_ach_transfer(
        amount=150.0,
        account_id="mock_acc_1",
        direction="credit",
        description="Large transfer",
    )
    assert isinstance(result, dict)
    assert result["status"] == "pending_confirmation"
    assert "confirmation_id" in result


@pytest.mark.asyncio
async def test_plaid_confirm_transfer_success(plaid_limb):
    result = await plaid_limb.initiate_ach_transfer(
        amount=150.0,
        account_id="mock_acc_1",
        direction="credit",
        description="Large transfer",
    )
    confirmation_id = result["confirmation_id"]

    # Force skip confirmation by manipulating internal state
    pending = plaid_limb._pending_confirmations[confirmation_id]
    pending["requested_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=plaid_limb.CONFIRMATION_DELAY_SECONDS + 1)
    ).isoformat()

    transfer = await plaid_limb.confirm_transfer(confirmation_id)
    assert isinstance(transfer, ACHTransfer)
    assert transfer.status == "pending"


@pytest.mark.asyncio
async def test_plaid_confirm_transfer_too_early(plaid_limb):
    result = await plaid_limb.initiate_ach_transfer(
        amount=150.0,
        account_id="mock_acc_1",
        direction="credit",
        description="Large transfer",
    )
    confirmation_id = result["confirmation_id"]

    with pytest.raises(TransferConfirmationError):
        await plaid_limb.confirm_transfer(confirmation_id)


@pytest.mark.asyncio
async def test_plaid_transfer_daily_limit_exceeded(plaid_limb_with_guard):
    plaid_limb_with_guard._spend_guard.set_cap("bank_transfer", daily=10.0)
    with pytest.raises(TransferLimitExceeded):
        await plaid_limb_with_guard.initiate_ach_transfer(
            amount=50.0,
            account_id="mock_acc_1",
            direction="credit",
            description="Over limit",
        )


@pytest.mark.asyncio
async def test_plaid_webhook_transactions(plaid_limb):
    payload = {
        "webhook_type": "TRANSACTIONS",
        "webhook_code": "INITIAL_UPDATE",
        "item_id": "item_123",
    }
    result = await plaid_limb.handle_webhook(payload)
    assert result["handled"] is True
    assert result["action"] == "fetch_new_transactions"


@pytest.mark.asyncio
async def test_plaid_webhook_transfer(plaid_limb):
    payload = {
        "webhook_type": "TRANSFER",
        "webhook_code": "TRANSFER_STATUS_UPDATE",
        "transfer_id": "txf_123",
        "item_id": "item_123",
    }
    result = await plaid_limb.handle_webhook(payload)
    assert result["handled"] is True
    assert result["action"] == "update_transfer_status"


@pytest.mark.asyncio
async def test_plaid_execute_dispatch(plaid_limb):
    result = await plaid_limb.execute({
        "method": "get_balance",
        "kwargs": {},
    })
    assert len(result) == 1

    result = await plaid_limb.execute({
        "method": "get_transactions",
        "kwargs": {"count": 5},
    })
    assert len(result) == 5


@pytest.mark.asyncio
async def test_plaid_is_available():
    limb = PlaidLimb()
    assert limb.is_available(0) is False
    assert limb.is_available(1) is True
    assert limb.is_available(2) is True


# ------------------------------------------------------------------ #
# BankReconciler
# ------------------------------------------------------------------ #


def test_reconciler_import_transactions(reconciler):
    txs = [
        BankTransaction(
            transaction_id="tx_1",
            account_id="acc_1",
            amount=100.0,
            iso_currency_code="USD",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            name="Test Merchant",
            pending=False,
        ),
        BankTransaction(
            transaction_id="tx_2",
            account_id="acc_1",
            amount=200.0,
            iso_currency_code="USD",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            name="Another Merchant",
            pending=False,
        ),
    ]
    imported = reconciler.import_bank_transactions(txs)
    assert imported == 2

    # Duplicate import should be ignored
    imported = reconciler.import_bank_transactions(txs)
    assert imported == 0


def test_reconciler_exact_match(wallet, reconciler):
    # Create an internal receipt that matches a bank tx
    wallet.debit(100.0, "Test Merchant")
    wallet.debit(50.0, "Unrelated")

    txs = [
        BankTransaction(
            transaction_id="tx_1",
            account_id="acc_1",
            amount=100.0,
            iso_currency_code="USD",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            name="Test Merchant",
            pending=False,
        ),
    ]
    reconciler.import_bank_transactions(txs)
    report = reconciler.reconcile()

    assert isinstance(report, ReconciliationReport)
    assert report.auto_matched == 1
    assert len(report.unmatched_bank) == 0


def test_reconciler_unmatched_bank(wallet, reconciler):
    # Bank tx exists but no internal receipt
    txs = [
        BankTransaction(
            transaction_id="tx_unmatched",
            account_id="acc_1",
            amount=999.0,
            iso_currency_code="USD",
            date=(datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d"),
            name="Ghost Merchant",
            pending=False,
        ),
    ]
    reconciler.import_bank_transactions(txs)
    report = reconciler.reconcile()

    assert report.auto_matched == 0
    assert len(report.unmatched_bank) == 1
    assert isinstance(report.unmatched_bank[0], UnmatchedBankTx)
    assert report.unmatched_bank[0].transaction_id == "tx_unmatched"


def test_reconciler_unmatched_internal(wallet, reconciler):
    # Internal receipt exists but no bank tx (seed is also unmatched)
    wallet.debit(123.0, "Internal Only")
    report = reconciler.reconcile()

    assert report.auto_matched == 0
    assert len(report.unmatched_internal) == 2  # seed + Internal Only
    assert any(r["amount"] == 123.0 for r in report.unmatched_internal)


def test_reconciler_manual_match(wallet, reconciler):
    receipt = wallet.debit(250.0, "Manual Match Target")

    txs = [
        BankTransaction(
            transaction_id="tx_manual",
            account_id="acc_1",
            amount=250.0,
            iso_currency_code="USD",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            name="Manual Match Target",
            pending=False,
        ),
    ]
    reconciler.import_bank_transactions(txs)

    match = reconciler.manual_match("tx_manual", receipt.id)
    assert match.bank_transaction_id == "tx_manual"
    assert match.receipt_id == receipt.id
    assert match.match_type == "manual"


def test_reconciler_manual_match_not_found(reconciler):
    with pytest.raises(ReconciliationError):
        reconciler.manual_match("nonexistent", 999)


def test_reconciler_get_unmatched(reconciler):
    txs = [
        BankTransaction(
            transaction_id="tx_old",
            account_id="acc_1",
            amount=10.0,
            iso_currency_code="USD",
            date=(datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d"),
            name="Old",
            pending=False,
        ),
    ]
    reconciler.import_bank_transactions(txs)
    reconciler.reconcile()

    unmatched = reconciler.get_unmatched_bank_transactions(days=30)
    assert len(unmatched) == 1
    assert unmatched[0].days_unmatched >= 10


def test_reconciler_history(reconciler, wallet):
    wallet.debit(10.0, "tx")
    reconciler.reconcile()
    history = reconciler.get_reconciliation_history(limit=5)
    assert len(history) == 1
    assert history[0]["auto_matched"] >= 0


def test_reconciler_fuzzy_match(wallet, reconciler):
    # Exact match with same name
    wallet.debit(100.0, "Fuzzy Merchant")
    txs = [
        BankTransaction(
            transaction_id="tx_fuzzy",
            account_id="acc_1",
            amount=100.0,
            iso_currency_code="USD",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            name="Fuzzy Merchant",
            pending=False,
        ),
    ]
    reconciler.import_bank_transactions(txs)
    report = reconciler.reconcile(allow_fuzzy=True)
    assert report.auto_matched == 1


def test_reconciler_discrepancy_detected(wallet, tmp_path):
    # Use a larger tolerance so the match is found but discrepancy is recorded
    db = tmp_path / "reconciler.db"
    rec = BankReconciler(wallet, db_path=db, fuzzy_tolerance=10.0)
    wallet.debit(100.0, "Discrepancy Merchant")
    txs = [
        BankTransaction(
            transaction_id="tx_disc",
            account_id="acc_1",
            amount=105.0,
            iso_currency_code="USD",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            name="Discrepancy Merchant",
            pending=False,
        ),
    ]
    rec.import_bank_transactions(txs)
    report = rec.reconcile()
    assert len(report.discrepancies) == 1
    assert report.discrepancies[0]["discrepancy"] == 5.0


# ------------------------------------------------------------------ #
# MasterWallet external refs
# ------------------------------------------------------------------ #


def test_wallet_link_external_ref(wallet):
    receipt = wallet.credit(500.0, "external deposit")
    result = wallet.link_external_ref(receipt.id, "ext_123", source="plaid")
    assert result["receipt_id"] == receipt.id
    assert result["external_id"] == "ext_123"
    assert result["source"] == "plaid"


def test_wallet_get_receipts_by_external_id(wallet):
    receipt = wallet.credit(500.0, "external deposit")
    wallet.link_external_ref(receipt.id, "ext_456", source="plaid")
    receipts = wallet.get_receipts_by_external_id("ext_456")
    assert len(receipts) == 1
    assert receipts[0].id == receipt.id


def test_wallet_get_receipts_in_range(wallet):
    old = wallet.credit(100.0, "old")
    new = wallet.credit(200.0, "new")

    start = datetime.now(timezone.utc) - timedelta(hours=1)
    end = datetime.now(timezone.utc) + timedelta(hours=1)
    receipts = list(wallet.get_receipts_in_range(start, end))
    # seed + old + new = 3 receipts
    assert len(receipts) == 3
    assert {r.id for r in receipts} == {old.id, new.id, 1}


def test_wallet_link_external_ref_duplicate_idempotent(wallet):
    receipt = wallet.credit(100.0, "dup test")
    wallet.link_external_ref(receipt.id, "ext_dup", source="plaid")
    wallet.link_external_ref(receipt.id, "ext_dup", source="plaid")
    receipts = wallet.get_receipts_by_external_id("ext_dup")
    assert len(receipts) == 1


# ------------------------------------------------------------------ #
# Environment variable safety
# ------------------------------------------------------------------ #


def test_plaid_reads_env_vars(monkeypatch):
    monkeypatch.setenv("PLAID_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("PLAID_SECRET", "test_secret")
    monkeypatch.setenv("PLAID_ENV", "development")
    monkeypatch.setenv("PLAID_ACCESS_TOKEN", "test_access_token")

    limb = PlaidLimb()
    assert limb._client_id == "test_client_id"
    assert limb._secret == "test_secret"
    assert limb._env == "development"
    assert limb._access_token == "test_access_token"


def test_plaid_no_env_vars_runs_skeleton():
    for key in ["PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ACCESS_TOKEN"]:
        os.environ.pop(key, None)
    limb = PlaidLimb()
    assert limb._client_id is None
    assert limb._secret is None
    assert limb._access_token is None
