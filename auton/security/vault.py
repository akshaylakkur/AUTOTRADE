"""Encrypted key vault for Project ÆON."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing_extensions import Self

from cryptography.fernet import Fernet, InvalidToken

from .exceptions import VaultError


class Vault:
    """Encrypted key-value store backed by SQLite.

    Secrets are encrypted at rest using Fernet. The encryption key is read
    from the ``AEON_VAULT_KEY`` environment variable.
    """

    _TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS vault_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_id TEXT UNIQUE NOT NULL,
        encrypted_secret BLOB NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_vault_key_id ON vault_entries(key_id);
    """

    def __init__(self, db_path: str = "aeon_vault.db") -> None:
        self._db_path = db_path
        key = os.environ.get("AEON_VAULT_KEY")
        if not key:
            raise VaultError("AEON_VAULT_KEY environment variable is not set")
        self._fernet = Fernet(key.encode())
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(self._TABLE_SQL)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def store(self, key_id: str, secret: str) -> None:
        """Encrypt and store *secret* under *key_id*."""
        encrypted = self._fernet.encrypt(secret.encode())
        now = self._now()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO vault_entries (key_id, encrypted_secret, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key_id) DO UPDATE SET
                    encrypted_secret = excluded.encrypted_secret,
                    updated_at = excluded.updated_at;
                """,
                (key_id, encrypted, now, now),
            )

    def retrieve(self, key_id: str) -> str:
        """Retrieve and decrypt the secret stored under *key_id*."""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT encrypted_secret FROM vault_entries WHERE key_id = ?;",
                (key_id,),
            ).fetchone()
        if row is None:
            raise VaultError(f"No secret found for key_id: {key_id}")
        try:
            return self._fernet.decrypt(row[0]).decode()
        except InvalidToken as exc:
            raise VaultError(f"Failed to decrypt secret for key_id: {key_id}") from exc

    def delete(self, key_id: str) -> None:
        """Remove the entry for *key_id*."""
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM vault_entries WHERE key_id = ?;", (key_id,)
            )
            if cur.rowcount == 0:
                raise VaultError(f"No secret found for key_id: {key_id}")

    def rotate_key(self, new_key: str) -> Self:
        """Re-encrypt all secrets with *new_key* and return a new Vault instance.

        The caller is responsible for updating the ``AEON_VAULT_KEY``
        environment variable after rotation.
        """
        new_fernet = Fernet(new_key.encode())
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
                    "UPDATE vault_entries SET encrypted_secret = ? WHERE id = ?;",
                    (re_encrypted, row_id),
                )
        # Return a new Vault instance using the new key.
        instance = object.__new__(Vault)
        instance._db_path = self._db_path
        instance._fernet = new_fernet
        return instance
