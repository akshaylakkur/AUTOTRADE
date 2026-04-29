"""Security exceptions for Project ÆON."""


class VaultError(Exception):
    """Raised when a vault operation fails."""


class AuditError(Exception):
    """Raised when an audit trail operation fails."""


class SpendCapExceeded(Exception):
    """Raised when a spend would exceed a configured cap."""


class SandboxError(Exception):
    """Raised when a sandbox operation fails."""


class NetworkBlocked(Exception):
    """Raised when a network request is denied by the gate."""


class FileAccessDenied(Exception):
    """Raised when a file operation violates sandbox policy."""


class BudgetExceeded(Exception):
    """Raised when a spend would exceed the global or category budget."""


class ApprovalRequired(Exception):
    """Raised when a spend requires human confirmation (multi-tier approval)."""


class EmergencyPauseActive(Exception):
    """Raised when operations are blocked by emergency pause."""


class ThreatDetected(Exception):
    """Raised when the security coordinator detects an active threat."""


class PolicyViolation(Exception):
    """Raised when an action violates the central security policy."""


class RotationRequired(Exception):
    """Raised when a credential has exceeded its rotation window."""
