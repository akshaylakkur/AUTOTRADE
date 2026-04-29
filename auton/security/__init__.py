"""Security layer for Project ÆON."""

from .audit_trail import AuditLog, AuditTrail
from .config import (
    EmailConfig,
    FileAccessLog,
    NetworkRule,
    ResourceLimits,
    SecurityConfig,
    SpendGuardConfig,
    retrieve_email_password,
    store_email_password,
    validate_email_config,
)
from .coordinator import SecureExecutionEnvironment
from .exceptions import (
    ApprovalRequired,
    AuditError,
    BudgetExceeded,
    EmergencyPauseActive,
    FileAccessDenied,
    NetworkBlocked,
    PolicyViolation,
    RotationRequired,
    SandboxError,
    SpendCapExceeded,
    ThreatDetected,
    VaultError,
)
from .file_sandbox import FileSandbox
from .network_gate import NetworkGate
from .risk_coordinator import (
    PendingDecision,
    RiskCoordinator,
    RiskLevel,
    RiskReview,
)
from .sandbox import ProcessSandbox, Sandbox, SandboxResult
from .spend_caps import CapConfig, SpendCaps, SpendGuard
from .vault import SecretVault, Vault

__all__ = [
    "ApprovalRequired",
    "AuditLog",
    "AuditTrail",
    "AuditError",
    "BudgetExceeded",
    "CapConfig",
    "EmailConfig",
    "EmergencyPauseActive",
    "FileAccessDenied",
    "FileAccessLog",
    "FileSandbox",
    "NetworkBlocked",
    "NetworkGate",
    "NetworkRule",
    "PendingDecision",
    "PolicyViolation",
    "ProcessSandbox",
    "ResourceLimits",
    "retrieve_email_password",
    "RiskCoordinator",
    "RiskLevel",
    "RiskReview",
    "RotationRequired",
    "Sandbox",
    "SandboxError",
    "SandboxResult",
    "SecureExecutionEnvironment",
    "SecurityConfig",
    "SpendCapExceeded",
    "SpendCaps",
    "SpendGuard",
    "SpendGuardConfig",
    "SecretVault",
    "store_email_password",
    "ThreatDetected",
    "validate_email_config",
    "Vault",
    "VaultError",
]
