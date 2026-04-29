"""Comprehensive pytest suite for auton/security."""

from __future__ import annotations

import asyncio
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
    ApprovalRequired,
    AuditError,
    BudgetExceeded,
    EmergencyPauseActive,
    NetworkBlocked,
    PolicyViolation,
    RotationRequired,
    SandboxError,
    SpendCapExceeded,
    ThreatDetected,
    VaultError,
)
from auton.security.config import NetworkRule, SecurityConfig, SpendGuardConfig
from auton.security.coordinator import SecureExecutionEnvironment
from auton.security.file_sandbox import FileSandbox
from auton.security.network_gate import NetworkGate
from auton.security.risk_coordinator import (
    PendingDecision,
    RiskCoordinator,
    RiskLevel,
    RiskReview,
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
        os.environ.pop("AEON_VAULT_SALT", None)
        self.vault = Vault(str(db))
        yield
        os.environ.pop("AEON_VAULT_KEY", None)
        os.environ.pop("AEON_VAULT_SALT", None)

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
        new_key = Fernet.generate_key().decode()
        rotated = self.vault.rotate_key(new_key)
        assert rotated.retrieve("rotate_me") == "hello"
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
        assert row[0] != b"y"
        assert self.vault.retrieve("x") == "y"

    def test_key_derivation(self):
        # Vault should work with PBKDF2-derived key
        self.vault.store("derived", "secret")
        assert self.vault.retrieve("derived") == "secret"

    def test_rotation_tracking(self):
        self.vault.store("rotate_test", "value", rotation_interval_days=1)
        status = self.vault.get_rotation_status("rotate_test")
        assert status["exists"]
        assert status["rotation_interval_days"] == 1
        assert not status["overdue"]

    def test_rotation_overdue_raises(self, tmp_path):
        db = tmp_path / "vault_rot.db"
        key = Fernet.generate_key().decode()
        os.environ["AEON_VAULT_KEY"] = key
        os.environ.pop("AEON_VAULT_SALT", None)
        vault = Vault(str(db))
        vault.store("old_key", "value", rotation_interval_days=1)
        # Backdate last_rotated_at
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE vault_entries SET last_rotated_at = ? WHERE key_id = ?;",
                ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), "old_key"),
            )
        with pytest.raises(RotationRequired, match="rotation window"):
            vault.retrieve("old_key")
        os.environ.pop("AEON_VAULT_KEY", None)

    def test_get_all_overdue(self, tmp_path):
        db = tmp_path / "vault_od.db"
        key = Fernet.generate_key().decode()
        os.environ["AEON_VAULT_KEY"] = key
        os.environ.pop("AEON_VAULT_SALT", None)
        vault = Vault(str(db))
        vault.store("od1", "v1", rotation_interval_days=1)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE vault_entries SET last_rotated_at = ? WHERE key_id = ?;",
                ((datetime.now(timezone.utc) - timedelta(days=5)).isoformat(), "od1"),
            )
        overdue = vault.get_all_overdue()
        assert len(overdue) == 1
        assert overdue[0]["key_id"] == "od1"
        os.environ.pop("AEON_VAULT_KEY", None)


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
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE audit_log SET parameters_json = ? WHERE id = 1;",
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

    def test_severity_levels(self):
        h = self.trail.log("critical_action", {"data": 1}, severity="critical")
        assert isinstance(h, str)
        severe = self.trail.query_by_severity("warning", limit=10)
        assert len(severe) >= 1
        assert severe[0]["severity"] == "critical"

    def test_query_by_action(self):
        self.trail.log("trade", {"sym": "A"})
        self.trail.log("trade", {"sym": "B"})
        self.trail.log("other", {"sym": "C"})
        results = self.trail.query_by_action("trade")
        assert len(results) == 2

    def test_export_jsonl_exists(self, tmp_path):
        db = tmp_path / "audit_exp.db"
        trail = AuditTrail(str(db), jsonl_dir=str(tmp_path / "audit"))
        trail.log("x", {})
        p = trail.export_jsonl()
        assert p.exists()


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

    def test_monthly_cap(self):
        self.caps.set_cap("service", monthly=2000)
        self.caps.record_spend("service", 1900)
        self.caps.check_spend("service", 99)
        with pytest.raises(SpendCapExceeded, match="Monthly cap exceeded"):
            self.caps.check_spend("service", 101)

    def test_exactly_at_cap(self):
        self.caps.set_cap("limit", hourly=100)
        self.caps.record_spend("limit", 100)
        self.caps.check_spend("limit", 0)
        with pytest.raises(SpendCapExceeded):
            self.caps.check_spend("limit", 0.01)

    def test_get_remaining_budget(self):
        self.caps.set_cap("budget", hourly=100, daily=500, weekly=2000, monthly=5000)
        self.caps.record_spend("budget", 30)
        remaining = self.caps.get_remaining_budget("budget")
        assert remaining["hourly"] == pytest.approx(70)
        assert remaining["daily"] == pytest.approx(470)
        assert remaining["weekly"] == pytest.approx(1970)
        assert remaining["monthly"] == pytest.approx(4970)

    def test_get_remaining_budget_no_cap(self):
        remaining = self.caps.get_remaining_budget("nocap")
        assert remaining["hourly"] is None
        assert remaining["monthly"] is None

    def test_window_rolloff(self):
        self.caps.set_cap("roll", hourly=10)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with sqlite3.connect(self.caps._db_path) as conn:
            conn.execute(
                "INSERT INTO spend_records (category, amount, recorded_at) VALUES (?, ?, ?);",
                ("roll", 9, old),
            )
        self.caps.check_spend("roll", 9)
        self.caps.record_spend("roll", 9)
        with pytest.raises(SpendCapExceeded):
            self.caps.check_spend("roll", 2)

    def test_emergency_pause(self):
        self.caps.set_cap("paused", daily=100)
        self.caps.pause("test emergency")
        paused, reason = self.caps.is_paused()
        assert paused
        assert "test emergency" in reason
        with pytest.raises(EmergencyPauseActive, match="paused"):
            self.caps.check_spend("paused", 1)
        self.caps.resume()
        paused, _ = self.caps.is_paused()
        assert not paused

    def test_multi_tier_approval(self):
        self.caps.set_cap("tiered", auto_approve_threshold=10, confirmation_threshold=50)
        # Small: auto-approve
        self.caps.check_spend("tiered", 5)
        # Medium: approval required
        allowed, reason = self.caps.quote_spend("tiered", 20)
        assert not allowed
        assert "manual confirmation" in reason
        with pytest.raises(ApprovalRequired):
            self.caps.check_spend("tiered", 20)
        # Large: blocked
        with pytest.raises(SpendCapExceeded, match="exceeds confirmation threshold"):
            self.caps.check_spend("tiered", 100)


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
# NetworkGate tests
# ---------------------------------------------------------------------------


class TestNetworkGate:
    @pytest.fixture(autouse=True)
    def _gate_env(self):
        self.gate = NetworkGate()
        self.gate.add_rule(NetworkRule("example.com", action="allow"))
        yield

    def test_explicit_deny(self):
        self.gate.add_rule(NetworkRule("bad.com", action="deny"))
        with pytest.raises(NetworkBlocked, match="blocked"):
            asyncio.run(self.gate.request("GET", "https://bad.com/path", source_module="test"))

    def test_default_deny(self):
        with pytest.raises(NetworkBlocked, match="blocked"):
            asyncio.run(self.gate.request("GET", "https://unknown.com", source_module="test"))

    def test_https_enforcement(self):
        gate = NetworkGate(require_https=True)
        gate.add_rule(NetworkRule("example.com", action="allow"))
        with pytest.raises(NetworkBlocked, match="HTTPS required"):
            asyncio.run(gate.request("GET", "http://example.com", source_module="test"))

    def test_ip_blocklist(self):
        gate = NetworkGate(require_https=False)
        gate.add_rule(NetworkRule("*", action="allow"))
        gate.add_blocklist_ip("1.2.3.4")
        with pytest.raises(NetworkBlocked, match="blocklist"):
            asyncio.run(gate.request("GET", "https://1.2.3.4", source_module="test"))

    def test_rate_limit_status(self):
        status = self.gate.get_rate_limit_status("example.com")
        assert status["requests_remaining"] is not None


# ---------------------------------------------------------------------------
# FileSandbox tests
# ---------------------------------------------------------------------------


class TestFileSandbox:
    @pytest.fixture(autouse=True)
    def _fs_env(self, tmp_path):
        self.fs = FileSandbox(write_roots=[str(tmp_path)])
        self.root = tmp_path
        yield

    def test_read_write(self):
        p = self.root / "test.txt"
        self.fs.write(str(p), b"hello", module="test")
        data = self.fs.read(str(p), module="test")
        assert data == b"hello"

    def test_redaction(self):
        p = self.root / "secrets.txt"
        payload = b"password = 'supersecret123'\napi_key = 'sk-abc123xyz'"
        self.fs.write(str(p), payload, module="test")
        data = self.fs.read(str(p), module="test")
        assert b"[REDACTED]" in data
        assert b"supersecret123" not in data
        assert b"sk-abc123xyz" not in data

    def test_write_no_redact(self):
        p = self.root / "raw.txt"
        payload = b"password = 'keepit'"
        self.fs.write(str(p), payload, module="test", redact=False)
        data = self.fs.read(str(p), module="test")
        assert data == payload

    def test_delete(self):
        p = self.root / "del.txt"
        self.fs.write(str(p), b"bye", module="test")
        self.fs.delete(str(p), module="test")
        assert not p.exists()

    def test_denied_write(self, tmp_path):
        fs = FileSandbox()
        with pytest.raises(Exception):
            fs.write("/tmp/outside.txt", b"data", module="test")

    def test_redact_buffer(self):
        payload = b"Authorization: Bearer abc123secret"
        redacted = self.fs.redact_buffer(payload)
        assert b"[REDACTED]" in redacted


# ---------------------------------------------------------------------------
# Coordinator tests
# ---------------------------------------------------------------------------


class TestCoordinator:
    @pytest.fixture(autouse=True)
    def _coord_env(self, tmp_path):
        os.environ["AEON_VAULT_KEY"] = Fernet.generate_key().decode()
        config = SecurityConfig(
            db_dir=str(tmp_path),
            cold_storage_dir=str(tmp_path / "audit"),
            threat_auto_pause_threshold=0.9,
        )
        self.coord = SecureExecutionEnvironment(config=config)
        yield
        os.environ.pop("AEON_VAULT_KEY", None)

    @pytest.mark.asyncio
    async def test_execute_action_success(self):
        async def action():
            return 42
        result = await self.coord.execute_action("test_action", action, category="test", estimated_cost=0)
        assert result == 42

    @pytest.mark.asyncio
    async def test_execute_action_blocked_by_pause(self):
        self.coord.pause("test")
        with pytest.raises(PolicyViolation, match="paused"):
            await self.coord.execute_action("blocked", lambda: 42, category="test", estimated_cost=1)
        self.coord.resume()

    def test_threat_score_zero_on_init(self):
        assert self.coord.get_threat_score() == 0.0

    def test_threat_summary(self):
        summary = self.coord.get_threat_summary()
        assert "threat_score" in summary
        assert "events_in_window" in summary

    def test_pause_resume(self):
        self.coord.pause("panic")
        paused, reason = self.coord.is_paused()
        assert paused
        assert "panic" in reason
        self.coord.resume()
        paused, _ = self.coord.is_paused()
        assert not paused


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------


def test_exception_hierarchy():
    assert issubclass(VaultError, Exception)
    assert issubclass(AuditError, Exception)
    assert issubclass(SpendCapExceeded, Exception)
    assert issubclass(SandboxError, Exception)
    assert issubclass(ApprovalRequired, Exception)
    assert issubclass(EmergencyPauseActive, Exception)
    assert issubclass(NetworkBlocked, Exception)
    assert issubclass(ThreatDetected, Exception)


# ---------------------------------------------------------------------------
# AuditTrail PII redaction tests
# ---------------------------------------------------------------------------


class TestAuditTrailRedaction:
    @pytest.fixture(autouse=True)
    def _trail_env(self, tmp_path):
        db = tmp_path / "audit.db"
        self.trail = AuditTrail(str(db))
        yield

    def test_redacts_credit_card(self):
        h = self.trail.log("payment", {"card": "4111 1111 1111 1111"})
        entry = self.trail.query_by_action("payment", limit=1)[0]
        assert "[REDACTED]" in json.dumps(entry["parameters"])
        assert "4111" not in json.dumps(entry["parameters"])

    def test_redacts_email(self):
        h = self.trail.log("signup", {"email": "user@example.com"})
        entry = self.trail.query_by_action("signup", limit=1)[0]
        assert "[REDACTED]" in json.dumps(entry["parameters"])
        assert "user@example.com" not in json.dumps(entry["parameters"])

    def test_redacts_phone(self):
        h = self.trail.log("contact", {"phone": "+1 (555) 123-4567"})
        entry = self.trail.query_by_action("contact", limit=1)[0]
        assert "[REDACTED]" in json.dumps(entry["parameters"])
        assert "555" not in json.dumps(entry["parameters"])

    def test_redacts_nested_dict(self):
        h = self.trail.log("config", {"nested": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz123"}})
        entry = self.trail.query_by_action("config", limit=1)[0]
        assert "[REDACTED]" in json.dumps(entry["parameters"])
        assert "sk-abcdefghijklmnopqrstuvwxyz123" not in json.dumps(entry["parameters"])

    def test_redacts_in_list(self):
        h = self.trail.log("batch", {"items": ["password = 'secret'"]})
        entry = self.trail.query_by_action("batch", limit=1)[0]
        assert "[REDACTED]" in json.dumps(entry["parameters"])
        assert "secret" not in json.dumps(entry["parameters"])

    def test_preserves_safe_values(self):
        h = self.trail.log("trade", {"symbol": "BTCUSD", "qty": 1})
        entry = self.trail.query_by_action("trade", limit=1)[0]
        assert entry["parameters"]["symbol"] == "BTCUSD"
        assert entry["parameters"]["qty"] == 1

    def test_hash_chain_still_valid_after_redaction(self):
        self.trail.log("a", {"email": "a@b.com"})
        self.trail.log("b", {"phone": "555-5555"})
        assert self.trail.verify_chain() is True


# ---------------------------------------------------------------------------
# RiskCoordinator tests
# ---------------------------------------------------------------------------


class TestRiskCoordinator:
    @pytest.fixture(autouse=True)
    def _coord_env(self, tmp_path):
        db = tmp_path / "risk.db"
        spend_db = tmp_path / "spend.db"
        from auton.security.spend_caps import SpendGuard
        self.spend_guard = SpendGuard(str(spend_db))
        self.rc = RiskCoordinator(
            db_path=str(db),
            spend_guard=self.spend_guard,
            low_amount=1.0,
            medium_amount=10.0,
            high_amount=50.0,
            critical_amount=200.0,
        )
        yield

    def test_low_risk_auto_approved(self):
        decision = {"action": "trade", "amount": 0.5, "risk_score": 0.1, "confidence": 0.9}
        review = self.rc.review_decision(decision, balance=100.0)
        assert review.risk_level == RiskLevel.LOW
        assert review.approved is True
        assert review.required_approvals == 0

    def test_high_risk_requires_approval(self):
        # risk_score=0.8 triggers HIGH (>=0.75) but not CRITICAL (<0.90)
        decision = {"action": "trade", "amount": 5.0, "risk_score": 0.8, "confidence": 0.7}
        review = self.rc.review_decision(decision, balance=100.0)
        assert review.risk_level == RiskLevel.HIGH
        assert review.approved is False
        assert review.required_approvals == 2

    def test_critical_risk_blocked(self):
        decision = {"action": "launch", "amount": 250.0, "risk_score": 0.95, "confidence": 0.5}
        review = self.rc.review_decision(decision, balance=100.0)
        assert review.risk_level == RiskLevel.CRITICAL
        assert review.approved is False
        assert review.required_approvals == 3

    def test_absolute_risk_limit(self):
        decision = {"action": "trade", "amount": 1.0, "risk_score": 0.99, "confidence": 0.5}
        review = self.rc.review_decision(decision, balance=100.0)
        assert review.risk_level == RiskLevel.CRITICAL
        assert review.approved is False
        assert "Absolute risk limit" in review.reason

    def test_approval_flow(self):
        decision = {"action": "trade", "amount": 5.0, "risk_score": 0.8, "confidence": 0.7}
        review = self.rc.review_decision(decision, balance=100.0)
        assert not review.approved

        r1 = self.rc.approve(review.decision_id, "alice")
        assert not r1.approved
        assert r1.current_approvals == 1

        r2 = self.rc.approve(review.decision_id, "bob")
        assert r2.approved
        assert r2.current_approvals == 2

    def test_duplicate_approval_idempotent(self):
        decision = {"action": "trade", "amount": 5.0, "risk_score": 0.8, "confidence": 0.7}
        review = self.rc.review_decision(decision, balance=100.0)
        self.rc.approve(review.decision_id, "alice")
        self.rc.approve(review.decision_id, "alice")  # same approver again — should not count twice
        r = self.rc.approve(review.decision_id, "bob")
        assert r.approved
        assert r.current_approvals == 2

    def test_get_pending_decisions(self):
        d1 = {"action": "trade", "amount": 15.0, "risk_score": 0.6, "confidence": 0.7}
        d2 = {"action": "trade", "amount": 0.5, "risk_score": 0.1, "confidence": 0.9}
        self.rc.review_decision(d1, balance=100.0)
        self.rc.review_decision(d2, balance=100.0)
        pending = self.rc.get_pending_decisions()
        assert len(pending) == 1
        assert pending[0].action == "trade"
        assert pending[0].amount == 15.0

    def test_review_history(self):
        decision = {"action": "trade", "amount": 15.0, "risk_score": 0.6, "confidence": 0.7}
        self.rc.review_decision(decision, balance=100.0)
        history = self.rc.get_review_history()
        assert len(history) == 1
        assert history[0]["status"] == "pending"

    def test_classify_risk_relative_amount(self):
        # Small balance makes moderate amounts high risk
        decision = {"action": "trade", "amount": 15.0, "risk_score": 0.2, "confidence": 0.9}
        review = self.rc.review_decision(decision, balance=40.0)
        assert review.risk_level == RiskLevel.HIGH

    def test_emergency_kill_switch(self):
        result = self.rc.emergency_kill_switch("test emergency")
        assert result["triggered"] is True
        assert "test emergency" in result["reason"]

    def test_is_kill_switch_active(self):
        assert not self.rc.is_kill_switch_active()
        self.rc.emergency_kill_switch("test")
        assert self.rc.is_kill_switch_active()

    def test_approve_nonexistent_decision(self):
        review = self.rc.approve("does_not_exist", "alice")
        assert not review.approved
        assert "not found" in review.reason
