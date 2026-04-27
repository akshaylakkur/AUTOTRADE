"""Comprehensive pytest suite for auton/security."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from auton.security import (
    AuditTrail,
    Sandbox,
    SandboxResult,
    SpendCaps,
    Vault,
)
from auton.security.exceptions import (
    AuditError,
    SandboxError,
    SpendCapExceeded,
    VaultError,
)


# ---------------------------------------------------------------------------
# Vault tests
# ---------------------------------------------------------------------------


class TestVault:
    @pytest.fixture(autouse=True)
    def _vault_env(self, tmp_path):
        db = tmp_path / "vault.db"
        key = Fernet.generate_key().decode()
        os.environ["AEON_VAULT_KEY"] = key
        self.vault = Vault(str(db))
        yield
        os.environ.pop("AEON_VAULT_KEY", None)

    def test_missing_key_raises(self, tmp_path):
        os.environ.pop("AEON_VAULT_KEY", None)
        with pytest.raises(VaultError, match="AEON_VAULT_KEY"):
            Vault(str(tmp_path / "vault2.db"))

    def test_store_and_retrieve(self):
        self.vault.store("api_key", "supersecret")
        assert self.vault.retrieve("api_key") == "supersecret"

    def test_store_overwrite(self):
        self.vault.store("api_key", "first")
        self.vault.store("api_key", "second")
        assert self.vault.retrieve("api_key") == "second"

    def test_retrieve_missing_raises(self):
        with pytest.raises(VaultError, match="No secret found"):
            self.vault.retrieve("missing")

    def test_delete(self):
        self.vault.store("temp", "value")
        self.vault.delete("temp")
        with pytest.raises(VaultError, match="No secret found"):
            self.vault.retrieve("temp")

    def test_delete_missing_raises(self):
        with pytest.raises(VaultError, match="No secret found"):
            self.vault.delete("missing")

    def test_rotate_key(self, tmp_path):
        self.vault.store("rotate_me", "hello")
        old_key = os.environ["AEON_VAULT_KEY"]
        new_key = Fernet.generate_key().decode()
        rotated = self.vault.rotate_key(new_key)
        assert rotated.retrieve("rotate_me") == "hello"
        # Old instance should still work with old key for non-rotated dbs,
        # but in this case the DB was re-encrypted so old instance fails.
        with pytest.raises(VaultError):
            self.vault.retrieve("rotate_me")

    def test_multiple_keys(self):
        self.vault.store("a", "1")
        self.vault.store("b", "2")
        assert self.vault.retrieve("a") == "1"
        assert self.vault.retrieve("b") == "2"

    def test_plaintext_not_in_db(self, tmp_path):
        self.vault.store("x", "y")
        db_path = self.vault._db_path
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT encrypted_secret FROM vault_entries WHERE key_id = ?;", ("x",)
            ).fetchone()
        assert row is not None
        # The raw stored value must not be the plaintext.
        assert row[0] != b"y"
        # Decrypting the stored value must yield the original plaintext.
        assert self.vault.retrieve("x") == "y"


# ---------------------------------------------------------------------------
# AuditTrail tests
# ---------------------------------------------------------------------------


class TestAuditTrail:
    @pytest.fixture(autouse=True)
    def _trail_env(self, tmp_path):
        db = tmp_path / "audit.db"
        self.trail = AuditTrail(str(db))
        yield

    def test_log_and_verify(self):
        h1 = self.trail.log("trade", {"symbol": "BTC", "qty": 1})
        h2 = self.trail.log("trade", {"symbol": "ETH", "qty": 2})
        assert self.trail.verify_chain() is True
        assert isinstance(h1, str)
        assert isinstance(h2, str)
        assert h1 != h2

    def test_verify_chain_empty(self):
        assert self.trail.verify_chain() is True

    def test_hash_chain_integrity(self, tmp_path):
        db = tmp_path / "audit.db"
        trail = AuditTrail(str(db))
        trail.log("action", {"data": 1})
        trail.log("action", {"data": 2})
        # Tamper with the database directly.
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE audit_log SET payload_json = ? WHERE id = 1;",
                (json.dumps({"data": 99}),),
            )
        assert trail.verify_chain() is False

    def test_prev_hash_links(self, tmp_path):
        db = tmp_path / "audit.db"
        trail = AuditTrail(str(db))
        h1 = trail.log("a", {})
        h2 = trail.log("b", {})
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT prev_hash, entry_hash FROM audit_log ORDER BY id;"
            ).fetchall()
        assert rows[0][0] == "0" * 64
        assert rows[1][0] == h1
        assert rows[1][1] == h2


# ---------------------------------------------------------------------------
# SpendCaps tests
# ---------------------------------------------------------------------------


class TestSpendCaps:
    @pytest.fixture(autouse=True)
    def _spend_env(self, tmp_path):
        db = tmp_path / "spend.db"
        self.caps = SpendCaps(str(db))
        yield

    def test_no_cap_no_restrictions(self):
        self.caps.check_spend("uncapped", 1_000_000)
        self.caps.record_spend("uncapped", 1_000_000)

    def test_hourly_cap(self):
        self.caps.set_cap("compute", hourly=100)
        self.caps.record_spend("compute", 50)
        self.caps.check_spend("compute", 40)
        with pytest.raises(SpendCapExceeded, match="Hourly cap exceeded"):
            self.caps.check_spend("compute", 60)

    def test_daily_cap(self):
        self.caps.set_cap("data", daily=500)
        self.caps.record_spend("data", 400)
        self.caps.check_spend("data", 99)
        with pytest.raises(SpendCapExceeded, match="Daily cap exceeded"):
            self.caps.check_spend("data", 101)

    def test_weekly_cap(self):
        self.caps.set_cap("api", weekly=1000)
        self.caps.record_spend("api", 900)
        self.caps.check_spend("api", 99)
        with pytest.raises(SpendCapExceeded, match="Weekly cap exceeded"):
            self.caps.check_spend("api", 101)

    def test_exactly_at_cap(self):
        self.caps.set_cap("limit", hourly=100)
        self.caps.record_spend("limit", 100)
        self.caps.check_spend("limit", 0)
        with pytest.raises(SpendCapExceeded):
            self.caps.check_spend("limit", 0.01)

    def test_get_remaining_budget(self):
        self.caps.set_cap("budget", hourly=100, daily=500, weekly=2000)
        self.caps.record_spend("budget", 30)
        remaining = self.caps.get_remaining_budget("budget")
        assert remaining["hourly"] == pytest.approx(70)
        assert remaining["daily"] == pytest.approx(470)
        assert remaining["weekly"] == pytest.approx(1970)

    def test_get_remaining_budget_no_cap(self):
        remaining = self.caps.get_remaining_budget("nocap")
        assert remaining == {"hourly": None, "daily": None, "weekly": None}

    def test_window_rolloff(self):
        self.caps.set_cap("roll", hourly=10)
        # Manually insert an old record.
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with sqlite3.connect(self.caps._db_path) as conn:
            conn.execute(
                "INSERT INTO spend_records (category, amount, recorded_at) VALUES (?, ?, ?);",
                ("roll", 9, old),
            )
        # Old spend should not count toward the current window.
        self.caps.check_spend("roll", 9)
        self.caps.record_spend("roll", 9)
        # Now current window has 9; adding 2 would exceed 10.
        with pytest.raises(SpendCapExceeded):
            self.caps.check_spend("roll", 2)


# ---------------------------------------------------------------------------
# Sandbox tests
# ---------------------------------------------------------------------------


class TestSandbox:
    @pytest.fixture(autouse=True)
    def _sandbox_env(self):
        self.sandbox = Sandbox()
        yield

    def test_hello_world(self):
        code = 'print("hello world")'
        result = self.sandbox.execute(code, timeout_seconds=5)
        assert result.stdout.strip() == "hello world"
        assert result.returncode == 0
        assert result.execution_time >= 0

    def test_stderr_capture(self):
        code = 'import sys; sys.stderr.write("error msg\\n")'
        result = self.sandbox.execute(code, timeout_seconds=5)
        assert "error msg" in result.stderr
        assert result.returncode == 0

    def test_timeout(self):
        code = "import time; time.sleep(60)"
        result = self.sandbox.execute(code, timeout_seconds=1, allowed_modules=["time"])
        assert result.returncode == -9

    def test_disallowed_import_blocked(self):
        code = "import os; print(os.getcwd())"
        result = self.sandbox.execute(code, timeout_seconds=5)
        assert result.returncode != 0 or "restricted" in result.stderr.lower()

    def test_allowed_import(self):
        code = "import math; print(math.sqrt(16))"
        result = self.sandbox.execute(
            code, timeout_seconds=5, allowed_modules=["math"]
        )
        assert result.stdout.strip() == "4.0"
        assert result.returncode == 0

    def test_network_blocked(self):
        code = """
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print("socket created")
except OSError as e:
    print("blocked", e)
"""
        result = self.sandbox.execute(code, timeout_seconds=5)
        assert result.returncode == 0
        assert "blocked" in result.stdout

    def test_sandbox_result_fields(self):
        code = 'print(42)'
        result = self.sandbox.execute(code, timeout_seconds=5)
        assert isinstance(result, SandboxResult)
        assert result.stdout == "42\n"
        assert result.stderr == ""
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------


def test_exception_hierarchy():
    assert issubclass(VaultError, Exception)
    assert issubclass(AuditError, Exception)
    assert issubclass(SpendCapExceeded, Exception)
    assert issubclass(SandboxError, Exception)
