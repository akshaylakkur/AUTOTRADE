"""Config migration engine for schema evolution."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auton.ledger.master_wallet import MasterWallet

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationProposal:
    """A proposed schema migration."""

    proposal_id: str
    new_keys: dict[str, Any]
    reason: str
    migration_script: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class MigrationResult:
    """Result of applying a migration."""

    success: bool
    migration_id: str
    message: str
    snapshot_path: Path | None = None


@dataclass(frozen=True)
class MigrationRecord:
    """Record of a completed migration."""

    migration_id: str
    timestamp: datetime
    new_keys: dict[str, Any]
    config_paths: list[Path]
    snapshot_path: Path
    applied: bool


class SchemaMigrationError(Exception):
    """Base exception for schema migration errors."""


class ConfigCorruptionError(SchemaMigrationError):
    """Config was corrupted during migration."""


class SchemaEvolver:
    """Handles config file migrations when new modules introduce new keys."""

    def __init__(
        self,
        config_paths: list[Path],
        migration_dir: Path,
        ledger: MasterWallet | None = None,
    ) -> None:
        self.config_paths = [Path(p) for p in config_paths]
        self.migration_dir = Path(migration_dir)
        self.migration_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger

    def _snapshot_configs(self, migration_id: str) -> Path:
        """Snapshot current config files."""
        snapshot_path = self.migration_dir / f"snapshot_{migration_id}"
        snapshot_path.mkdir(parents=True, exist_ok=True)
        for cfg in self.config_paths:
            if cfg.exists():
                shutil.copy2(cfg, snapshot_path / cfg.name)
        return snapshot_path

    def _generate_migration_script(self, new_keys: dict[str, Any], reason: str) -> str:
        """Generate a migration script that adds *new_keys* with defaults."""
        lines = [
            '"""Auto-generated config migration."""',
            "",
            "import json",
            "from pathlib import Path",
            "",
            f"new_keys = {json.dumps(new_keys, indent=4, default=str)}",
            "",
            "config_paths = [",
        ]
        for p in self.config_paths:
            lines.append(f'    Path("{p}"),')
        lines.extend([
            "]",
            "",
            "for cfg in config_paths:",
            "    if not cfg.exists():",
            "        cfg.parent.mkdir(parents=True, exist_ok=True)",
            "        data = {}",
            "    else:",
            "        data = json.loads(cfg.read_text(encoding='utf-8'))",
            "",
            "    for key, default in new_keys.items():",
            "        if key not in data:",
            "            data[key] = default",
            "",
            "    cfg.write_text(json.dumps(data, indent=2), encoding='utf-8')",
            "",
        ])
        return "\n".join(lines)

    def _validate_keys(self, new_keys: dict[str, Any]) -> None:
        """Validate that new keys are safe to add."""
        for key in new_keys:
            if "." in key or "/" in key or "\\" in key:
                raise SchemaMigrationError(f"Key contains path traversal characters: {key}")
            for cfg in self.config_paths:
                if cfg.exists():
                    data = json.loads(cfg.read_text(encoding="utf-8"))
                    if key in data:
                        raise SchemaMigrationError(f"Key already exists: {key}")
            # Check serialized size
            serialized = json.dumps(new_keys[key], default=str)
            if len(serialized) > 1024:
                raise SchemaMigrationError(f"Default value for {key} exceeds 1 KiB")

    def propose_migration(
        self,
        new_keys: dict[str, Any],
        reason: str,
    ) -> MigrationProposal:
        """Generate a migration script that adds *new_keys* with defaults."""
        self._validate_keys(new_keys)
        migration_id = f"migration_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        script = self._generate_migration_script(new_keys, reason)
        return MigrationProposal(
            proposal_id=migration_id,
            new_keys=dict(new_keys),
            reason=reason,
            migration_script=script,
        )

    def apply_migration(self, proposal: MigrationProposal) -> MigrationResult:
        """Apply the migration, snapshot old configs, write migration record."""
        try:
            snapshot_path = self._snapshot_configs(proposal.proposal_id)

            # Write migration script
            script_path = self.migration_dir / f"{proposal.proposal_id}.py"
            script_path.write_text(proposal.migration_script, encoding="utf-8")

            # Validate script syntax
            import py_compile
            try:
                py_compile.compile(str(script_path), doraise=True)
            except py_compile.PyCompileError as exc:
                return MigrationResult(
                    success=False,
                    migration_id=proposal.proposal_id,
                    message=f"Migration script syntax error: {exc}",
                )

            # Execute migration script
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                self._restore_snapshot(snapshot_path)
                return MigrationResult(
                    success=False,
                    migration_id=proposal.proposal_id,
                    message=f"Migration script failed: {result.stderr}",
                )

            # Record migration
            record = MigrationRecord(
                migration_id=proposal.proposal_id,
                timestamp=datetime.now(timezone.utc),
                new_keys=proposal.new_keys,
                config_paths=self.config_paths,
                snapshot_path=snapshot_path,
                applied=True,
            )
            record_path = self.migration_dir / f"{proposal.proposal_id}.json"
            record_path.write_text(
                json.dumps({
                    "migration_id": record.migration_id,
                    "timestamp": record.timestamp.isoformat(),
                    "new_keys": record.new_keys,
                    "config_paths": [str(p) for p in record.config_paths],
                    "snapshot_path": str(record.snapshot_path),
                    "applied": record.applied,
                }, indent=2),
                encoding="utf-8",
            )

            return MigrationResult(
                success=True,
                migration_id=proposal.proposal_id,
                message="Migration applied successfully",
                snapshot_path=snapshot_path,
            )

        except Exception as exc:
            logger.exception("Migration failed")
            return MigrationResult(
                success=False,
                migration_id=proposal.proposal_id,
                message=f"Migration error: {exc}",
            )

    def rollback_migration(self, migration_id: str) -> MigrationResult:
        """Restore config files from pre-migration snapshot."""
        snapshot_path = self.migration_dir / f"snapshot_{migration_id}"
        if not snapshot_path.exists():
            return MigrationResult(
                success=False,
                migration_id=migration_id,
                message="Snapshot not found",
            )
        self._restore_snapshot(snapshot_path)
        return MigrationResult(
            success=True,
            migration_id=migration_id,
            message="Rollback successful",
            snapshot_path=snapshot_path,
        )

    def list_migrations(self) -> list[MigrationRecord]:
        """Return all migrations, newest first."""
        records: list[MigrationRecord] = []
        for record_path in sorted(self.migration_dir.glob("migration_*.json"), reverse=True):
            data = json.loads(record_path.read_text(encoding="utf-8"))
            records.append(
                MigrationRecord(
                    migration_id=data["migration_id"],
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    new_keys=data["new_keys"],
                    config_paths=[Path(p) for p in data["config_paths"]],
                    snapshot_path=Path(data["snapshot_path"]),
                    applied=data["applied"],
                )
            )
        return records

    def _restore_snapshot(self, snapshot_path: Path) -> None:
        """Copy files from snapshot back to config paths."""
        for cfg in self.config_paths:
            snapshot_file = snapshot_path / cfg.name
            if snapshot_file.exists():
                shutil.copy2(snapshot_file, cfg)
            else:
                logger.warning("Snapshot file missing for %s", cfg)
