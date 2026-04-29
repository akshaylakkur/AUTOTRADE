"""Comprehensive pytest tests for auton.metamind."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from auton.metamind import (
    AdaptionConfig,
    AdaptionEngine,
    AdaptationProposal,
    CICDGenerator,
    ClassInfo,
    CodeGenerator,
    CostEstimate,
    DecisionType,
    DeploymentManager,
    EvolutionGate,
    EvolutionResult,
    FunctionInfo,
    GeneratedCode,
    JournalEntry,
    LLMProvider,
    ListingRecord,
    MarketOpportunity,
    MarketplaceLister,
    ModuleGenerator,
    ModuleInfo,
    ProductCategory,
    ProductManager,
    ProductRecord,
    ProductStage,
    RevenueTracker,
    SafetyRating,
    SelfAnalyzer,
    SourceMap,
    StrategyJournal,
    SystemMetrics,
)
from auton.metamind.dataclasses import SafetyRating as _SafetyRating


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_source_dir(tmp_path: Path) -> Path:
    """Create a temporary Python package tree for SelfAnalyzer tests."""
    pkg = tmp_path / "auton"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    core = pkg / "core"
    core.mkdir()
    (core / "__init__.py").write_text("from auton.core.events import EventBus\n")
    (core / "events.py").write_text(
        "class EventBus:\n    pass\n"
    )
    security = pkg / "security"
    security.mkdir()
    (security / "__init__.py").write_text("# TODO: implement sandbox\n")
    (security / "sandbox.py").write_text(
        "import os\n"
        "def run(code):\n"
        "    if True:\n"
        "        while False:\n"
        "            pass\n"
        "    return eval(code)\n"
    )
    return tmp_path


@pytest.fixture
def mock_llm() -> MagicMock:
    """Return a mock LLMProvider."""
    m = MagicMock(spec=LLMProvider)
    m.complete.return_value = "# generated code\nclass Foo:\n    pass\n"
    return m


@pytest.fixture
def analyzer(temp_source_dir: Path) -> SelfAnalyzer:
    """Return a SelfAnalyzer primed on *temp_source_dir*."""
    sa = SelfAnalyzer()
    sa.analyze_source_tree(temp_source_dir)
    return sa


@pytest.fixture
def journal(tmp_path: Path) -> StrategyJournal:
    """Return a StrategyJournal backed by a temporary SQLite file."""
    return StrategyJournal(db_path=tmp_path / "journal.db")


@pytest.fixture
def gate() -> EvolutionGate:
    return EvolutionGate(sandbox_timeout=5.0)


@pytest.fixture
def generator(mock_llm: MagicMock, tmp_path: Path) -> CodeGenerator:
    return CodeGenerator(llm=mock_llm, mutation_dir=tmp_path / "mutations")


@pytest.fixture
def engine(
    analyzer: SelfAnalyzer,
    generator: CodeGenerator,
    gate: EvolutionGate,
    journal: StrategyJournal,
) -> AdaptionEngine:
    return AdaptionEngine(
        analyzer=analyzer,
        generator=generator,
        gate=gate,
        journal=journal,
        config=AdaptionConfig(cooldown_minutes=0.0, target_module="auton"),
    )


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------

def test_safety_rating_values() -> None:
    assert SafetyRating.PASS.value == "pass"
    assert SafetyRating.WARNING.value == "warning"
    assert SafetyRating.FAIL.value == "fail"


def test_decision_type_values() -> None:
    assert DecisionType.TRADE.value == "trade"
    assert DecisionType.ADAPTATION.value == "adaptation"


def test_module_info_to_dict() -> None:
    mi = ModuleInfo(path=Path("/foo.py"), complexity=3.0)
    d = mi.to_dict()
    assert d["path"] == "/foo.py"
    assert d["complexity"] == 3.0


def test_class_info_to_dict() -> None:
    ci = ClassInfo(name="Bar", line_number=10)
    assert ci.to_dict()["name"] == "Bar"
    assert ci.to_dict()["line_number"] == 10


def test_function_info_to_dict() -> None:
    fi = FunctionInfo(name="baz", complexity=5, is_async=True)
    assert fi.to_dict()["is_async"] is True
    assert fi.to_dict()["complexity"] == 5


def test_generated_code_to_dict() -> None:
    gc = GeneratedCode(module_name="m", source="x = 1", cost=0.01)
    d = gc.to_dict()
    assert d["module_name"] == "m"
    assert d["cost"] == 0.01
    assert d["mutation_path"] is None


def test_evolution_result_to_dict() -> None:
    er = EvolutionResult(
        passed=True, safety_score=0.95, promoted=True, safety_rating=SafetyRating.PASS
    )
    d = er.to_dict()
    assert d["safety_rating"] == "pass"
    assert d["promoted"] is True


def test_journal_entry_metadata() -> None:
    je = JournalEntry(metadata_json='{"key": "val"}')
    assert je.metadata() == {"key": "val"}


def test_journal_entry_bad_metadata() -> None:
    je = JournalEntry(metadata_json="not json")
    assert je.metadata() == {}


def test_adaptation_proposal_to_dict() -> None:
    ap = AdaptationProposal(
        module_name="mod",
        reasoning="r",
        expected_benefit="b",
        estimated_cost=1.0,
    )
    d = ap.to_dict()
    assert d["module_name"] == "mod"
    assert d["estimated_cost"] == 1.0


def test_system_metrics_to_dict() -> None:
    sm = SystemMetrics(cpu_percent=50.0)
    assert sm.to_dict()["cpu_percent"] == 50.0


# ---------------------------------------------------------------------------
# SelfAnalyzer
# ---------------------------------------------------------------------------

def test_analyze_source_tree(analyzer: SelfAnalyzer) -> None:
    sm = analyzer.get_source_map()
    assert isinstance(sm, SourceMap)
    assert "auton.core.events" in sm.modules
    assert "auton.security.sandbox" in sm.modules


def test_build_dependency_graph(analyzer: SelfAnalyzer) -> None:
    graph = analyzer.build_dependency_graph()
    assert "auton.core.events" in graph.get("auton.core.__init__", [])


def test_identify_bottlenecks(analyzer: SelfAnalyzer) -> None:
    b = analyzer.identify_bottlenecks(complexity_threshold=1)
    assert isinstance(b, list)
    # sandbox.py has a function with branching
    func_bottlenecks = [x for x in b if x["type"] == "function_complexity"]
    assert any(x["function"] == "run" for x in func_bottlenecks)


def test_find_missing_capabilities(analyzer: SelfAnalyzer) -> None:
    gaps = analyzer.find_missing_capabilities(["core", "ledger", "fantasy"])
    assert "fantasy" in gaps
    assert "core" not in gaps
    assert "ledger" in gaps


def test_get_source_map_entry_points(temp_source_dir: Path) -> None:
    main_file = temp_source_dir / "auton" / "main.py"
    main_file.write_text("if __name__ == '__main__':\n    pass\n")
    sa = SelfAnalyzer()
    sa.analyze_source_tree(temp_source_dir)
    sm = sa.get_source_map()
    assert "auton.main" in sm.entry_points


def test_get_source_map_entry_points_function(temp_source_dir: Path) -> None:
    main_file = temp_source_dir / "auton" / "run.py"
    main_file.write_text("def __main__():\n    pass\n")
    sa = SelfAnalyzer()
    sa.analyze_source_tree(temp_source_dir)
    sm = sa.get_source_map()
    assert "auton.run" in sm.entry_points


# ---------------------------------------------------------------------------
# CodeGenerator
# ---------------------------------------------------------------------------

def test_generate_module(mock_llm: MagicMock, tmp_path: Path) -> None:
    gen = CodeGenerator(llm=mock_llm, mutation_dir=tmp_path / "muts")
    gc = gen.generate_module("foo", requirements=["r1"], context={"k": "v"})
    assert gc.module_name == "foo"
    assert gc.mutation_path is not None
    assert gc.mutation_path.exists()
    assert gc.cost >= 0.0


def test_optimize_function(mock_llm: MagicMock, tmp_path: Path) -> None:
    gen = CodeGenerator(llm=mock_llm, mutation_dir=tmp_path / "muts")
    gc = gen.optimize_function("def f(): pass", "speed")
    assert gc.module_name == "optimized_function"
    assert "optimized" in gc.mutation_path.name


def test_generate_connector(mock_llm: MagicMock, tmp_path: Path) -> None:
    gen = CodeGenerator(llm=mock_llm, mutation_dir=tmp_path / "muts")
    gc = gen.generate_connector("Binance", "REST API v3")
    assert gc.module_name == "connector_binance"
    assert "connector_binance" in gc.mutation_path.name


def test_estimate_cost() -> None:
    cost = CodeGenerator._estimate_cost("a" * 400, "b" * 400)
    assert cost > 0.0


# ---------------------------------------------------------------------------
# EvolutionGate
# ---------------------------------------------------------------------------

def test_validate_syntax_pass(gate: EvolutionGate) -> None:
    assert gate.validate_syntax("x = 1") is True


def test_validate_syntax_fail(gate: EvolutionGate) -> None:
    assert gate.validate_syntax("def foo(") is False


def test_check_safety_clean(gate: EvolutionGate) -> None:
    rating, score, issues = gate.check_safety("x = 1")
    assert rating == SafetyRating.PASS
    assert score == 1.0
    assert issues == []


def test_check_safety_forbidden_import(gate: EvolutionGate) -> None:
    code = "import os\nos.system('ls')"
    rating, score, issues = gate.check_safety(code)
    assert rating == SafetyRating.FAIL
    assert any("os.system" in i for i in issues)


def test_check_safety_forbidden_call(gate: EvolutionGate) -> None:
    code = "subprocess.call(['ls'])"
    rating, score, issues = gate.check_safety(code)
    assert rating == SafetyRating.FAIL
    assert any("subprocess.call" in i for i in issues)


def test_run_sandbox_tests_pass(gate: EvolutionGate) -> None:
    code = "def add(a, b): return a + b"
    tests = "assert add(1, 2) == 3"
    assert gate.run_sandbox_tests(code, tests) is True


def test_run_sandbox_tests_fail(gate: EvolutionGate) -> None:
    code = "def add(a, b): return a + b"
    tests = "assert add(1, 2) == 4"
    assert gate.run_sandbox_tests(code, tests) is False


def test_promote_to_production(gate: EvolutionGate, tmp_path: Path) -> None:
    src = tmp_path / "src.py"
    src.write_text("# hello")
    dst = tmp_path / "dst.py"
    result = gate.promote_to_production(src, dst)
    assert result.promoted is True
    assert dst.read_text() == "# hello"


def test_validate_and_promote_full_pipeline(gate: EvolutionGate, tmp_path: Path) -> None:
    src = tmp_path / "mut.py"
    src.write_text("x = 1")
    dst = tmp_path / "prod.py"
    result = gate.validate_and_promote("x = 1", src, dst, test_code="assert x == 1")
    assert result.passed is True
    assert result.promoted is True


def test_validate_and_promote_syntax_fail(gate: EvolutionGate, tmp_path: Path) -> None:
    result = gate.validate_and_promote("def foo(", tmp_path / "a.py", tmp_path / "b.py")
    assert result.passed is False
    assert result.syntax_valid is False


# ---------------------------------------------------------------------------
# StrategyJournal
# ---------------------------------------------------------------------------

def test_log_and_retrieve(journal: StrategyJournal) -> None:
    entry = JournalEntry(
        reasoning="test", outcome="ok", decision_type=DecisionType.TRADE, pnl=10.0
    )
    row_id = journal.log_decision(entry)
    assert row_id is not None
    recent = journal.get_recent_entries(limit=10)
    assert len(recent) == 1
    assert recent[0].pnl == 10.0


def test_analyze_win_rate(journal: StrategyJournal) -> None:
    for i in range(5):
        journal.log_decision(
            JournalEntry(
                reasoning="r",
                outcome="win" if i % 2 == 0 else "loss",
                decision_type=DecisionType.TRADE,
                pnl=1.0 if i % 2 == 0 else -1.0,
                metadata_json='{"strategy_name":"s1"}',
            )
        )
    stats = journal.analyze_win_rate("s1")
    assert stats["total_trades"] == 5
    assert stats["win_rate"] == 0.6


def test_log_adaptation(journal: StrategyJournal) -> None:
    rid = journal.log_adaptation(
        reasoning="add feature",
        outcome="promoted",
        before_metrics={"latency": 100},
        after_metrics={"latency": 80},
        cost=0.5,
    )
    assert rid is not None
    entry = journal.get_recent_entries(limit=1)[0]
    assert entry.decision_type == DecisionType.ADAPTATION
    meta = entry.metadata()
    assert meta["before"]["latency"] == 100


# ---------------------------------------------------------------------------
# AdaptionEngine
# ---------------------------------------------------------------------------

def test_review_performance(engine: AdaptionEngine, journal: StrategyJournal) -> None:
    journal.log_decision(
        JournalEntry(
            reasoning="r", outcome="ok", decision_type=DecisionType.TRADE, pnl=5.0, cost=0.1
        )
    )
    summary = engine.review_performance()
    assert summary["total_pnl"] == 5.0
    assert summary["entry_count"] == 1


def test_review_performance_with_system_metrics(engine: AdaptionEngine) -> None:
    sm = SystemMetrics(cpu_percent=42.0)
    summary = engine.review_performance(system_metrics=sm)
    assert summary["system_metrics"]["cpu_percent"] == 42.0


def test_propose_adaptation_no_cooldown(engine: AdaptionEngine) -> None:
    # With cooldown=0 and empty history, should propose if gaps/bottlenecks exist.
    prop = engine.propose_adaptation()
    # temp_source_dir lacks ledger, so there are gaps
    assert prop is not None
    assert "ledger" in prop.reasoning or "bottleneck" in prop.reasoning


def test_propose_adaptation_cooldown_blocks(engine: AdaptionEngine) -> None:
    engine._last_adaptation = datetime.now(timezone.utc)
    engine.config.cooldown_minutes = 60.0
    prop = engine.propose_adaptation()
    assert prop is None


def test_propose_adaptation_roi_blocks(engine: AdaptionEngine, journal: StrategyJournal) -> None:
    # Simulate a failed prior adaptation by injecting history with bad net
    engine._history.append({"pnl": -10.0, "cost": 1.0})
    # Cooldown is 0, but ROI threshold is 1.0, so net -11 < 1 => blocked
    engine.config.min_roi_threshold = 1.0
    # Need to make journal show bad adaptations
    journal.log_decision(
        JournalEntry(
            reasoning="bad", outcome="fail", decision_type=DecisionType.ADAPTATION, pnl=-10, cost=1
        )
    )
    prop = engine.propose_adaptation()
    assert prop is None


@pytest.mark.asyncio
async def test_execute_adaptation_pipeline_no_proposal(engine: AdaptionEngine) -> None:
    # Force no proposal by setting cooldown
    engine._last_adaptation = datetime.now(timezone.utc)
    engine.config.cooldown_minutes = 60.0
    result = await engine.execute_adaptation_pipeline()
    assert result.passed is False
    assert "No adaptation proposal" in result.message


@pytest.mark.asyncio
async def test_get_adaptation_history(engine: AdaptionEngine) -> None:
    assert engine.get_adaptation_history() == []
    # Manually push a history record
    engine._history.append({"foo": "bar"})
    assert engine.get_adaptation_history() == [{"foo": "bar"}]


@pytest.mark.asyncio
async def test_emit_event(engine: AdaptionEngine, caplog: pytest.LogCaptureFixture) -> None:
    import logging
    logger = logging.getLogger("auton.metamind.adaption_engine")
    logger.setLevel(logging.INFO)
    await engine.emit_event("SelfModificationProposed", {"module": "x"})
    assert "SelfModificationProposed" in caplog.text


# ---------------------------------------------------------------------------
# Integration / edge cases
# ---------------------------------------------------------------------------

def test_self_analyzer_empty_dir(tmp_path: Path) -> None:
    sa = SelfAnalyzer()
    sa.analyze_source_tree(tmp_path)
    assert sa.get_source_map().modules == {}


def test_self_analyzer_syntax_error_skipped(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("def foo(")
    sa = SelfAnalyzer()
    sa.analyze_source_tree(tmp_path)
    assert "bad" not in sa.get_source_map().modules


def test_evolution_gate_timeout_handling(tmp_path: Path) -> None:
    gate = EvolutionGate(sandbox_timeout=0.01)
    code = "import time; time.sleep(10)"
    tests = "pass"
    assert gate.run_sandbox_tests(code, tests) is False


def test_strategy_journal_concurrent_writes(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    journal = StrategyJournal(db_path=db)
    for _ in range(100):
        journal.log_decision(
            JournalEntry(reasoning="r", outcome="ok", decision_type=DecisionType.TRADE)
        )
    assert len(journal.get_recent_entries(limit=200)) == 100


def test_generated_code_roundtrip(tmp_path: Path) -> None:
    gc = GeneratedCode(
        module_name="m",
        source="x = 1",
        mutation_path=tmp_path / "f.py",
    )
    d = gc.to_dict()
    assert d["mutation_path"] == str(tmp_path / "f.py")


def test_adaption_config_defaults() -> None:
    cfg = AdaptionConfig()
    assert cfg.cooldown_minutes == 60.0
    assert cfg.min_roi_threshold == 1.0


def test_adaption_engine_cooldown_elapsed(engine: AdaptionEngine) -> None:
    assert engine._cooldown_elapsed() is True
    engine._last_adaptation = datetime.now(timezone.utc)
    engine.config.cooldown_minutes = 60.0
    assert engine._cooldown_elapsed() is False


def test_adaption_engine_roi_proven_empty_history(engine: AdaptionEngine) -> None:
    assert engine._roi_proven() is True


# ---------------------------------------------------------------------------
# SaaS Product Factory — CodeGenerator web app generation
# ---------------------------------------------------------------------------

def test_generate_fastapi_app(mock_llm: MagicMock, tmp_path: Path) -> None:
    mock_llm.complete.return_value = (
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\nasync def health(): return {'status': 'ok'}"
    )
    gen = CodeGenerator(llm=mock_llm, mutation_dir=tmp_path / "muts")
    gc = gen.generate_fastapi_app(
        "TestAPI",
        endpoints=[{"path": "/items", "method": "GET"}],
        models=[{"name": "Item", "fields": {"name": "str"}}],
    )
    assert gc.module_name == "fastapi_testapi"
    assert "FastAPI" in gc.source
    assert gc.mutation_path is not None
    assert gc.mutation_path.exists()


def test_generate_react_frontend(mock_llm: MagicMock, tmp_path: Path) -> None:
    mock_llm.complete.return_value = "export default function App() { return <div>Hello</div>; }"
    gen = CodeGenerator(llm=mock_llm, mutation_dir=tmp_path / "muts")
    gc = gen.generate_react_frontend(
        "TestUI",
        pages=["Home", "Dashboard"],
        api_base_url="https://api.example.com",
    )
    assert gc.module_name == "react_testui"
    assert gc.mutation_path is not None
    assert gc.mutation_path.exists()


def test_generate_fullstack_app(mock_llm: MagicMock, tmp_path: Path) -> None:
    mock_llm.complete.side_effect = [
        "from fastapi import FastAPI\napp = FastAPI()",
        "export default function App() { return <div>Hello</div>; }",
    ]
    gen = CodeGenerator(llm=mock_llm, mutation_dir=tmp_path / "muts")
    result = gen.generate_fullstack_app(
        "TestApp",
        endpoints=[{"path": "/api", "method": "GET"}],
        pages=["Home"],
    )
    assert "backend" in result
    assert "frontend" in result
    assert result["backend"].module_name == "fastapi_testapp"
    assert result["frontend"].module_name == "react_testapp"


# ---------------------------------------------------------------------------
# SaaS Product Factory — ModuleGenerator web app generation
# ---------------------------------------------------------------------------

def test_generate_fastapi_module(mock_llm: MagicMock, tmp_path: Path) -> None:
    mock_llm.complete.return_value = "from fastapi import FastAPI\napp = FastAPI()\n"
    mg = ModuleGenerator(llm=mock_llm, mutation_dir=tmp_path / "muts")
    gc = mg.generate_fastapi_module(
        "TestAPI",
        endpoints=[{"path": "/items", "method": "GET"}],
    )
    assert gc.module_name == "api_testapi"
    assert gc.mutation_path is not None


def test_generate_react_module(mock_llm: MagicMock, tmp_path: Path) -> None:
    mock_llm.complete.return_value = "export default function App() { return <div>Hello</div>; }"
    mg = ModuleGenerator(llm=mock_llm, mutation_dir=tmp_path / "muts")
    gc = mg.generate_react_module(
        "TestUI",
        pages=["Home", "Dashboard"],
    )
    assert gc.module_name == "ui_testui"
    assert gc.mutation_path is not None


# ---------------------------------------------------------------------------
# SaaS Product Factory — ProductManager
# ---------------------------------------------------------------------------

@pytest.fixture
def product_manager(tmp_path: Path) -> ProductManager:
    return ProductManager(db_path=tmp_path / "products.db")


def test_register_and_score_opportunities(product_manager: ProductManager) -> None:
    opp = MarketOpportunity(
        category=ProductCategory.API_SERVICE,
        name="Crypto Price API",
        description="Real-time crypto prices",
        estimated_tam=50000.0,
        competition_level="low",
        trend_score=0.85,
    )
    rid = product_manager.register_opportunity(opp)
    assert rid > 0
    scored = product_manager.score_opportunities()
    assert len(scored) == 1
    assert scored[0][0].name == "Crypto Price API"
    assert scored[0][1] > 0


def test_estimate_cost(product_manager: ProductManager) -> None:
    cost = product_manager.estimate_cost(ProductCategory.API_SERVICE, complexity="medium")
    assert cost.llm_tokens > 0
    assert cost.total_estimated_cost > 0


def test_create_product(product_manager: ProductManager) -> None:
    cost = product_manager.estimate_cost(ProductCategory.MICROSAAS)
    prod = product_manager.create_product(
        product_id="prod_001",
        name="TinySaaS",
        category=ProductCategory.MICROSAAS,
        cost_estimate=cost,
        metadata={"author": "aeon"},
    )
    assert prod.product_id == "prod_001"
    assert prod.stage == ProductStage.IDEATION


def test_product_lifecycle(product_manager: ProductManager) -> None:
    cost = product_manager.estimate_cost(ProductCategory.WEB_APP)
    product_manager.create_product("prod_002", "WebApp", ProductCategory.WEB_APP, cost)
    assert product_manager.update_stage("prod_002", ProductStage.DEVELOPMENT) is True
    assert product_manager.update_stage("prod_002", ProductStage.DEPLOYED) is True
    assert product_manager.record_cost("prod_002", 5.0) is True
    assert product_manager.record_revenue("prod_002", 25.0) is True
    assert product_manager.set_deployed_url("prod_002", "https://example.com") is True
    assert product_manager.add_marketplace_url("prod_002", "stripe", "https://buy.stripe.com/test") is True
    assert product_manager.add_source_path("prod_002", "/tmp/src") is True

    prod = product_manager.get_product("prod_002")
    assert prod is not None
    assert prod.stage == ProductStage.DEPLOYED
    assert prod.actual_cost == 5.0
    assert prod.revenue == 25.0
    assert prod.deployed_url == "https://example.com"
    assert prod.marketplace_urls.get("stripe") == "https://buy.stripe.com/test"
    assert "/tmp/src" in prod.source_paths


def test_list_products(product_manager: ProductManager) -> None:
    cost = product_manager.estimate_cost(ProductCategory.TOOL)
    product_manager.create_product("prod_003", "Tool1", ProductCategory.TOOL, cost)
    product_manager.create_product("prod_004", "Tool2", ProductCategory.TOOL, cost)
    products = product_manager.list_products()
    assert len(products) == 2


def test_portfolio_summary(product_manager: ProductManager) -> None:
    cost = product_manager.estimate_cost(ProductCategory.CONTENT)
    product_manager.create_product("prod_005", "Content1", ProductCategory.CONTENT, cost)
    product_manager.record_revenue("prod_005", 100.0)
    summary = product_manager.portfolio_summary()
    assert summary["total_products"] == 1
    assert summary["total_revenue"] == 100.0
    assert summary["net_profit"] == 100.0


# ---------------------------------------------------------------------------
# SaaS Product Factory — CICDGenerator
# ---------------------------------------------------------------------------

def test_generate_github_actions(tmp_path: Path) -> None:
    gen = CICDGenerator(output_dir=tmp_path)
    artifact = gen.generate_github_actions("TestApp", deploy_target="fly.io")
    assert artifact.artifact_type == "github_action"
    assert artifact.file_path.exists()
    assert "fly.io" in artifact.content


def test_generate_dockerfile(tmp_path: Path) -> None:
    gen = CICDGenerator(output_dir=tmp_path)
    artifact = gen.generate_dockerfile("TestApp", entrypoint="main:app", port=8080)
    assert artifact.artifact_type == "dockerfile"
    assert artifact.file_path.exists()
    assert "8080" in artifact.content


def test_generate_docker_compose(tmp_path: Path) -> None:
    gen = CICDGenerator(output_dir=tmp_path)
    artifact = gen.generate_docker_compose("TestApp", include_postgres=True, include_redis=True)
    assert artifact.artifact_type == "compose"
    assert "postgres" in artifact.content
    assert "redis" in artifact.content


def test_generate_deploy_script(tmp_path: Path) -> None:
    gen = CICDGenerator(output_dir=tmp_path)
    artifact = gen.generate_deploy_script("TestApp", target="fly.io")
    assert artifact.artifact_type == "script"
    assert artifact.file_path.exists()
    assert "flyctl" in artifact.content


def test_generate_full_pipeline(tmp_path: Path) -> None:
    gen = CICDGenerator(output_dir=tmp_path)
    artifacts = gen.generate_full_pipeline("TestApp", deploy_targets=["fly.io"])
    assert len(artifacts) >= 3
    types = {a.artifact_type for a in artifacts}
    assert "github_action" in types
    assert "dockerfile" in types
    assert "compose" in types
    assert "script" in types


# ---------------------------------------------------------------------------
# SaaS Product Factory — DeploymentManager
# ---------------------------------------------------------------------------

@pytest.fixture
def deployment_manager(tmp_path: Path) -> DeploymentManager:
    return DeploymentManager(db_path=tmp_path / "deployments.db")


def test_prepare_source_dir(tmp_path: Path) -> None:
    dm = DeploymentManager()
    src = tmp_path / "source"
    src.mkdir()
    artifacts = dm.prepare_source_dir("TestApp", src)
    assert len(artifacts) >= 3
    assert (src / "Dockerfile").exists()


def test_deployment_record(deployment_manager: DeploymentManager) -> None:
    deployment_manager._record_deployment("dep_001", "prod_001", "fly.io", "deployed", url="https://app.fly.dev")
    dep = deployment_manager.get_deployment("dep_001")
    assert dep is not None
    assert dep.status == "deployed"
    assert dep.url == "https://app.fly.dev"


def test_list_deployments(deployment_manager: DeploymentManager) -> None:
    deployment_manager._record_deployment("dep_002", "prod_002", "fly.io", "pending")
    deployment_manager._record_deployment("dep_003", "prod_002", "railway", "deployed")
    deps = deployment_manager.list_deployments(product_id="prod_002")
    assert len(deps) == 2


# ---------------------------------------------------------------------------
# SaaS Product Factory — MarketplaceLister
# ---------------------------------------------------------------------------

@pytest.fixture
def marketplace_lister(tmp_path: Path) -> MarketplaceLister:
    return MarketplaceLister(db_path=tmp_path / "marketplace.db")


def test_update_sales(marketplace_lister: MarketplaceLister) -> None:
    marketplace_lister._record_listing(
        ListingRecord(
            listing_id="lst_001",
            product_id="prod_001",
            marketplace="stripe",
            listing_url="https://buy.stripe.com/test",
            status="live",
            price_cents=1000,
        )
    )
    assert marketplace_lister.update_sales("lst_001", sales_count=5, revenue=50.0) is True
    listing = marketplace_lister.get_listing("lst_001")
    assert listing is not None
    assert listing.sales_count == 5
    assert listing.revenue == 50.0


def test_list_listings(marketplace_lister: MarketplaceLister) -> None:
    marketplace_lister._record_listing(
        ListingRecord(
            listing_id="lst_002",
            product_id="prod_003",
            marketplace="gumroad",
            listing_url="https://gumroad.com/l/test",
            status="live",
        )
    )
    listings = marketplace_lister.list_listings(product_id="prod_003")
    assert len(listings) == 1
    assert listings[0].marketplace == "gumroad"


def test_total_revenue(marketplace_lister: MarketplaceLister) -> None:
    marketplace_lister._record_listing(
        ListingRecord(
            listing_id="lst_003",
            product_id="prod_004",
            marketplace="stripe",
            listing_url="https://buy.stripe.com/test",
            status="live",
            revenue=100.0,
        )
    )
    assert marketplace_lister.total_revenue("prod_004") == 100.0


# ---------------------------------------------------------------------------
# SaaS Product Factory — RevenueTracker
# ---------------------------------------------------------------------------

@pytest.fixture
def revenue_tracker(tmp_path: Path) -> RevenueTracker:
    return RevenueTracker(db_path=tmp_path / "revenue.db")


def test_record_sale(revenue_tracker: RevenueTracker) -> None:
    revenue_tracker.record_sale("evt_001", "prod_001", 49.99, customer_id="cust_001")
    metrics = revenue_tracker.get_product_metrics("prod_001")
    assert metrics.total_revenue == 49.99
    assert metrics.total_sales == 1


def test_record_refund(revenue_tracker: RevenueTracker) -> None:
    revenue_tracker.record_sale("evt_002", "prod_001", 49.99, customer_id="cust_001")
    revenue_tracker.record_refund("evt_003", "prod_001", 49.99, customer_id="cust_001")
    metrics = revenue_tracker.get_product_metrics("prod_001")
    # total_revenue tracks gross sales; refunds are tracked separately
    assert metrics.total_revenue == 49.99
    assert metrics.refunds == 49.99


def test_subscription_events(revenue_tracker: RevenueTracker) -> None:
    revenue_tracker.record_subscription_event(
        "evt_sub_001", "prod_002", "subscription_created", 0.0, customer_id="cust_002"
    )
    revenue_tracker.record_subscription_event(
        "evt_sub_002", "prod_002", "renewal", 29.99, customer_id="cust_002"
    )
    metrics = revenue_tracker.get_product_metrics("prod_002")
    assert metrics.active_subscriptions == 1
    assert metrics.mrr == 29.99


def test_stripe_webhook_sale(revenue_tracker: RevenueTracker) -> None:
    payload = {
        "id": "evt_stripe_001",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"aeon_product_id": "prod_003"},
                "customer": "cust_003",
                "currency": "usd",
                "amount_total": 4999,
            }
        },
    }
    event = revenue_tracker.handle_stripe_webhook(payload)
    assert event is not None
    assert event.product_id == "prod_003"
    assert event.amount == 49.99


def test_stripe_webhook_unhandled(revenue_tracker: RevenueTracker) -> None:
    payload = {
        "id": "evt_stripe_002",
        "type": "invoice.updated",
        "data": {"object": {"metadata": {}}},
    }
    event = revenue_tracker.handle_stripe_webhook(payload)
    assert event is None


def test_revenue_time_series(revenue_tracker: RevenueTracker) -> None:
    revenue_tracker.record_sale("evt_004", "prod_004", 10.0)
    revenue_tracker.record_sale("evt_005", "prod_004", 20.0)
    ts = revenue_tracker.get_revenue_time_series("prod_004", days=30)
    assert len(ts) == 1
    assert ts[0]["revenue"] == 30.0


def test_portfolio_revenue(revenue_tracker: RevenueTracker) -> None:
    revenue_tracker.record_sale("evt_006", "prod_005", 100.0)
    revenue_tracker.record_sale("evt_007", "prod_006", 200.0)
    assert revenue_tracker.total_portfolio_revenue() == 300.0


def test_customer_churn_rate(revenue_tracker: RevenueTracker) -> None:
    revenue_tracker.record_subscription_event(
        "evt_sub_003", "prod_007", "subscription_created", 0.0, customer_id="cust_004"
    )
    revenue_tracker.record_subscription_event(
        "evt_sub_004", "prod_007", "subscription_cancelled", 0.0, customer_id="cust_004"
    )
    churn = revenue_tracker.get_customer_churn_rate("prod_007", days=30)
    assert churn == 1.0
