"""Public API for auton.metamind — the self-modification and adaptation system."""

from auton.metamind.adaption_engine import AdaptionConfig, AdaptionEngine
from auton.metamind.code_generator import CodeGenerator, LLMProvider
from auton.metamind.dataclasses import (
    AdaptationProposal,
    ClassInfo,
    DecisionType,
    EvolutionResult,
    FunctionInfo,
    GeneratedCode,
    JournalEntry,
    ModuleInfo,
    SafetyRating,
    SourceMap,
    SystemMetrics,
)
from auton.metamind.evolution_gate import EvolutionGate
from auton.metamind.self_analyzer import SelfAnalyzer
from auton.metamind.strategy_journal import StrategyJournal

__all__ = [
    "AdaptionConfig",
    "AdaptionEngine",
    "AdaptationProposal",
    "ClassInfo",
    "CodeGenerator",
    "DecisionType",
    "EvolutionGate",
    "EvolutionResult",
    "FunctionInfo",
    "GeneratedCode",
    "JournalEntry",
    "LLMProvider",
    "ModuleInfo",
    "SafetyRating",
    "SelfAnalyzer",
    "SourceMap",
    "StrategyJournal",
    "SystemMetrics",
]