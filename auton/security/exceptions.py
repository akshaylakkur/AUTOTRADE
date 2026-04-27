"""Security exceptions for Project ÆON."""


class VaultError(Exception):
    """Raised when a vault operation fails."""


class AuditError(Exception):
    """Raised when an audit trail operation fails."""


class SpendCapExceeded(Exception):
    """Raised when a spend would exceed a configured cap."""


class SandboxError(Exception):
    """Raised when a sandbox operation fails."""
