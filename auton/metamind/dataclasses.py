"""Data classes for the metamind self-modification system."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class SafetyRating(Enum):
    """Safety rating for evolved code."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class DecisionType(Enum):
    """Types of journal decisions."""

    TRADE = "trade"
    ADAPTATION = "adaptation"
    DATA_SOURCE = "data_source"
    COMPUTE = "compute"


@dataclass(frozen=True)
class SourceMap:
    """Structured representation of the codebase."""

    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    entry_points: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "modules": {k: v.to_dict() for k, v in self.modules.items()},
            "dependencies": dict(self.dependencies),
            "entry_points": list(self.entry_points),
        }


@dataclass(frozen=True)
class ModuleInfo:
    """Information about a parsed module."""

    path: Path
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    complexity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "classes": [c.to_dict() for c in self.classes],
            "functions": [f.to_dict() for f in self.functions],
            "imports": list(self.imports),
            "todos": list(self.todos),
            "complexity": self.complexity,
        }


@dataclass(frozen=True)
class ClassInfo:
    """Information about a parsed class."""

    name: str
    methods: list[FunctionInfo] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    line_number: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "methods": [m.to_dict() for m in self.methods],
            "bases": list(self.bases),
            "line_number": self.line_number,
        }


@dataclass(frozen=True)
class FunctionInfo:
    """Information about a parsed function."""

    name: str
    line_number: int = 0
    complexity: int = 0
    docstring: str | None = None
    calls: list[str] = field(default_factory=list)
    is_async: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "line_number": self.line_number,
            "complexity": self.complexity,
            "docstring": self.docstring,
            "calls": list(self.calls),
            "is_async": self.is_async,
        }


@dataclass(frozen=True)
class GeneratedCode:
    """Represents a piece of generated code."""

    module_name: str
    source: str
    requirements: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    cost: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    mutation_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_name": self.module_name,
            "source": self.source,
            "requirements": list(self.requirements),
            "context": dict(self.context),
            "cost": self.cost,
            "timestamp": self.timestamp.isoformat(),
            "mutation_path": str(self.mutation_path) if self.mutation_path else None,
        }


@dataclass(frozen=True)
class EvolutionResult:
    """Result of the evolution gate validation."""

    passed: bool
    safety_score: float
    promoted: bool
    syntax_valid: bool = False
    tests_passed: bool = False
    safety_rating: SafetyRating = SafetyRating.FAIL
    message: str = ""
    source_path: Path | None = None
    target_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "safety_score": self.safety_score,
            "promoted": self.promoted,
            "syntax_valid": self.syntax_valid,
            "tests_passed": self.tests_passed,
            "safety_rating": self.safety_rating.value,
            "message": self.message,
            "source_path": str(self.source_path) if self.source_path else None,
            "target_path": str(self.target_path) if self.target_path else None,
        }


@dataclass(frozen=True)
class JournalEntry:
    """Immutable journal entry."""

    id: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decision_type: DecisionType = DecisionType.ADAPTATION
    reasoning: str = ""
    outcome: str = ""
    pnl: float = 0.0
    cost: float = 0.0
    metadata_json: str = "{}"

    def metadata(self) -> dict[str, Any]:
        try:
            return json.loads(self.metadata_json)
        except json.JSONDecodeError:
            return {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "decision_type": self.decision_type.value,
            "reasoning": self.reasoning,
            "outcome": self.outcome,
            "pnl": self.pnl,
            "cost": self.cost,
            "metadata_json": self.metadata_json,
        }


@dataclass(frozen=True)
class AdaptationProposal:
    """Proposal for a system adaptation."""

    module_name: str
    reasoning: str
    expected_benefit: str
    estimated_cost: float = 0.0
    target_metrics: dict[str, float] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_name": self.module_name,
            "reasoning": self.reasoning,
            "expected_benefit": self.expected_benefit,
            "estimated_cost": self.estimated_cost,
            "target_metrics": dict(self.target_metrics),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True)
class SystemMetrics:
    """System performance metrics."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    latency_p99_ms: float = 0.0
    error_rate: float = 0.0
    throughput_qps: float = 0.0
    pnl_1h: float = 0.0
    pnl_24h: float = 0.0
    cost_1h: float = 0.0
    cost_24h: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
            "latency_p99_ms": self.latency_p99_ms,
            "error_rate": self.error_rate,
            "throughput_qps": self.throughput_qps,
            "pnl_1h": self.pnl_1h,
            "pnl_24h": self.pnl_24h,
            "cost_1h": self.cost_1h,
            "cost_24h": self.cost_24h,
        }
