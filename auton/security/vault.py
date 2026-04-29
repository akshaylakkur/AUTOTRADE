"""Encrypted key vault for Project ÆON."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .exceptions import RotationRequired, VaultError


_VAULT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vault_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id TEXT UNIQUE NOT NULL,
    encrypted_secret BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_rotated_at TEXT NOT NULL,
    rotation_interval_days INTEGER NOT NULL DEFAULT 90,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_vault_key_id ON vault_entries(key_id);
"""

_ACCESS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vault_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id TEXT NOT NULL,
    caller TEXT NOT NULL,
    action TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vault_access_key ON vault_access_log(key_id);
"""


def _derive_key(master_key: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """Derive a Fernet-compatible key from a master key using PBKDF2.

    Returns (derived_key_bytes, salt).
    """
    if salt is None:
        salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", master_key.encode(), salt, 480_000, dklen=32)
    # Fernet keys must be 32 bytes, url-safe base64 encoded.
    fernet_key = base64.urlsafe_b64encode(derived)
    return fernet_key, salt


class SecretVault:
    """Encrypted credential store with key derivation, access auditing, and rotation tracking.

    Encryption key is derived from ``AEON_VAULT_KEY`` via PBKDF2.
    If absent, the vault refuses to initialize (fail-closed).
    """

    def __init__(
        self,
        db_path: str = "data/aeon_vault.db",
        audit_log=None,
        auto_rotate_days: int = 90,
    ) -> None:
        self._db_path = db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._audit_log = audit_log
        self._sealed = False
        self._auto_rotate_days = auto_rotate_days

        master_key = os.environ.get("AEON_VAULT_KEY")
        if not master_key:
            raise VaultError("AEON_VAULT_KEY environment variable is not set")

        # Load or generate salt
        salt_env = os.environ.get("AEON_VAULT_SALT")
        if salt_env:
            self._salt = base64.b64decode(salt_env)
        else:
            self._salt = os.urandom(16)
            os.environ["AEON_VAULT_SALT"] = base64.b64encode(self._salt).decode()

        self._key, self._salt = _derive_key(master_key, self._salt)
        self._fernet = Fernet(self._key)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(_VAULT_TABLE_SQL)
            conn.executescript(_ACCESS_TABLE_SQL)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _log_access(self, key_id: str, caller: str, action: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO vault_access_log (key_id, caller, action, timestamp)
                VALUES (?, ?, ?, ?);
                """,
                (key_id, caller, action, self._now()),
            )
        if self._audit_log:
            self._audit_log.log(
                "vault_access",
                {"key_id": key_id, "caller": caller, "action": action},
            )

    def store(
        self,
        key_id: str,
        secret: str,
        *,
        metadata: dict[str, Any] | None = None,
        rotation_interval_days: int | None = None,
    ) -> None:
        """Encrypt and store *secret* under *key_id*."""
        if self._sealed:
            raise VaultError("Vault is sealed")
        encrypted = self._fernet.encrypt(secret.encode())
        now = self._now()
        meta = json.dumps(metadata or {}, sort_keys=True) if metadata else "{}"
        interval = rotation_interval_days or self._auto_rotate_days
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO vault_entries (
                    key_id, encrypted_secret, created_at, updated_at,
                    last_rotated_at, rotation_interval_days, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_id) DO UPDATE SET
                    encrypted_secret = excluded.encrypted_secret,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json,
                    rotation_interval_days = excluded.rotation_interval_days;
                """,
                (key_id, encrypted, now, now, now, interval, meta),
            )
        self._log_access(key_id, caller="system", action="store")

    def retrieve(self, key_id: str, *, caller: str = "unknown") -> str:
        """Retrieve and decrypt the secret stored under *key_id*."""
        if self._sealed:
            raise VaultError("Vault is sealed")

        # Check rotation status before retrieval
        self._check_rotation(key_id)

        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT encrypted_secret FROM vault_entries WHERE key_id = ?;",
                (key_id,),
            ).fetchone()
        if row is None:
            raise VaultError(f"No secret found for key_id: {key_id}")
        try:
            plaintext = self._fernet.decrypt(row[0]).decode()
        except InvalidToken as exc:
            raise VaultError(f"Failed to decrypt secret for key_id: {key_id}") from exc
        self._log_access(key_id, caller=caller, action="retrieve")
        return plaintext

    def _check_rotation(self, key_id: str) -> None:
        """Raise RotationRequired if credential is past its rotation window."""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT last_rotated_at, rotation_interval_days FROM vault_entries WHERE key_id = ?;",
                (key_id,),
            ).fetchone()
        if row is None:
            return
        last_rotated = datetime.fromisoformat(row[0])
        interval = row[1]
        if interval <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=interval)
        if last_rotated < cutoff:
            raise RotationRequired(
                f"Credential '{key_id}' has exceeded its {interval}-day rotation window"
            )

    def get_rotation_status(self, key_id: str) -> dict[str, Any]:
        """Return rotation status for a credential."""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT last_rotated_at, rotation_interval_days, updated_at, created_at FROM vault_entries WHERE key_id = ?;",
                (key_id,),
            ).fetchone()
        if row is None:
            return {"exists": False}
        last_rotated = datetime.fromisoformat(row[0])
        interval = row[1]
        days_since = (datetime.now(timezone.utc) - last_rotated).days
        overdue = interval > 0 and days_since > interval
        return {
            "exists": True,
            "last_rotated_at": row[0],
            "rotation_interval_days": interval,
            "days_since_rotation": days_since,
            "overdue": overdue,
            "updated_at": row[2],
            "created_at": row[3],
        }

    def get_all_overdue(self) -> list[dict[str, Any]]:
        """Return all credentials that need rotation."""
        overdue = []
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT key_id, last_rotated_at, rotation_interval_days FROM vault_entries;"
            ).fetchall()
        for key_id, last_rotated_str, interval in rows:
            if interval <= 0:
                continue
            last_rotated = datetime.fromisoformat(last_rotated_str)
            days_since = (datetime.now(timezone.utc) - last_rotated).days
            if days_since > interval:
                overdue.append({
                    "key_id": key_id,
                    "days_since_rotation": days_since,
                    "rotation_interval_days": interval,
                })
        return overdue

    def delete(self, key_id: str, *, caller: str = "unknown") -> None:
        """Remove the entry for *key_id*."""
        if self._sealed:
            raise VaultError("Vault is sealed")
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM vault_entries WHERE key_id = ?;", (key_id,)
            )
            if cur.rowcount == 0:
                raise VaultError(f"No secret found for key_id: {key_id}")
        self._log_access(key_id, caller=caller, action="delete")

    def rotate_key(self, new_key: str) -> "SecretVault":
        """Re-encrypt all secrets with *new_key* and return a new vault instance."""
        if self._sealed:
            raise VaultError("Vault is sealed")
        new_fernet = Fernet(new_key.encode())
        now = self._now()
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id, key_id, encrypted_secret FROM vault_entries;"
            ).fetchall()
            for row_id, key_id, encrypted in rows:
                try:
                    plaintext = self._fernet.decrypt(encrypted)
                except InvalidToken as exc:
                    raise VaultError(
                        f"Failed to decrypt secret for key_id: {key_id}"
                    ) from exc
                re_encrypted = new_fernet.encrypt(plaintext)
                conn.execute(
                    "UPDATE vault_entries SET encrypted_secret = ?, last_rotated_at = ? WHERE id = ?;",
                    (re_encrypted, now, row_id),
                )
        instance = object.__new__(SecretVault)
        instance._db_path = self._db_path
        instance._fernet = new_fernet
        instance._sealed = False
        instance._audit_log = self._audit_log
        instance._salt = self._salt
        instance._auto_rotate_days = self._auto_rotate_days
        instance._key = new_key.encode()
        return instance

    def seal(self) -> None:
        """Zero the in-memory Fernet key and prevent further access until unsealed."""
        self._sealed = True
        del self._fernet
        if self._audit_log:
            self._audit_log.log("vault_seal", {})

    def unseal(self, master_key: str) -> None:
        """Restore the vault using *master_key* (derive Fernet key via PBKDF2)."""
        derived, _ = _derive_key(master_key, self._salt)
        self._fernet = Fernet(derived)
        self._sealed = False
        if self._audit_log:
            self._audit_log.log("vault_unseal", {})

    def revoke_all(self, *, caller: str = "unknown") -> None:
        """Delete all vault entries (irreversible)."""
        if self._sealed:
            raise VaultError("Vault is sealed")
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM vault_entries;")
        self._log_access("*", caller=caller, action="revoke_all")
        if self._audit_log:
            self._audit_log.log("vault_revoke_all", {})

    def get_access_log(
        self,
        key_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent access records for *key_id*."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT key_id, caller, action, timestamp
                FROM vault_access_log
                WHERE key_id = ?
                ORDER BY id DESC
                LIMIT ?;
                """,
                (key_id, limit),
            ).fetchall()
        return [
            {
                "key_id": r[0],
                "caller": r[1],
                "action": r[2],
                "timestamp": r[3],
            }
            for r in rows
        ]


# Backward-compatible alias
Vault = SecretVault
