"""Secure Execution Environment coordinator for Project ÆON."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from .audit_trail import AuditLog
from .config import SecurityConfig
from .exceptions import (
    ApprovalRequired,
    BudgetExceeded,
    EmergencyPauseActive,
    PolicyViolation,
    SpendCapExceeded,
    ThreatDetected,
)
from .file_sandbox import FileSandbox
from .network_gate import NetworkGate
from .sandbox import ProcessSandbox
from .spend_caps import SpendGuard
from .vault import SecretVault


class SecureExecutionEnvironment:
    """Wires all security components and exposes a simplified API for ÆON.

    Every action flows through :meth:`execute_action`, the golden path:
    1. Pre-audit log the action.
    2. SpendGuard checks the budget.
    3. The executor runs.
    4. Post-audit log the result.
    5. If the action spent money, SpendGuard records it and debits the ledger.

    Cross-module threat correlation automatically scores events and triggers
    responses when thresholds are crossed.
    """

    def __init__(
        self,
        wallet=None,
        config: SecurityConfig | None = None,
    ) -> None:
        self._config = config or SecurityConfig()
        self._audit = AuditLog(
            db_path=f"{self._config.db_dir}/aeon_audit.db",
            jsonl_dir=self._config.cold_storage_dir,
        )
        self._process = ProcessSandbox(
            audit_log=self._audit,
            default_limits=self._config.default_sandbox_limits,
        )
        self._network = NetworkGate(
            audit_log=self._audit,
            rules=list(self._config.network_rules),
            blocklist_ips=list(self._config.network_blocklist_ips),
            require_https=self._config.network_require_https,
        )
        self._files = FileSandbox(
            audit_log=self._audit,
            write_roots=list(self._config.file_write_roots),
            secret_patterns=list(self._config.secret_patterns),
        )
        self._vault = SecretVault(
            db_path=f"{self._config.db_dir}/aeon_vault.db",
            audit_log=self._audit,
            auto_rotate_days=self._config.vault_auto_rotate_days,
        )
        self._spend = SpendGuard(
            wallet=wallet,
            audit_log=self._audit,
            db_path=f"{self._config.db_dir}/aeon_spend.db",
        )

        # Threat correlation state
        self._event_window: list[dict[str, Any]] = []
        self._event_counts: dict[str, int] = defaultdict(int)
        self._threat_score = 0.0
        self._paused = False
        self._pause_reason: str | None = None

    @property
    def process(self) -> ProcessSandbox:
        return self._process

    @property
    def network(self) -> NetworkGate:
        return self._network

    @property
    def files(self) -> FileSandbox:
        return self._files

    @property
    def vault(self) -> SecretVault:
        return self._vault

    @property
    def audit(self) -> AuditLog:
        return self._audit

    @property
    def spend(self) -> SpendGuard:
        return self._spend

    def is_paused(self) -> tuple[bool, str | None]:
        """Return (paused, reason) for coordinator-level pause."""
        return self._paused, self._pause_reason

    def pause(self, reason: str) -> None:
        """Pause all operations at the coordinator level."""
        self._paused = True
        self._pause_reason = reason
        self._spend.pause(reason)
        self._audit.log(
            "coordinator_pause",
            {"reason": reason, "threat_score": self._threat_score},
            severity="critical",
        )

    def resume(self, reason: str = "manual") -> None:
        """Resume operations."""
        self._paused = False
        self._pause_reason = None
        self._spend.resume(reason)
        self._audit.log("coordinator_resume", {"reason": reason})

    def _record_event(self, event_type: str, details: dict[str, Any]) -> None:
        """Record a security event for correlation and scoring."""
        now = time.monotonic()
        self._event_window.append({
            "type": event_type,
            "time": now,
            "details": details,
        })
        # Prune events older than 5 minutes
        cutoff = now - 300
        self._event_window = [e for e in self._event_window if e["time"] > cutoff]
        self._event_counts[event_type] += 1
        self._update_threat_score()

    def _update_threat_score(self) -> None:
        """Recompute threat score from recent events."""
        score = 0.0
        event_types = defaultdict(int)
        for e in self._event_window:
            event_types[e["type"]] += 1

        # Multiple blocked network requests
        if event_types.get("network_blocked", 0) >= 5:
            score += 0.3
        if event_types.get("network_blocked", 0) >= 10:
            score += 0.4

        # Multiple file access denials
        if event_types.get("file_access_denied", 0) >= 3:
            score += 0.2

        # Vault access failures
        if event_types.get("vault_error", 0) >= 3:
            score += 0.2

        # Spend cap breaches
        if event_types.get("spend_blocked", 0) >= 3:
            score += 0.2

        # Rate limiting events
        if event_types.get("network_rate_limited", 0) >= 5:
            score += 0.15

        self._threat_score = min(score, 1.0)

        # Automated response triggers
        if self._threat_score >= self._config.threat_auto_pause_threshold:
            if not self._paused:
                self.pause(
                    f"Automated threat response: score {self._threat_score:.2f} exceeded threshold"
                )
                raise ThreatDetected(
                    f"Threat score {self._threat_score:.2f} triggered automatic pause"
                )

    def get_threat_score(self) -> float:
        """Return current threat score."""
        self._update_threat_score()
        return self._threat_score

    def get_threat_summary(self) -> dict[str, Any]:
        """Return a summary of current threat state."""
        self._update_threat_score()
        return {
            "threat_score": self._threat_score,
            "events_in_window": len(self._event_window),
            "event_breakdown": dict(self._event_counts),
            "paused": self._paused,
            "pause_reason": self._pause_reason,
        }

    async def execute_action(
        self,
        action_type: str,
        executor: Callable[[], Awaitable[Any]],
        *,
        category: str,
        estimated_cost: float = 0.0,
        audit_params: dict[str, Any] | None = None,
    ) -> Any:
        """Run *executor* through the full security pipeline.

        :param action_type: High-level action name for the audit trail.
        :param executor: Async callable that performs the action.
        :param category: Spend category for budget enforcement.
        :param estimated_cost: Amount the action is expected to cost.
        :param audit_params: Additional parameters to log.
        :returns: The return value of *executor*.
        :raises BudgetExceeded: If the spend cap would be breached.
        :raises EmergencyPauseActive: If spending is paused.
        :raises ThreatDetected: If threat score auto-pauses operations.
        """
        if self._paused:
            raise PolicyViolation(f"Operations paused: {self._pause_reason}")

        params = audit_params or {}
        params["category"] = category
        params["estimated_cost"] = estimated_cost

        # 1. Pre-audit
        log_id = self._audit.pre_log(action_type, params)

        # 2. SpendGuard quote
        if estimated_cost > 0:
            allowed, reason = self._spend.quote_spend(category, estimated_cost)
            if not allowed:
                self._record_event("spend_blocked", {"category": category, "amount": estimated_cost, "reason": reason})
                self._audit.post_log(
                    log_id,
                    {"status": "blocked", "reason": reason, "stage": "quote"},
                )
                if "requires manual confirmation" in reason:
                    raise ApprovalRequired(reason)
                raise BudgetExceeded(reason)

        # 3. Execute
        try:
            result = await executor()
        except Exception as exc:
            self._record_event("action_error", {"action_type": action_type, "error": str(exc)})
            self._audit.post_log(
                log_id,
                {"status": "error", "error": str(exc)},
            )
            raise

        # 4. Post-audit
        self._audit.post_log(
            log_id,
            {"status": "success", "result": str(result)},
        )

        # 5. Record actual spend if any
        if estimated_cost > 0:
            try:
                self._spend.check_and_record(
                    category,
                    estimated_cost,
                    reason=action_type,
                )
            except BudgetExceeded:
                self._record_event("spend_record_failed", {"category": category, "amount": estimated_cost})
                self._audit.log(
                    "spend_record_failed",
                    {"category": category, "amount": estimated_cost, "action": action_type},
                    severity="warning",
                )

        return result

    async def check_vault_health(self) -> dict[str, Any]:
        """Check vault for overdue credentials and other health indicators."""
        overdue = self._vault.get_all_overdue()
        status = {
            "overdue_credentials": overdue,
            "overdue_count": len(overdue),
        }
        self._audit.log("vault_health_check", status)
        return status

    async def revoke_all_keys(self, reason: str) -> None:
        """Emergency revocation of all vault keys."""
        self._vault.revoke_all(caller="coordinator")
        self._audit.log(
            "emergency_key_revocation",
            {"reason": reason},
            severity="critical",
        )
