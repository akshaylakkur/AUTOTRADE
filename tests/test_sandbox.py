"""Comprehensive pytest suite for the Secure Execution Environment."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from auton.ledger.master_wallet import MasterWallet
from auton.security import (
    AuditLog,
    FileSandbox,
    NetworkGate,
    ProcessSandbox,
    SandboxResult,
    SecretVault,
    SecureExecutionEnvironment,
    SpendGuard,
)
from auton.security.config import NetworkRule, ResourceLimits, SecurityConfig, SpendGuardConfig
from auton.security.exceptions import (
    AuditError,
    BudgetExceeded,
    FileAccessDenied,
    NetworkBlocked,
    SandboxError,
    SpendCapExceeded,
    VaultError,
)


# ---------------------------------------------------------------------------
# ProcessSandbox tests
# ---------------------------------------------------------------------------


class TestProcessSandbox:
    @pytest.fixture(autouse=True)
    def _sandbox_env(self):
        self.sandbox = ProcessSandbox()
        yield

    def test_hello_world(self):
        code = 'print("hello world")'
        result = self.sandbox.execute(code, language="python")
        assert result.stdout.strip() == "hello world"
        assert result.returncode == 0
        assert result.execution_time >= 0
        assert not result.timed_out

    def test_stderr_capture(self):
        code = 'import sys; sys.stderr.write("error msg\\n")'
        result = self.sandbox.execute(code, language="python")
        assert "error msg" in result.stderr
        assert result.returncode == 0

    def test_timeout(self):
        code = "import time; time.sleep(60)"
        result = self.sandbox.execute(
            code,
            language="python",
            limits=ResourceLimits(max_wall_time_seconds=1),
            allowed_modules=["time"],
        )
        assert result.returncode == -9
        assert result.timed_out

    def test_disallowed_import_blocked(self):
        code = "import os; print(os.getcwd())"
        result = self.sandbox.execute(code, language="python")
        assert result.returncode != 0 or "restricted" in result.stderr.lower()

    def test_allowed_import(self):
        code = "import math; print(math.sqrt(16))"
        result = self.sandbox.execute(
            code, language="python", allowed_modules=["math"]
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
        result = self.sandbox.execute(code, language="python")
        assert result.returncode == 0
        assert "blocked" in result.stdout

    def test_resource_limits(self):
        limits = ResourceLimits(
            max_memory_mb=64,
            max_cpu_time_seconds=5,
            max_wall_time_seconds=10,
            max_file_descriptors=16,
        )
        result = self.sandbox.execute('print("ok")', language="python", limits=limits)
        assert result.returncode == 0

    def test_cumulative_metrics(self):
        self.sandbox.execute('print("a")', language="python")
        self.sandbox.execute('print("b")', language="python")
        metrics = self.sandbox.cumulative_metrics
        assert metrics["total_executions"] == 2
        assert metrics["total_cpu_burned_seconds"] > 0

    def test_sandbox_result_fields(self):
        code = 'print(42)'
        result = self.sandbox.execute(code, language="python")
        assert isinstance(result, SandboxResult)
        assert result.stdout == "42\n"
        assert result.stderr == ""
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# NetworkGate tests
# ---------------------------------------------------------------------------


class TestNetworkGate:
    @pytest.fixture(autouse=True)
    def _gate_env(self):
        self.gate = NetworkGate()
        yield

    @pytest.mark.asyncio
    async def test_allow_explicit_rule(self):
        self.gate.add_rule(NetworkRule(domain="httpbin.org", action="allow"))
        status, body, headers = await self.gate.request(
            "GET", "https://httpbin.org/get", source_module="test"
        )
        assert status == 200
        assert len(body) > 0

    @pytest.mark.asyncio
    async def test_deny_by_default(self):
        with pytest.raises(NetworkBlocked):
            await self.gate.request(
                "GET", "https://example.com", source_module="test"
            )

    @pytest.mark.asyncio
    async def test_deny_explicit_rule(self):
        self.gate.add_rule(NetworkRule(domain="*.bad.com", action="deny"))
        with pytest.raises(NetworkBlocked):
            await self.gate.request(
                "GET", "https://evil.bad.com", source_module="test"
            )

    @pytest.mark.asyncio
    async def test_rate_limit(self):
        self.gate.add_rule(NetworkRule(domain="localhost", action="allow", max_requests_per_minute=1))
        # First request should succeed (we can't easily test actual HTTP here without a server)
        status = self.gate.get_rate_limit_status("localhost")
        assert status["requests_remaining"] is not None

    def test_remove_rule(self):
        self.gate.add_rule(NetworkRule(domain="example.com", action="allow"))
        self.gate.remove_rule("example.com")
        assert self.gate._match_rule("https://example.com") is None

    def test_rate_limit_status_missing(self):
        status = self.gate.get_rate_limit_status("nonexistent")
        assert status["requests_remaining"] is None

    @pytest.mark.asyncio
    async def test_close_client(self):
        await self.gate.close()
        assert self.gate._client is None


# ---------------------------------------------------------------------------
# FileSandbox tests
# ---------------------------------------------------------------------------


class TestFileSandbox:
    @pytest.fixture(autouse=True)
    def _fs_env(self, tmp_path):
        self.fs = FileSandbox(write_roots=[str(tmp_path / "data")])
        self.root = tmp_path / "data"
        self.root.mkdir(parents=True, exist_ok=True)
        yield

    def test_read_allowed(self):
        f = self.root / "file.txt"
        f.write_text("hello")
        data = self.fs.read(f, module="test")
        assert data == b"hello"

    def test_read_denied_immutable(self):
        with pytest.raises(FileAccessDenied):
            self.fs.read("auton/terminal.py", module="test")

    def test_write_allowed(self):
        path = self.root / "new.txt"
        self.fs.write(path, b"data", module="test")
        assert path.read_bytes() == b"data"

    def test_write_denied_outside_root(self):
        with pytest.raises(FileAccessDenied):
            self.fs.write("/tmp/outside.txt", b"data", module="test")

    def test_write_denied_immutable(self):
        with pytest.raises(FileAccessDenied):
            self.fs.write("auton/terminal.py", b"data", module="test")

    def test_delete_allowed(self):
        path = self.root / "del.txt"
        path.write_text("bye")
        self.fs.delete(path, module="test")
        assert not path.exists()

    def test_delete_denied_outside_root(self):
        with pytest.raises(FileAccessDenied):
            self.fs.delete("/tmp/outside.txt", module="test")

    def test_listdir_allowed(self):
        (self.root / "a.txt").write_text("a")
        entries = self.fs.listdir(self.root, module="test")
        assert "a.txt" in entries

    def test_listdir_denied_immutable(self):
        with pytest.raises(FileAccessDenied):
            self.fs.listdir("auton/core/", module="test")

    def test_mkdir_allowed(self):
        path = self.root / "subdir"
        self.fs.mkdir(path, module="test")
        assert path.is_dir()

    def test_mkdir_denied_outside_root(self):
        with pytest.raises(FileAccessDenied):
            self.fs.mkdir("/tmp/outside_dir", module="test")

    def test_symlink_resolution(self, tmp_path):
        real_dir = self.root / "real"
        real_dir.mkdir()
        link = self.root / "link"
        link.symlink_to(real_dir)
        self.fs.write(link / "file.txt", b"via symlink", module="test")
        assert (real_dir / "file.txt").read_bytes() == b"via symlink"


# ---------------------------------------------------------------------------
# SecretVault tests
# ---------------------------------------------------------------------------


class TestSecretVault:
    @pytest.fixture(autouse=True)
    def _vault_env(self, tmp_path):
        db = tmp_path / "vault.db"
        key = Fernet.generate_key().decode()
        os.environ["AEON_VAULT_KEY"] = key
        self.vault = SecretVault(str(db))
        yield
        os.environ.pop("AEON_VAULT_KEY", None)

    def test_missing_key_raises(self, tmp_path):
        os.environ.pop("AEON_VAULT_KEY", None)
        with pytest.raises(VaultError, match="AEON_VAULT_KEY"):
            SecretVault(str(tmp_path / "vault2.db"))

    def test_store_and_retrieve(self):
        self.vault.store("api_key", "supersecret")
        assert self.vault.retrieve("api_key", caller="test") == "supersecret"

    def test_retrieve_logs_access(self):
        self.vault.store("logged_key", "secret")
        self.vault.retrieve("logged_key", caller="test_module")
        log = self.vault.get_access_log("logged_key", limit=10)
        assert any(entry["caller"] == "test_module" for entry in log)

    def test_seal_and_unseal(self):
        self.vault.store("sealed", "data")
        self.vault.seal()
        with pytest.raises(VaultError, match="sealed"):
            self.vault.retrieve("sealed", caller="test")
        key = os.environ["AEON_VAULT_KEY"]
        self.vault.unseal(key)
        assert self.vault.retrieve("sealed", caller="test") == "data"

    def test_delete(self):
        self.vault.store("temp", "value")
        self.vault.delete("temp", caller="test")
        with pytest.raises(VaultError, match="No secret found"):
            self.vault.retrieve("temp", caller="test")

    def test_rotate_key(self, tmp_path):
        self.vault.store("rotate_me", "hello")
        new_key = Fernet.generate_key().decode()
        rotated = self.vault.rotate_key(new_key)
        assert rotated.retrieve("rotate_me", caller="test") == "hello"

    def test_plaintext_not_in_db(self, tmp_path):
        self.vault.store("x", "y")
        db_path = self.vault._db_path
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT encrypted_secret FROM vault_entries WHERE key_id = ?;", ("x",)
            ).fetchone()
        assert row is not None
        assert row[0] != b"y"
        assert self.vault.retrieve("x", caller="test") == "y"


# ---------------------------------------------------------------------------
# AuditLog tests
# ---------------------------------------------------------------------------


class TestAuditLog:
    @pytest.fixture(autouse=True)
    def _log_env(self, tmp_path):
        db = tmp_path / "audit.db"
        jsonl_dir = tmp_path / "audit"
        self.log = AuditLog(str(db), str(jsonl_dir))
        yield

    def test_log_and_verify(self):
        h1 = self.log.log("trade", {"symbol": "BTC", "qty": 1})
        h2 = self.log.log("trade", {"symbol": "ETH", "qty": 2})
        assert self.log.verify_chain() is True
        assert isinstance(h1, str)
        assert isinstance(h2, str)
        assert h1 != h2

    def test_pre_and_post_log(self):
        log_id = self.log.pre_log("action", {"step": 1})
        assert isinstance(log_id, int)
        self.log.post_log(log_id, {"status": "ok"})
        assert self.log.verify_chain() is True

    def test_verify_chain_empty(self):
        assert self.log.verify_chain() is True

    def test_hash_chain_integrity(self, tmp_path):
        db = tmp_path / "audit.db"
        log = AuditLog(str(db), str(tmp_path / "audit"))
        log.log("action", {"data": 1})
        log.log("action", {"data": 2})
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE audit_log SET parameters_json = ? WHERE id = 1;",
                (json.dumps({"data": 99}),),
            )
        assert log.verify_chain() is False

    def test_jsonl_export(self):
        self.log.log("test", {"k": "v"})
        path = self.log.export_jsonl()
        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) >= 1
        entry = json.loads(lines[0])
        assert entry["action_type"] == "test"

    def test_prev_hash_links(self, tmp_path):
        db = tmp_path / "audit.db"
        log = AuditLog(str(db), str(tmp_path / "audit"))
        h1 = log.log("a", {})
        h2 = log.log("b", {})
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT prev_hash, entry_hash FROM audit_log ORDER BY id;"
            ).fetchall()
        assert rows[0][0] == "0" * 64
        assert rows[1][0] == h1
        assert rows[1][1] == h2


# ---------------------------------------------------------------------------
# SpendGuard tests
# ---------------------------------------------------------------------------


class TestSpendGuard:
    @pytest.fixture(autouse=True)
    def _guard_env(self, tmp_path):
        db = tmp_path / "spend.db"
        self.guard = SpendGuard(db_path=str(db))
        yield

    def test_no_cap_no_restrictions(self):
        self.guard.check_and_record("uncapped", 1_000_000, "test")

    def test_hourly_cap(self):
        self.guard.set_cap(SpendGuardConfig(category="compute", hourly=100))
        self.guard.check_and_record("compute", 50, "test")
        allowed, _ = self.guard.quote_spend("compute", 40)
        assert allowed is True
        allowed, reason = self.guard.quote_spend("compute", 60)
        assert allowed is False
        assert "Hourly cap exceeded" in reason

    def test_daily_cap(self):
        self.guard.set_cap(SpendGuardConfig(category="data", daily=500))
        self.guard.check_and_record("data", 400, "test")
        allowed, _ = self.guard.quote_spend("data", 99)
        assert allowed is True
        allowed, reason = self.guard.quote_spend("data", 101)
        assert allowed is False
        assert "Daily cap exceeded" in reason

    def test_weekly_cap(self):
        self.guard.set_cap(SpendGuardConfig(category="api", weekly=1000))
        self.guard.check_and_record("api", 900, "test")
        allowed, _ = self.guard.quote_spend("api", 99)
        assert allowed is True
        allowed, reason = self.guard.quote_spend("api", 101)
        assert allowed is False
        assert "Weekly cap exceeded" in reason

    def test_exactly_at_cap(self):
        self.guard.set_cap(SpendGuardConfig(category="limit", hourly=100))
        self.guard.check_and_record("limit", 100, "test")
        allowed, reason = self.guard.quote_spend("limit", 0.01)
        assert allowed is False
        assert "Hourly cap exceeded" in reason

    def test_get_remaining_budget(self):
        self.guard.set_cap(SpendGuardConfig(category="budget", hourly=100, daily=500, weekly=2000))
        self.guard.check_and_record("budget", 30, "test")
        remaining = self.guard.get_remaining_budget("budget")
        assert remaining["hourly"] == pytest.approx(70)
        assert remaining["daily"] == pytest.approx(470)
        assert remaining["weekly"] == pytest.approx(1970)

    def test_get_remaining_budget_no_cap(self):
        remaining = self.guard.get_remaining_budget("nocap")
        assert remaining == {"hourly": None, "daily": None, "weekly": None, "monthly": None, "total": None}

    def test_window_rolloff(self):
        self.guard.set_cap(SpendGuardConfig(category="roll", hourly=10))
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with sqlite3.connect(self.guard._db_path) as conn:
            conn.execute(
                "INSERT INTO spend_records (category, amount, reason, recorded_at) VALUES (?, ?, ?, ?);",
                ("roll", 9, "test", old),
            )
        self.guard.check_and_record("roll", 9, "test")
        allowed, reason = self.guard.quote_spend("roll", 2)
        assert allowed is False
        assert "Hourly cap exceeded" in reason

    def test_global_cap(self):
        self.guard.set_global_cap(100)
        self.guard.check_and_record("cat1", 60, "test")
        allowed, reason = self.guard.quote_spend("cat2", 50)
        assert allowed is False
        assert "Global budget exceeded" in reason

    def test_global_remaining(self):
        self.guard.set_global_cap(100)
        self.guard.check_and_record("cat", 30, "test")
        assert self.guard.get_global_remaining() == pytest.approx(70)

    def test_ledger_integration(self, tmp_path):
        ledger = MasterWallet(str(tmp_path / "ledger.db"))
        ledger.credit(100, "seed")
        guard = SpendGuard(wallet=ledger, db_path=str(tmp_path / "spend.db"))
        guard.set_cap(SpendGuardConfig(category="compute", hourly=50))
        receipt = guard.check_and_record("compute", 30, "test")
        assert receipt["ledger_receipt"] is not None
        assert ledger.get_balance() == pytest.approx(70)


# ---------------------------------------------------------------------------
# SecureExecutionEnvironment tests
# ---------------------------------------------------------------------------


class TestSecureExecutionEnvironment:
    @pytest.fixture(autouse=True)
    def _env(self, tmp_path):
        key = Fernet.generate_key().decode()
        os.environ["AEON_VAULT_KEY"] = key
        self.config = SecurityConfig(
            db_dir=str(tmp_path / "data"),
            cold_storage_dir=str(tmp_path / "cold_storage"),
        )
        self.env = SecureExecutionEnvironment(config=self.config)
        yield
        os.environ.pop("AEON_VAULT_KEY", None)

    def test_components_exposed(self):
        assert isinstance(self.env.process, ProcessSandbox)
        assert isinstance(self.env.network, NetworkGate)
        assert isinstance(self.env.files, FileSandbox)
        assert isinstance(self.env.vault, SecretVault)
        assert isinstance(self.env.audit, AuditLog)
        assert isinstance(self.env.spend, SpendGuard)

    @pytest.mark.asyncio
    async def test_execute_action_success(self):
        async def _done():
            return "done"

        result = await self.env.execute_action(
            "test_action",
            executor=_done,
            category="test",
            estimated_cost=0.0,
        )
        assert result == "done"
        assert self.env.audit.verify_chain() is True

    @pytest.mark.asyncio
    async def test_execute_action_blocked_by_budget(self):
        self.env.spend.set_global_cap(10)
        with pytest.raises(BudgetExceeded):
            async def _done():
                return "done"

            await self.env.execute_action(
                "expensive_action",
                executor=_done,
                category="test",
                estimated_cost=20.0,
            )

    @pytest.mark.asyncio
    async def test_execute_action_with_spend(self, tmp_path):
        ledger = MasterWallet(str(tmp_path / "ledger.db"))
        ledger.credit(100, "seed")
        env = SecureExecutionEnvironment(wallet=ledger, config=self.config)
        env.spend.set_cap(SpendGuardConfig(category="compute", hourly=50))

        async def _compute():
            return "computed"

        result = await env.execute_action(
            "compute",
            executor=_compute,
            category="compute",
            estimated_cost=10.0,
        )
        assert result == "computed"
        assert ledger.get_balance() == pytest.approx(90)

    @pytest.mark.asyncio
    async def test_execute_action_error_logged(self):
        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await self.env.execute_action(
                "failing_action",
                executor=fail,
                category="test",
                estimated_cost=0.0,
            )
        # Chain should still be intact even with error entries
        assert self.env.audit.verify_chain() is True


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------


def test_exception_hierarchy():
    assert issubclass(VaultError, Exception)
    assert issubclass(AuditError, Exception)
    assert issubclass(SpendCapExceeded, Exception)
    assert issubclass(SandboxError, Exception)
    assert issubclass(NetworkBlocked, Exception)
    assert issubclass(FileAccessDenied, Exception)
    assert issubclass(BudgetExceeded, Exception)
