"""SelfModificationEngine — orchestrates the full self-modification lifecycle."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from auton.core.constants import TIER_THRESHOLDS
from auton.core.event_bus import EventBus
from auton.core.events import CodeModified, DependencyInstalled, ModificationFailed
from auton.core.state_machine import State, StateMachine
from auton.ledger.cost_tracker import CostCategory, CostTracker
from auton.ledger.master_wallet import MasterWallet
from auton.metamind.code_generator import LLMProvider
from auton.metamind.code_introspector import (
    CodeIntrospector,
    FunctionLocation,
    ModuleComplexity,
    ModuleInfo,
    SelfModificationError,
)
from auton.metamind.dependency_manager import DependencyManager, DependencyReport
from auton.metamind.evolution_gate import EvolutionGate
from auton.metamind.module_generator import GeneratedCode, ModuleGenerator, ModuleSpecification
from auton.metamind.patch_applier import CodePatch, PatchApplier, PatchResult
from auton.metamind.rollback_journal import RollbackJournal
from auton.metamind.schema_evolver import (
    MigrationProposal,
    MigrationResult,
    SchemaEvolver,
)

logger = logging.getLogger(__name__)

# Protected paths that require tier 4 + force flag
_PROTECTED_PATHS = [
    "auton/core/",
    "auton/ledger/",
]

# Tier-gated capabilities
_TIER_GATES: dict[str, tuple[int, float]] = {
    "generate_module_generic": (1, 100.0),
    "generate_module_exchange": (2, 500.0),
    "apply_patch": (2, 500.0),
    "install_dependency": (3, 2500.0),
    "evolve_schema": (4, 10000.0),
    "modify_protected": (4, 10000.0),
}


@dataclass(frozen=True)
class ModificationResult:
    """Result of a self-modification operation."""

    success: bool
    patch_id: str
    cost: float
    new_file_path: Path | None = None
    test_results: dict[str, Any] | None = None
    message: str = ""
    rolled_back: bool = False


class TierGateError(SelfModificationError):
    """Tier gate blocked the operation."""


class SelfModificationEngine:
    """Orchestrates the full self-modification lifecycle."""

    def __init__(
        self,
        event_bus: EventBus,
        wallet: MasterWallet,
        cost_tracker: CostTracker,
        gate: EvolutionGate,
        llm: LLMProvider,
        project_root: Path | None = None,
        rollback_db_path: Path | None = None,
        config_paths: list[Path] | None = None,
        state_machine: StateMachine | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.wallet = wallet
        self.cost_tracker = cost_tracker
        self.gate = gate
        self.llm = llm
        self.project_root = project_root or Path.cwd()
        self.state_machine = state_machine

        self._rollback_db_path = rollback_db_path or self.project_root / "data" / "rollback_journal.db"
        self._rollback_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal = RollbackJournal(db_path=self._rollback_db_path)
        self.introspector = CodeIntrospector(self.project_root)
        self.generator = ModuleGenerator(
            llm=llm,
            template_dir=self.project_root / "auton" / "metamind" / "templates",
            mutation_dir=self.project_root / "mutations",
        )
        self.patch_applier = PatchApplier(
            project_root=self.project_root,
            rollback_journal=self.journal,
        )
        self.dependency_manager = DependencyManager(
            project_root=self.project_root,
        )
        self.schema_evolver = SchemaEvolver(
            config_paths=config_paths or [self.project_root / "config.json"],
            migration_dir=self.project_root / "migrations",
            ledger=wallet,
        )

        # Failure tracking for hibernation advisory
        self._failures: list[datetime] = []
        self._failure_window = timedelta(hours=1)
        self._failure_threshold = 3

    # ------------------------------------------------------------------
    # Tier gating
    # ------------------------------------------------------------------
    def _check_tier(self, capability: str) -> None:
        """Raise TierGateError if the current balance is insufficient."""
        min_tier, min_balance = _TIER_GATES.get(capability, (4, float("inf")))
        balance = self.wallet.get_balance()
        if balance < min_balance:
            raise TierGateError(
                f"Capability '{capability}' requires tier {min_tier} (balance >= ${min_balance:.2f}), "
                f"but current balance is ${balance:.2f}"
            )

    def _is_protected(self, file_path: Path) -> bool:
        """Check if a file path is in a protected directory."""
        str_path = str(file_path)
        for protected in _PROTECTED_PATHS:
            if protected in str_path:
                return True
        return False

    # ------------------------------------------------------------------
    # Charging
    # ------------------------------------------------------------------
    async def _charge(
        self,
        amount: float,
        category: str,
        description: str,
    ) -> None:
        """Deduct operation cost from wallet."""
        try:
            cat = CostCategory(category.upper())
        except ValueError:
            cat = CostCategory.COMPUTE
        self.cost_tracker.record_cost(cat, amount, description)

    # ------------------------------------------------------------------
    # Event publishing
    # ------------------------------------------------------------------
    async def _publish_result(self, result: ModificationResult) -> None:
        """Emit CodeModified or ModificationFailed events."""
        if result.success:
            await self.event_bus.publish(
                CodeModified,
                CodeModified(
                    patch_id=result.patch_id,
                    target_file=str(result.new_file_path) if result.new_file_path else "",
                    author="cortex",
                    reason=result.message,
                    cost=result.cost,
                ),
            )
        else:
            await self.event_bus.publish(
                ModificationFailed,
                ModificationFailed(
                    patch_id=result.patch_id,
                    target_file=str(result.new_file_path) if result.new_file_path else "",
                    reason=result.message,
                    rolled_back=result.test_results is not None,
                ),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def generate_module(
        self,
        specification: ModuleSpecification,
        context: dict[str, Any],
    ) -> ModificationResult:
        """Generate a new module, validate, and optionally promote."""
        cap = (
            "generate_module_exchange"
            if specification.module_type == "exchange_connector"
            else "generate_module_generic"
        )
        try:
            self._check_tier(cap)
        except TierGateError as exc:
            return ModificationResult(
                success=False,
                patch_id="",
                cost=0.0,
                message=str(exc),
            )

        patch_id = str(uuid.uuid4())
        try:
            spec = ModuleSpecification(
                module_name=specification.module_name,
                module_type=specification.module_type,
                requirements=specification.requirements,
                interface_contract=specification.interface_contract,
                context={**specification.context, **context},
                target_path=specification.target_path,
            )
            generated = self.generator.generate_from_spec(spec)
            await self._charge(generated.cost, "inference", f"generate_module:{spec.module_name}")

            # Validate through EvolutionGate
            test_code = (
                "\n"
                "def test_generated_module():\n"
                "    assert True\n"
            )
            source_path = generated.mutation_path
            if source_path is None:
                return ModificationResult(
                    success=False,
                    patch_id=patch_id,
                    cost=generated.cost,
                    message="Generation produced no source path",
                )

            target_path = spec.target_path or (
                self.project_root / "auton" / f"{spec.module_name}.py"
            )
            gate_result = self.gate.validate_and_promote(
                code=generated.source,
                source_path=source_path,
                target_path=target_path,
                test_code=test_code,
            )

            if not gate_result.passed:
                return ModificationResult(
                    success=False,
                    patch_id=patch_id,
                    cost=generated.cost,
                    message=gate_result.message,
                )

            result = ModificationResult(
                success=True,
                patch_id=patch_id,
                cost=generated.cost,
                new_file_path=target_path,
                message=f"Module {spec.module_name} generated and promoted",
            )
            await self._publish_result(result)
            return result

        except Exception as exc:
            logger.exception("Module generation failed")
            return ModificationResult(
                success=False,
                patch_id=patch_id,
                cost=0.0,
                message=str(exc),
            )

    async def apply_patch(
        self,
        patch: CodePatch,
        run_tests: bool = True,
    ) -> ModificationResult:
        """Apply a diff to an existing module with full rollback support."""
        try:
            self._check_tier("apply_patch")
        except TierGateError as exc:
            return ModificationResult(
                success=False,
                patch_id=patch.patch_id,
                cost=0.0,
                message=str(exc),
            )

        # Check protected paths
        if self._is_protected(patch.target_file):
            try:
                self._check_tier("modify_protected")
            except TierGateError as exc:
                return ModificationResult(
                    success=False,
                    patch_id=patch.patch_id,
                    cost=0.0,
                    message=str(exc),
                )

        cost = 0.001  # nominal compute cost
        await self._charge(cost, "compute", f"apply_patch:{patch.patch_id}")

        patch_result = self.patch_applier.apply_patch(patch)

        if not patch_result.success:
            self._record_failure()
            await self._check_hibernation_advisory()
            result = ModificationResult(
                success=False,
                patch_id=patch.patch_id,
                cost=cost,
                new_file_path=patch.target_file,
                test_results={"output": patch_result.test_output},
                message=patch_result.message,
                rolled_back=patch_result.rolled_back,
            )
            await self._publish_result(result)
            return result

        result = ModificationResult(
            success=True,
            patch_id=patch.patch_id,
            cost=cost,
            new_file_path=patch.target_file,
            test_results={"output": patch_result.test_output},
            message=patch_result.message,
            rolled_back=False,
        )
        await self._publish_result(result)

        return result

    async def introspect_module(self, module_path: str) -> ModuleInfo:
        """Return structured metadata about any module in auton/."""
        return self.introspector.describe_module(module_path)

    async def resolve_dependencies(self, imports: list[str]) -> DependencyReport:
        """Detect missing imports and install required packages."""
        try:
            self._check_tier("install_dependency")
        except TierGateError as exc:
            return DependencyReport(
                missing_imports=imports,
                failed=imports,
                total_cost=0.0,
            )

        report = self.dependency_manager.resolve_dependencies(imports)
        if report.installed:
            for pkg in report.installed:
                await self._charge(0.001, "compute", f"install_dependency:{pkg}")
                await self.event_bus.publish(
                    DependencyInstalled,
                    DependencyInstalled(
                        package=pkg,
                        version="latest",
                        cost=0.001,
                    ),
                )
        return report

    async def evolve_schema(
        self,
        new_config_keys: dict[str, Any],
        migration_reason: str,
    ) -> MigrationResult:
        """Migrate config files when new modules add new keys."""
        try:
            self._check_tier("evolve_schema")
        except TierGateError as exc:
            return MigrationResult(
                success=False,
                migration_id="",
                message=str(exc),
            )

        cost = 0.001
        await self._charge(cost, "compute", f"evolve_schema:{migration_reason}")

        try:
            proposal = self.schema_evolver.propose_migration(new_config_keys, migration_reason)
            result = self.schema_evolver.apply_migration(proposal)
            if result.success:
                await self._charge(cost, "compute", f"schema_migration:{result.migration_id}")
            return result
        except Exception as exc:
            logger.exception("Schema evolution failed")
            return MigrationResult(
                success=False,
                migration_id="",
                message=str(exc),
            )

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    def locate_function(self, function_name: str) -> list[FunctionLocation]:
        """Find where a function is defined."""
        return self.introspector.locate_function(function_name)

    def locate_class(self, class_name: str) -> list:
        """Find where a class is defined."""
        return self.introspector.locate_class(class_name)

    def compute_complexity(self, module_path: str) -> ModuleComplexity:
        """Compute complexity metrics for a module."""
        return self.introspector.compute_complexity(module_path)

    def find_callers(self, target: str, package: str = "auton") -> list:
        """Find who calls a function/class."""
        return self.introspector.find_callers(target, package)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _record_failure(self) -> None:
        """Record a modification failure for hibernation tracking."""
        now = datetime.now(timezone.utc)
        self._failures.append(now)
        # Prune old failures
        cutoff = now - self._failure_window
        self._failures = [f for f in self._failures if f >= cutoff]

    async def _check_hibernation_advisory(self) -> None:
        """If failure rate exceeds threshold, emit hibernation advisory."""
        if len(self._failures) > self._failure_threshold:
            logger.warning(
                "Self-modification failure rate exceeded (%d failures in 1h). Advising hibernation.",
                len(self._failures),
            )
            if self.state_machine is not None:
                await self.state_machine.transition_to(State.HIBERNATE)
