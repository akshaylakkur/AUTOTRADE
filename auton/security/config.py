"""Security configuration dataclasses for Project ÆON."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from auton.core.constants import DEFAULT_SMTP_PORT, DEFAULT_SMTP_USE_TLS


@dataclass(frozen=True)
class ResourceLimits:
    """Hard resource ceilings for ProcessSandbox executions."""

    max_memory_mb: int = 128
    max_disk_mb: int = 64
    max_cpu_time_seconds: int = 30
    max_wall_time_seconds: int = 60
    max_file_descriptors: int = 32


@dataclass(frozen=True)
class NetworkRule:
    """A single filtering rule for outbound traffic."""

    domain: str
    action: str = "allow"  # "allow" or "deny"
    max_requests_per_minute: int = 60
    max_bytes_per_minute: int = 10_000_000
    require_https: bool = True
    allowed_ips: Sequence[str] | None = None


@dataclass(frozen=True)
class FileAccessLog:
    """Immutable record of a single filesystem operation."""

    timestamp: str
    operation: str
    path: str
    allowed: bool
    size_bytes: int | None
    module: str


@dataclass(frozen=True)
class SpendGuardConfig:
    """Per-category spend limits."""

    category: str
    hourly: float = 0.0
    daily: float = 0.0
    weekly: float = 0.0
    monthly: float = 0.0
    total: float = 0.0
    # Per-transaction approval thresholds
    auto_approve_threshold: float = 0.0  # 0 means no auto-approve
    confirmation_threshold: float = 0.0  # 0 means no confirmation tier


@dataclass(frozen=True)
class SecurityConfig:
    """Aggregated tunables for the Secure Execution Environment."""

    db_dir: str = "data"
    cold_storage_dir: str = "cold_storage/audit"
    default_sandbox_limits: ResourceLimits = field(default_factory=ResourceLimits)
    network_rules: Sequence[NetworkRule] = field(default_factory=tuple)
    network_blocklist_ips: Sequence[str] = field(default_factory=tuple)
    network_require_https: bool = True
    file_write_roots: Sequence[str] = field(
        default_factory=lambda: (
            "data/",
            "auton/limbs/",
            "logs/",
            "cold_storage/audit/",
        )
    )
    file_max_bytes_per_write: int = 100 * 1024 * 1024  # 100 MB
    file_max_bytes_per_root: int = 1024 * 1024 * 1024  # 1 GB
    global_spend_cap: float = 0.0  # 0 means no global cap
    vault_auto_rotate_days: int = 90
    vault_key_derivation_salt: str | None = None
    # Secret detection patterns for redaction
    secret_patterns: Sequence[str] = field(
        default_factory=lambda: (
            r"(?i)(password|passwd|pwd)\s*=\s*['\"][^'\"]+['\"]",
            r"(?i)(api[_-]?key|apikey|secret[_-]?key)\s*=\s*['\"][^'\"]+['\"]",
            r"(?i)(token|bearer)\s+['\"]?[a-zA-Z0-9_\-]{20,}['\"]?",
            r"(?i)(aws_access_key_id|aws_secret_access_key)\s*=\s*['\"][^'\"]+['\"]",
            r"(?i)(private[_-]?key|ssh[_-]?key)\s*=\s*['\"][^'\"]+['\"]",
            r"(?i)(authorization:\s*bearer\s+)[a-zA-Z0-9_\-\.=]+",
            r"(?i)(sk-[a-zA-Z0-9]{20,})",
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            r"\b[0-9]{4}[\s-]?[0-9]{4}[\s-]?[0-9]{4}[\s-]?[0-9]{4}\b",  # Credit cards
            r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b",  # SSN
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Emails
            r"\b(?:\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",  # Phone numbers
        )
    )
    # Threat response thresholds
    threat_score_threshold: float = 0.7
    threat_auto_pause_threshold: float = 0.9
    max_events_per_minute: int = 100


@dataclass(frozen=True)
class EmailConfig:
    """SMTP configuration for human-in-the-loop approval emails."""

    smtp_host: str = ""
    smtp_port: int = DEFAULT_SMTP_PORT
    sender_email: str = ""
    sender_password: str = ""
    recipient_email: str = ""
    use_tls: bool = DEFAULT_SMTP_USE_TLS


def validate_email_config(config: EmailConfig) -> None:
    """Raise ValueError if any required SMTP field is empty."""
    required = ("smtp_host", "sender_email", "sender_password", "recipient_email")
    missing = [f for f in required if not getattr(config, f)]
    if missing:
        raise ValueError(
            f"Email configuration incomplete. Missing fields: {', '.join(missing)}"
        )


def store_email_password(vault: Any, password: str) -> None:
    """Encrypt and store the approval email password in the vault.

    Args:
        vault: A ``SecretVault`` instance (imported locally to avoid cycles).
        password: The plain-text SMTP password.
    """
    vault.store("approval_email_password", password, metadata={"service": "smtp"})


def retrieve_email_password(vault: Any, *, caller: str = "unknown") -> str:
    """Retrieve the decrypted approval email password from the vault.

    Args:
        vault: A ``SecretVault`` instance (imported locally to avoid cycles).
        caller: Identifier for the requesting module (audit trail).

    Returns:
        The plain-text SMTP password.
    """
    return vault.retrieve("approval_email_password", caller=caller)
