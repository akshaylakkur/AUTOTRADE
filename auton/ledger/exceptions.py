"""Custom exceptions for the ÆON ledger."""


class LedgerError(Exception):
    """Base exception for all ledger-related errors."""


class InsufficientFundsError(LedgerError):
    """Raised when a debit would cause the balance to go negative."""
