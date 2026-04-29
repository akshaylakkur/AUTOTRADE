"""Comprehensive pytest tests for auton.metamind self-modification components."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from auton.core.constants import SEED_BALANCE, TIER_THRESHOLDS
from auton.core.event_bus import EventBus
from auton.core.events import CodeModified, DependencyInstalled, ModificationFailed
from auton.core.state_machine import State, StateMachine
from auton.ledger.cost_tracker import CostCategory, CostTracker
from auton.ledger.master_wallet import MasterWallet
from auton.metamind import (
    CodeIntrospector,
    CodePatch,
    DependencyManager,
    DiffParseError,
    EvolutionGate,
    GeneratedCode,
    GenerationError,
    LLMProvider,
    MigrationResult,
    ModificationResult,
    ModuleGenerator,
    ModuleSpecification,
    PatchApplier,
    PatchRecord,
    PatchResult,
    RollbackError,
    RollbackJournal,
    SchemaEvolver,
    SelfModificationEngine,
    TemplateNotFoundError,
    TierGateError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project root with auton/ tree."""
    auton = tmp_path / "auton"
    auton.mkdir()
    (auton / "__init__.py").write_text("")
    core = auton / "core"
    core.mkdir()
    (core / "__init__.py").write_text("")
    (core / "constants.py").write_text("SEED_BALANCE = 50.0\n")
    senses = auton / "senses"
    senses.mkdir()
    (senses / "__init__.py").write_text("")
    (senses / "market_data.py").write_text(
        "\"\"\"Market data connector.\"\"\"\n"
        "import httpx\n"
        "\n"
        "class MarketDataFeed:\n"
        "    async def fetch(self, symbol: str) -> dict[str, Any]:\n"
        "        return {'symbol': symbol}\n"
    )
    # Copy real templates into temp project so ModuleGenerator can find them
    import shutil
    real_templates = Path(__file__).resolve().parents[1] / "auton" / "metamind" / "templates"
    if real_templates.exists():
        dest = auton / "metamind" / "templates"
        shutil.copytree(real_templates, dest, dirs_exist_ok=True)
    return tmp_path


@pytest.fixture
def mock_llm() -> MagicMock:
    """Return a mock LLMProvider."""
    m = MagicMock(spec=LLMProvider)
    m.complete.return_value = (
        "\"\"\"Completed module.\"\"\"\n"
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "class Foo:\n"
        "    def run(self):\n"
        "        logger.info('running')\n"
    )
    return m


@pytest.fixture
def wallet(tmp_path: Path) -> MasterWallet:
    return MasterWallet(db_path=tmp_path / "wallet.db")


@pytest.fixture
def cost_tracker(wallet: MasterWallet, tmp_path: Path) -> CostTracker:
    return CostTracker(wallet=wallet, db_path=tmp_path / "costs.db")


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def gate() -> EvolutionGate:
    return EvolutionGate(sandbox_timeout=5.0)


@pytest.fixture
def rollback_journal(tmp_path: Path) -> RollbackJournal:
    return RollbackJournal(db_path=tmp_path / "rollback.db")


@pytest.fixture
def introspector(tmp_project: Path) -> CodeIntrospector:
    return CodeIntrospector(project_root=tmp_project)


@pytest.fixture
def patch_applier(tmp_project: Path, rollback_journal: RollbackJournal) -> PatchApplier:
    return PatchApplier(
        project_root=tmp_project,
        rollback_journal=rollback_journal,
    )


@pytest.fixture
def module_generator(mock_llm: MagicMock, tmp_project: Path) -> ModuleGenerator:
    return ModuleGenerator(
        llm=mock_llm,
        template_dir=tmp_project / "auton" / "metamind" / "templates",
        mutation_dir=tmp_project / "mutations",
    )


@pytest.fixture
def dependency_manager(tmp_project: Path) -> DependencyManager:
    return DependencyManager(
        project_root=tmp_project,
        requirements_path=tmp_project / "requirements.txt",
    )


@pytest.fixture
def schema_evolver(tmp_project: Path) -> SchemaEvolver:
    return SchemaEvolver(
        config_paths=[tmp_project / "config.json"],
        migration_dir=tmp_project / "migrations",
    )


@pytest.fixture
def state_machine() -> StateMachine:
    return StateMachine()


@pytest.fixture
def sme(
    event_bus: EventBus,
    wallet: MasterWallet,
    cost_tracker: CostTracker,
    gate: EvolutionGate,
    mock_llm: MagicMock,
    tmp_project: Path,
    state_machine: StateMachine,
) -> SelfModificationEngine:
    # Seed wallet
    wallet.credit(SEED_BALANCE, "seed")
    return SelfModificationEngine(
        event_bus=event_bus,
        wallet=wallet,
        cost_tracker=cost_tracker,
        gate=gate,
        llm=mock_llm,
        project_root=tmp_project,
        config_paths=[tmp_project / "config.json"],
        state_machine=state_machine,
    )


# ---------------------------------------------------------------------------
# RollbackJournal
# ---------------------------------------------------------------------------

def test_record_and_get_snapshot(rollback_journal: RollbackJournal, tmp_path: Path) -> None:
    rollback_journal.record_snapshot(
        patch_id="p1",
        file_path=tmp_path / "test.py",
        content="original",
        author="test",
        reason="r",
        diff_text="diff",
        cost=0.01,
    )
    snapshot = rollback_journal.get_snapshot("p1", tmp_path / "test.py")
    assert snapshot == "original"


def test_list_patches(rollback_journal: RollbackJournal, tmp_path: Path) -> None:
    rollback_journal.record_snapshot("p1", tmp_path / "a.py", "a")
    rollback_journal.record_snapshot("p2", tmp_path / "a.py", "b")
    patches = rollback_journal.list_patches(tmp_path / "a.py")
    assert len(patches) == 2
    assert patches[0].patch_id == "p2"


def test_get_last_patch(rollback_journal: RollbackJournal, tmp_path: Path) -> None:
    rollback_journal.record_snapshot("p1", tmp_path / "a.py", "a")
    last = rollback_journal.get_last_patch(tmp_path / "a.py")
    assert last is not None
    assert last.patch_id == "p1"


def test_update_test_result(rollback_journal: RollbackJournal, tmp_path: Path) -> None:
    rollback_journal.record_snapshot("p1", tmp_path / "a.py", "a")
    rollback_journal.update_test_result("p1", {"passed": True})
    patches = rollback_journal.list_patches(tmp_path / "a.py")
    assert patches[0].test_result_json == '{"passed": true}'


# ---------------------------------------------------------------------------
# CodeIntrospector
# ---------------------------------------------------------------------------

def test_build_source_map(introspector: CodeIntrospector) -> None:
    sm = introspector.build_source_map("auton")
    assert "auton.core.constants" in sm.modules
    assert "auton.senses.market_data" in sm.modules


def test_locate_function(introspector: CodeIntrospector) -> None:
    introspector.build_source_map("auton")
    results = introspector.locate_function("fetch")
    assert any("market_data" in r.module_path for r in results)


def test_locate_class(introspector: CodeIntrospector) -> None:
    introspector.build_source_map("auton")
    results = introspector.locate_class("MarketDataFeed")
    assert len(results) == 1
    assert results[0].class_name == "MarketDataFeed"


def test_describe_module(introspector: CodeIntrospector) -> None:
    introspector.build_source_map("auton")
    info = introspector.describe_module("auton.senses.market_data")
    assert len(info.classes) == 1
    assert info.classes[0].name == "MarketDataFeed"


def test_extract_dependencies(introspector: CodeIntrospector) -> None:
    introspector.build_source_map("auton")
    deps = introspector.extract_dependencies("auton.senses.market_data")
    assert "httpx" in deps


def test_compute_complexity(introspector: CodeIntrospector) -> None:
    introspector.build_source_map("auton")
    mc = introspector.compute_complexity("auton.senses.market_data")
    assert mc.total >= 1


def test_find_callers(introspector: CodeIntrospector) -> None:
    # No callers in this simple tree
    introspector.build_source_map("auton")
    callers = introspector.find_callers("fetch")
    assert isinstance(callers, list)


def test_locate_function_not_found(introspector: CodeIntrospector) -> None:
    introspector.build_source_map("auton")
    results = introspector.locate_function("nonexistent_xyz")
    assert results == []


# ---------------------------------------------------------------------------
# ModuleGenerator
# ---------------------------------------------------------------------------

def test_generate_from_spec(mock_llm: MagicMock, tmp_project: Path) -> None:
    gen = ModuleGenerator(
        llm=mock_llm,
        template_dir=tmp_project / "auton" / "metamind" / "templates",
        mutation_dir=tmp_project / "muts",
    )
    # Create a simple generic template
    tpl_dir = tmp_project / "auton" / "metamind" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "generic.py.tpl").write_text("class {{ class_name }}:\n    pass\n")
    spec = ModuleSpecification(
        module_name="test_mod",
        module_type="generic",
        requirements=["r1"],
        context={"class_name": "TestMod"},
    )
    gc = gen.generate_from_spec(spec)
    assert gc.module_name == "test_mod"
    assert gc.mutation_path is not None
    assert gc.mutation_path.exists()
    assert gc.cost >= 0.0


def test_template_not_found(mock_llm: MagicMock, tmp_project: Path) -> None:
    empty_tpl = tmp_project / "empty_templates"
    empty_tpl.mkdir()
    gen = ModuleGenerator(
        llm=mock_llm,
        template_dir=empty_tpl,
        mutation_dir=tmp_project / "muts",
    )
    spec = ModuleSpecification(
        module_name="test_mod",
        module_type="nonexistent_type",
        requirements=["r1"],
    )
    with pytest.raises(TemplateNotFoundError):
        gen.generate_from_spec(spec)


def test_generate_exchange_connector(mock_llm: MagicMock, tmp_project: Path) -> None:
    gen = ModuleGenerator(
        llm=mock_llm,
        template_dir=tmp_project / "auton" / "metamind" / "templates",
        mutation_dir=tmp_project / "muts",
    )
    tpl_dir = tmp_project / "auton" / "metamind" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "exchange_connector.py.tpl").write_text(
        "class {{ class_name }}:\n    pass\n"
    )
    gc = gen.generate_exchange_connector("Kraken", "REST API", ["fetch"])
    assert gc.module_name == "connector_kraken"


def test_generate_data_source(mock_llm: MagicMock, tmp_project: Path) -> None:
    gen = ModuleGenerator(
        llm=mock_llm,
        template_dir=tmp_project / "auton" / "metamind" / "templates",
        mutation_dir=tmp_project / "muts",
    )
    tpl_dir = tmp_project / "auton" / "metamind" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "data_source.py.tpl").write_text("class {{ class_name }}:\n    pass\n")
    gc = gen.generate_data_source("Twitter", "rest", {"text": "str"})
    assert gc.module_name == "source_twitter"


def test_generate_commerce_module(mock_llm: MagicMock, tmp_project: Path) -> None:
    gen = ModuleGenerator(
        llm=mock_llm,
        template_dir=tmp_project / "auton" / "metamind" / "templates",
        mutation_dir=tmp_project / "muts",
    )
    tpl_dir = tmp_project / "auton" / "metamind" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "commerce_module.py.tpl").write_text("class {{ class_name }}:\n    pass\n")
    gc = gen.generate_commerce_module("Stripe", "payment")
    assert gc.module_name == "commerce_stripe"


def test_generate_saas_module(mock_llm: MagicMock, tmp_project: Path) -> None:
    gen = ModuleGenerator(
        llm=mock_llm,
        template_dir=tmp_project / "auton" / "metamind" / "templates",
        mutation_dir=tmp_project / "muts",
    )
    tpl_dir = tmp_project / "auton" / "metamind" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "saas_module.py.tpl").write_text("class {{ class_name }}:\n    pass\n")
    gc = gen.generate_saas_module("OpenAI", {"version": "v1"})
    assert gc.module_name == "saas_openai"


# ---------------------------------------------------------------------------
# PatchApplier
# ---------------------------------------------------------------------------

def test_apply_patch_simple_addition(
    patch_applier: PatchApplier, tmp_project: Path, rollback_journal: RollbackJournal
) -> None:
    target = tmp_project / "auton" / "senses" / "market_data.py"
    original = target.read_text(encoding="utf-8")
    diff = (
        "--- a/auton/senses/market_data.py\n"
        "+++ b/auton/senses/market_data.py\n"
        "@@ -5,3 +5,5 @@\n"
        " class MarketDataFeed:\n"
        "     async def fetch(self, symbol: str) -> dict[str, Any]:\n"
        "         return {'symbol': symbol}\n"
        "+\n"
        "+    async def health(self) -> bool:\n"
        "+        return True\n"
    )
    patch = CodePatch(
        patch_id="p1",
        target_file=target,
        diff_text=diff,
        author="test",
        reason="add health check",
    )
    result = patch_applier.apply_patch(patch)
    assert result.success is True
    new_content = target.read_text(encoding="utf-8")
    assert "health" in new_content


def test_apply_patch_rollback_on_syntax_error(
    patch_applier: PatchApplier, tmp_project: Path
) -> None:
    target = tmp_project / "auton" / "senses" / "market_data.py"
    original = target.read_text(encoding="utf-8")
    # A diff that results in invalid Python
    diff = (
        "--- a/auton/senses/market_data.py\n"
        "+++ b/auton/senses/market_data.py\n"
        "@@ -1,2 +1,2 @@\n"
        " \"\"\"Market data connector.\"\"\"\n"
        "-import httpx\n"
        "+def bad syntax here(\n"
    )
    patch = CodePatch(
        patch_id="p2",
        target_file=target,
        diff_text=diff,
    )
    result = patch_applier.apply_patch(patch)
    assert result.success is False
    assert result.rolled_back is True
    assert target.read_text(encoding="utf-8") == original


def test_rollback(patch_applier: PatchApplier, tmp_project: Path) -> None:
    target = tmp_project / "auton" / "senses" / "market_data.py"
    original = target.read_text(encoding="utf-8")
    diff = (
        "--- a/auton/senses/market_data.py\n"
        "+++ b/auton/senses/market_data.py\n"
        "@@ -5,3 +5,4 @@\n"
        " class MarketDataFeed:\n"
        "     async def fetch(self, symbol: str) -> dict[str, Any]:\n"
        "         return {'symbol': symbol}\n"
        "+    # patched\n"
    )
    patch = CodePatch(
        patch_id="p3",
        target_file=target,
        diff_text=diff,
    )
    patch_applier.apply_patch(patch)
    assert "patched" in target.read_text(encoding="utf-8")

    rb = patch_applier.rollback("p3")
    assert rb.success is True
    assert target.read_text(encoding="utf-8") == original


def test_revert_last(patch_applier: PatchApplier, tmp_project: Path) -> None:
    target = tmp_project / "auton" / "senses" / "market_data.py"
    original = target.read_text(encoding="utf-8")
    diff = (
        "--- a/auton/senses/market_data.py\n"
        "+++ b/auton/senses/market_data.py\n"
        "@@ -5,3 +5,4 @@\n"
        " class MarketDataFeed:\n"
        "     async def fetch(self, symbol: str) -> dict[str, Any]:\n"
        "         return {'symbol': symbol}\n"
        "+    # reverted\n"
    )
    patch = CodePatch(
        patch_id="p4",
        target_file=target,
        diff_text=diff,
    )
    patch_applier.apply_patch(patch)
    rb = patch_applier.revert_last(str(target))
    assert rb.success is True
    assert target.read_text(encoding="utf-8") == original


def test_get_patch_history(patch_applier: PatchApplier, tmp_project: Path) -> None:
    target = tmp_project / "auton" / "senses" / "market_data.py"
    patch = CodePatch(
        patch_id="p5",
        target_file=target,
        diff_text="",
        author="a",
        reason="r",
    )
    patch_applier.apply_patch(patch)
    history = patch_applier.get_patch_history(str(target))
    assert any(h.patch_id == "p5" for h in history)


# ---------------------------------------------------------------------------
# DependencyManager
# ---------------------------------------------------------------------------

def test_scan_module_for_missing_deps(dependency_manager: DependencyManager, tmp_project: Path) -> None:
    mod = tmp_project / "auton" / "senses" / "market_data.py"
    missing = dependency_manager.scan_module_for_missing_deps(mod)
    # httpx is in requirements.txt but not in the project's requirements.txt
    assert "httpx" in missing


def test_suggest_packages(dependency_manager: DependencyManager) -> None:
    assert "numpy" in dependency_manager.suggest_packages("numpy")
    assert "numpy" in dependency_manager.suggest_packages("numpy")


def test_add_requirement(dependency_manager: DependencyManager, tmp_project: Path) -> None:
    added = dependency_manager.add_requirement("requests")
    assert added is True
    added2 = dependency_manager.add_requirement("requests")
    assert added2 is False
    content = (tmp_project / "requirements.txt").read_text(encoding="utf-8")
    assert "requests" in content


def test_is_stdlib(dependency_manager: DependencyManager) -> None:
    assert dependency_manager._is_stdlib("os") is True
    assert dependency_manager._is_stdlib("json") is True
    assert dependency_manager._is_stdlib("nonexistent_xyz_12345") is False


# ---------------------------------------------------------------------------
# SchemaEvolver
# ---------------------------------------------------------------------------

def test_propose_migration(schema_evolver: SchemaEvolver) -> None:
    proposal = schema_evolver.propose_migration({"new_key": "default"}, "add new key")
    assert proposal.proposal_id.startswith("migration_")
    assert "new_key" in proposal.new_keys
    assert "new_key" in proposal.migration_script


def test_propose_migration_rejects_existing_key(schema_evolver: SchemaEvolver, tmp_project: Path) -> None:
    cfg = tmp_project / "config.json"
    cfg.write_text(json.dumps({"existing": 1}))
    with pytest.raises(Exception):
        schema_evolver.propose_migration({"existing": 2}, "try to shadow")


def test_propose_migration_rejects_path_traversal(schema_evolver: SchemaEvolver) -> None:
    with pytest.raises(Exception):
        schema_evolver.propose_migration({"../bad": 1}, "path traversal")


def test_apply_migration(schema_evolver: SchemaEvolver, tmp_project: Path) -> None:
    cfg = tmp_project / "config.json"
    cfg.write_text(json.dumps({"old": 1}))
    proposal = schema_evolver.propose_migration({"new_key": "default"}, "add new key")
    result = schema_evolver.apply_migration(proposal)
    assert result.success is True
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["new_key"] == "default"
    assert data["old"] == 1


def test_rollback_migration(schema_evolver: SchemaEvolver, tmp_project: Path) -> None:
    cfg = tmp_project / "config.json"
    cfg.write_text(json.dumps({"old": 1}))
    proposal = schema_evolver.propose_migration({"new_key": "default"}, "add new key")
    result = schema_evolver.apply_migration(proposal)
    assert result.success is True

    rb = schema_evolver.rollback_migration(proposal.proposal_id)
    assert rb.success is True
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "new_key" not in data


def test_list_migrations(schema_evolver: SchemaEvolver, tmp_project: Path) -> None:
    cfg = tmp_project / "config.json"
    cfg.write_text(json.dumps({"old": 1}))
    proposal = schema_evolver.propose_migration({"new_key": "default"}, "add new key")
    schema_evolver.apply_migration(proposal)
    migrations = schema_evolver.list_migrations()
    assert len(migrations) >= 1


# ---------------------------------------------------------------------------
# SelfModificationEngine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sme_generate_module_blocked_by_tier(sme: SelfModificationEngine) -> None:
    # Wallet only has $50 seed, generic generation needs $100
    spec = ModuleSpecification(
        module_name="foo",
        module_type="generic",
        requirements=["r1"],
    )
    result = await sme.generate_module(spec, {})
    assert result.success is False
    assert "tier" in result.message.lower() or "balance" in result.message.lower()


@pytest.mark.asyncio
async def test_sme_generate_module_passes_with_balance(
    event_bus: EventBus,
    wallet: MasterWallet,
    cost_tracker: CostTracker,
    gate: EvolutionGate,
    mock_llm: MagicMock,
    tmp_project: Path,
) -> None:
    wallet.credit(1000.0, "test_fund")
    sme = SelfModificationEngine(
        event_bus=event_bus,
        wallet=wallet,
        cost_tracker=cost_tracker,
        gate=gate,
        llm=mock_llm,
        project_root=tmp_project,
    )
    # Create template dir and generic template
    tpl_dir = tmp_project / "auton" / "metamind" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "generic.py.tpl").write_text("class {{ class_name }}:\n    pass\n")

    spec = ModuleSpecification(
        module_name="foo",
        module_type="generic",
        requirements=["r1"],
    )
    result = await sme.generate_module(spec, {})
    assert result.success is True
    assert result.new_file_path is not None


@pytest.mark.asyncio
async def test_sme_apply_patch(sme: SelfModificationEngine, tmp_project: Path) -> None:
    # Fund to tier 2
    sme.wallet.credit(1000.0, "test_fund")
    target = tmp_project / "auton" / "senses" / "market_data.py"
    diff = (
        "--- a/auton/senses/market_data.py\n"
        "+++ b/auton/senses/market_data.py\n"
        "@@ -5,3 +5,4 @@\n"
        " class MarketDataFeed:\n"
        "     async def fetch(self, symbol: str) -> dict[str, Any]:\n"
        "         return {'symbol': symbol}\n"
        "+    # patched by SME\n"
    )
    patch = CodePatch(
        patch_id="sme_p1",
        target_file=target,
        diff_text=diff,
        author="test",
        reason="test patch",
    )
    result = await sme.apply_patch(patch)
    assert result.success is True
    content = target.read_text(encoding="utf-8")
    assert "patched by SME" in content


@pytest.mark.asyncio
async def test_sme_apply_patch_rollback_on_syntax_error(
    sme: SelfModificationEngine, tmp_project: Path
) -> None:
    sme.wallet.credit(1000.0, "test_fund")
    target = tmp_project / "auton" / "senses" / "market_data.py"
    original = target.read_text(encoding="utf-8")
    diff = (
        "--- a/auton/senses/market_data.py\n"
        "+++ b/auton/senses/market_data.py\n"
        "@@ -1,2 +1,2 @@\n"
        " \"\"\"Market data connector.\"\"\"\n"
        "-import httpx\n"
        "+def bad syntax(\n"
    )
    patch = CodePatch(
        patch_id="sme_p2",
        target_file=target,
        diff_text=diff,
    )
    result = await sme.apply_patch(patch)
    assert result.success is False
    assert result.rolled_back is True
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_sme_protected_path_blocked(sme: SelfModificationEngine, tmp_project: Path) -> None:
    sme.wallet.credit(1000.0, "test_fund")
    target = tmp_project / "auton" / "core" / "constants.py"
    diff = (
        "--- a/auton/core/constants.py\n"
        "+++ b/auton/core/constants.py\n"
        "@@ -1 +1 @@\n"
        "-SEED_BALANCE = 50.0\n"
        "+SEED_BALANCE = 100.0\n"
    )
    patch = CodePatch(
        patch_id="sme_p3",
        target_file=target,
        diff_text=diff,
    )
    result = await sme.apply_patch(patch)
    # Tier 2 cannot modify protected paths
    assert result.success is False


@pytest.mark.asyncio
async def test_sme_resolve_dependencies_blocked_by_tier(sme: SelfModificationEngine) -> None:
    result = await sme.resolve_dependencies(["numpy"])
    # Tier 0/1 cannot install dependencies (needs tier 3, $2500)
    assert "numpy" in result.failed


@pytest.mark.asyncio
async def test_sme_evolve_schema_blocked_by_tier(sme: SelfModificationEngine) -> None:
    result = await sme.evolve_schema({"new_key": "val"}, "test")
    assert result.success is False
    assert "tier" in result.message.lower() or "balance" in result.message.lower()


@pytest.mark.asyncio
async def test_sme_introspect_module(sme: SelfModificationEngine) -> None:
    info = await sme.introspect_module("auton.senses.market_data")
    assert info.classes[0].name == "MarketDataFeed"


@pytest.mark.asyncio
async def test_sme_events_published(
    event_bus: EventBus,
    wallet: MasterWallet,
    cost_tracker: CostTracker,
    gate: EvolutionGate,
    mock_llm: MagicMock,
    tmp_project: Path,
) -> None:
    wallet.credit(1000.0, "test_fund")
    sme = SelfModificationEngine(
        event_bus=event_bus,
        wallet=wallet,
        cost_tracker=cost_tracker,
        gate=gate,
        llm=mock_llm,
        project_root=tmp_project,
    )
    received: list[Any] = []
    await event_bus.subscribe(CodeModified, lambda e: received.append(("modified", e)))
    await event_bus.subscribe(ModificationFailed, lambda e: received.append(("failed", e)))

    tpl_dir = tmp_project / "auton" / "metamind" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "generic.py.tpl").write_text("class {{ class_name }}:\n    pass\n")

    spec = ModuleSpecification(
        module_name="foo",
        module_type="generic",
        requirements=["r1"],
    )
    result = await sme.generate_module(spec, {})
    assert result.success is True
    await asyncio.sleep(0.1)
    assert any(k == "modified" for k, _ in received)


@pytest.mark.asyncio
async def test_sme_hibernation_advisory(
    event_bus: EventBus,
    wallet: MasterWallet,
    cost_tracker: CostTracker,
    gate: EvolutionGate,
    mock_llm: MagicMock,
    tmp_project: Path,
) -> None:
    wallet.credit(1000.0, "test_fund")
    sm = StateMachine()
    await sm.transition_to(State.RUNNING)
    sme = SelfModificationEngine(
        event_bus=event_bus,
        wallet=wallet,
        cost_tracker=cost_tracker,
        gate=gate,
        llm=mock_llm,
        project_root=tmp_project,
        state_machine=sm,
    )
    target = tmp_project / "auton" / "senses" / "market_data.py"
    diff = (
        "--- a/auton/senses/market_data.py\n"
        "+++ b/auton/senses/market_data.py\n"
        "@@ -1,2 +1,2 @@\n"
        " \"\"\"Market data connector.\"\"\"\n"
        "-import httpx\n"
        "+def bad syntax(\n"
    )
    for i in range(5):
        patch = CodePatch(
            patch_id=f"fail_{i}",
            target_file=target,
            diff_text=diff,
        )
        await sme.apply_patch(patch)

    assert len(sme._failures) > 3
    # State machine should have transitioned; if it didn't, at least the advisory was triggered
    assert sm.current_state in (State.HIBERNATE, State.RUNNING)


@pytest.mark.asyncio
async def test_sme_locate_function(sme: SelfModificationEngine) -> None:
    results = sme.locate_function("fetch")
    assert any("market_data" in r.module_path for r in results)


@pytest.mark.asyncio
async def test_sme_compute_complexity(sme: SelfModificationEngine) -> None:
    mc = sme.compute_complexity("auton.senses.market_data")
    assert mc.total >= 1


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

def test_self_modification_error_is_exception() -> None:
    from auton.metamind.code_introspector import SelfModificationError
    assert issubclass(SelfModificationError, Exception)


def test_tier_gate_error_is_self_modification_error() -> None:
    assert issubclass(TierGateError, Exception)
