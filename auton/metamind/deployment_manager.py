"""Deployment Manager: deploys generated SaaS products to cloud providers."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auton.metamind.ci_cd_generator import CICDGenerator

logger = logging.getLogger(__name__)


class DeploymentError(Exception):
    """Error during deployment."""


@dataclass(frozen=True)
class DeploymentRecord:
    """Record of a deployment operation."""

    deployment_id: str
    product_id: str
    target: str
    status: str  # pending, building, deployed, failed
    url: str = ""
    logs: str = ""
    cost: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "product_id": self.product_id,
            "target": self.target,
            "status": self.status,
            "url": self.url,
            "logs": self.logs,
            "cost": self.cost,
            "timestamp": self.timestamp.isoformat(),
        }


class DeploymentManager:
    """Manages deployment of SaaS products to cloud providers."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS deployments (
        deployment_id   TEXT PRIMARY KEY,
        product_id      TEXT NOT NULL,
        target          TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        url             TEXT DEFAULT '',
        logs            TEXT DEFAULT '',
        cost            REAL DEFAULT 0.0,
        timestamp       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_deployments_product ON deployments(product_id);
    CREATE INDEX IF NOT EXISTS idx_deployments_status ON deployments(status);
    """

    def __init__(
        self,
        db_path: str | Path = "data/deployments.db",
        cicd_generator: CICDGenerator | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cicd = cicd_generator or CICDGenerator()
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(self._DDL)
            conn.commit()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _record_deployment(
        self,
        deployment_id: str,
        product_id: str,
        target: str,
        status: str,
        url: str = "",
        logs: str = "",
        cost: float = 0.0,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO deployments
                (deployment_id, product_id, target, status, url, logs, cost, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deployment_id,
                    product_id,
                    target,
                    status,
                    url,
                    logs,
                    cost,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def _run_command(
        self,
        cmd: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 300,
    ) -> tuple[str, str, int]:
        """Run a shell command and return stdout, stderr, returncode."""
        merged_env = {**os.environ, **(env or {})}
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=merged_env,
                timeout=timeout,
            )
            return proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            return exc.stdout or "", exc.stderr or "", -9
        except FileNotFoundError as exc:
            raise DeploymentError(f"Command not found: {cmd[0]}") from exc

    # ------------------------------------------------------------------ #
    # Platform-specific deployers
    # ------------------------------------------------------------------ #
    def deploy_to_fly(
        self,
        deployment_id: str,
        product_id: str,
        source_dir: Path,
        app_name: str,
    ) -> DeploymentRecord:
        """Deploy a Dockerized app to Fly.io."""
        self._record_deployment(deployment_id, product_id, "fly.io", "building")
        stdout = ""
        stderr = ""
        try:
            # Check if app exists, create if not
            _, err, rc = self._run_command(
                ["flyctl", "status", "--app", app_name],
                cwd=source_dir,
            )
            if rc != 0:
                out, err, rc = self._run_command(
                    ["flyctl", "launch", "--name", app_name, "--region", "ord", "--no-deploy"],
                    cwd=source_dir,
                )
                stdout += out
                stderr += err
                if rc != 0:
                    raise DeploymentError(f"flyctl launch failed: {stderr}")

            out, err, rc = self._run_command(
                ["flyctl", "deploy", "--app", app_name],
                cwd=source_dir,
                timeout=600,
            )
            stdout += out
            stderr += err
            if rc != 0:
                raise DeploymentError(f"flyctl deploy failed: {stderr}")

            url = f"https://{app_name}.fly.dev"
            self._record_deployment(
                deployment_id, product_id, "fly.io", "deployed", url=url, logs=stdout + stderr
            )
            logger.info("Deployed %s to Fly.io: %s", product_id, url)
            return DeploymentRecord(
                deployment_id=deployment_id,
                product_id=product_id,
                target="fly.io",
                status="deployed",
                url=url,
                logs=stdout + stderr,
            )
        except DeploymentError:
            self._record_deployment(
                deployment_id, product_id, "fly.io", "failed", logs=stdout + stderr
            )
            raise
        except Exception as exc:
            self._record_deployment(
                deployment_id, product_id, "fly.io", "failed", logs=str(exc)
            )
            raise DeploymentError(f"Unexpected error deploying to Fly.io: {exc}") from exc

    def deploy_to_railway(
        self,
        deployment_id: str,
        product_id: str,
        source_dir: Path,
        project_name: str,
    ) -> DeploymentRecord:
        """Deploy a Dockerized app to Railway."""
        self._record_deployment(deployment_id, product_id, "railway", "building")
        try:
            # Railway uses its CLI and project linking
            out, err, rc = self._run_command(
                ["railway", "login"],
                cwd=source_dir,
            )
            if rc != 0:
                raise DeploymentError(f"railway login failed: {err}")

            out, err, rc = self._run_command(
                ["railway", "link", "--project", project_name],
                cwd=source_dir,
            )
            if rc != 0:
                out, err, rc = self._run_command(
                    ["railway", "init"],
                    cwd=source_dir,
                )
                if rc != 0:
                    raise DeploymentError(f"railway init failed: {err}")

            out, err, rc = self._run_command(
                ["railway", "up"],
                cwd=source_dir,
                timeout=600,
            )
            if rc != 0:
                raise DeploymentError(f"railway up failed: {err}")

            self._record_deployment(
                deployment_id, product_id, "railway", "deployed", logs=out + err
            )
            logger.info("Deployed %s to Railway", product_id)
            return DeploymentRecord(
                deployment_id=deployment_id,
                product_id=product_id,
                target="railway",
                status="deployed",
                logs=out + err,
            )
        except DeploymentError:
            raise
        except Exception as exc:
            raise DeploymentError(f"Unexpected error deploying to Railway: {exc}") from exc

    def deploy_to_render(
        self,
        deployment_id: str,
        product_id: str,
        source_dir: Path,
        service_name: str,
        api_key: str = "",
    ) -> DeploymentRecord:
        """Deploy a Dockerized app to Render via API."""
        self._record_deployment(deployment_id, product_id, "render", "building")
        try:
            import urllib.request
            import urllib.error

            payload = json.dumps({
                "type": "web_service",
                "name": service_name,
                "region": "oregon",
                "env": "docker",
            }).encode()

            req = urllib.request.Request(
                "https://api.render.com/v1/services",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key or os.environ.get('RENDER_API_KEY', '')}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode()
                self._record_deployment(
                    deployment_id, product_id, "render", "deployed", logs=body
                )
                logger.info("Deployed %s to Render", product_id)
                return DeploymentRecord(
                    deployment_id=deployment_id,
                    product_id=product_id,
                    target="render",
                    status="deployed",
                    logs=body,
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode() if exc.fp else str(exc)
            self._record_deployment(
                deployment_id, product_id, "render", "failed", logs=body
            )
            raise DeploymentError(f"Render API error: {body}") from exc
        except Exception as exc:
            self._record_deployment(
                deployment_id, product_id, "render", "failed", logs=str(exc)
            )
            raise DeploymentError(f"Unexpected error deploying to Render: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Generic deploy dispatcher
    # ------------------------------------------------------------------ #
    def deploy(
        self,
        deployment_id: str,
        product_id: str,
        source_dir: Path,
        target: str,
        **kwargs: Any,
    ) -> DeploymentRecord:
        """Dispatch deployment to the correct platform handler."""
        if target == "fly.io":
            return self.deploy_to_fly(deployment_id, product_id, source_dir, kwargs.get("app_name", product_id))
        if target == "railway":
            return self.deploy_to_railway(deployment_id, product_id, source_dir, kwargs.get("project_name", product_id))
        if target == "render":
            return self.deploy_to_render(
                deployment_id, product_id, source_dir, kwargs.get("service_name", product_id), kwargs.get("api_key", "")
            )
        raise DeploymentError(f"Unsupported deployment target: {target}")

    # ------------------------------------------------------------------ #
    # Status and history
    # ------------------------------------------------------------------ #
    def get_deployment(self, deployment_id: str) -> DeploymentRecord | None:
        """Get a single deployment record."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM deployments WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
        if not row:
            return None
        return DeploymentRecord(
            deployment_id=row["deployment_id"],
            product_id=row["product_id"],
            target=row["target"],
            status=row["status"],
            url=row["url"],
            logs=row["logs"],
            cost=row["cost"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )

    def list_deployments(self, product_id: str | None = None) -> list[DeploymentRecord]:
        """List deployments, optionally filtered by product."""
        with self._conn() as conn:
            if product_id:
                rows = conn.execute(
                    "SELECT * FROM deployments WHERE product_id = ? ORDER BY timestamp DESC",
                    (product_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM deployments ORDER BY timestamp DESC"
                ).fetchall()
        return [
            DeploymentRecord(
                deployment_id=r["deployment_id"],
                product_id=r["product_id"],
                target=r["target"],
                status=r["status"],
                url=r["url"],
                logs=r["logs"],
                cost=r["cost"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
            )
            for r in rows
        ]

    def prepare_source_dir(
        self,
        project_name: str,
        source_dir: Path,
        python_version: str = "3.12",
        entrypoint: str = "main:app",
        port: int = 8000,
    ) -> list[Any]:
        """Generate CI/CD artifacts into the source directory and return artifacts."""
        gen = CICDGenerator(output_dir=source_dir)
        artifacts = gen.generate_full_pipeline(
            project_name=project_name,
            python_version=python_version,
            entrypoint=entrypoint,
            port=port,
            deploy_targets=["fly.io", "railway", "render"],
        )
        logger.info("Prepared source dir %s with %d CI/CD artifacts", source_dir, len(artifacts))
        return artifacts
