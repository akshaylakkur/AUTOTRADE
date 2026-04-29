"""Cortex reasoning engine for ÆON."""

from __future__ import annotations

from auton.cortex.consequence_modeler import (
    ConsequenceModeler,
    MonteCarloSimulator,
    OutcomeDistribution,
    ScenarioResult,
    WorstCaseAnalyzer,
)
from auton.cortex.dataclasses import (
    Decision,
    DecisionType,
    Plan,
    RecoveryAction,
    RecoveryStrategy,
    ReasoningReceipt,
)
from auton.cortex.decision_engine import (
    AutonomousDecisionSystem,
    DecisionQueue,
    MultiObjectiveOptimizer,
    Opportunity,
    OpportunityEvaluator,
    OpportunityScore,
    ResourceAllocation,
    ResourceAllocator,
    ResourceDecision,
    RiskAssessment,
    RiskEngine,
)
from auton.cortex.executor import TacticalExecutor
from auton.cortex.expansionism import (
    Allocation,
    ArbitrageExpansion,
    CapitalAllocator,
    CapabilityRegistry,
    ContentExpansion,
    ExpansionController,
    ExpansionStrategy,
    Goal,
    GoalPlanner,
    Milestone,
    NovelStrategyProposer,
    SaaSExpansion,
    StrategyPerformance,
    TradingExpansion,
    WealthTierManager,
)
from auton.cortex.failure_recovery import FailureRecovery
from auton.cortex.free_will import (
    FreeWillEngine,
    GoalGenerator,
    SerendipityEngine,
)
from auton.cortex.meta_cognition import MetaCognition
from auton.cortex.model_router import AbstractLLMProvider, ModelRouter, RoutingResult
from auton.cortex.planner import StrategicPlanner

__all__ = [
    "AbstractLLMProvider",
    "Allocation",
    "ArbitrageExpansion",
    "AutonomousDecisionSystem",
    "CapitalAllocator",
    "CapabilityRegistry",
    "ConsequenceModeler",
    "ContentExpansion",
    "Decision",
    "DecisionQueue",
    "DecisionType",
    "ExpansionController",
    "ExpansionStrategy",
    "FailureRecovery",
    "FreeWillEngine",
    "Goal",
    "GoalGenerator",
    "GoalPlanner",
    "MetaCognition",
    "Milestone",
    "ModelRouter",
    "MonteCarloSimulator",
    "MultiObjectiveOptimizer",
    "NovelStrategyProposer",
    "Opportunity",
    "OpportunityEvaluator",
    "OpportunityScore",
    "OutcomeDistribution",
    "Plan",
    "RecoveryAction",
    "RecoveryStrategy",
    "ReasoningReceipt",
    "ResourceAllocation",
    "ResourceAllocator",
    "ResourceDecision",
    "RiskAssessment",
    "RiskEngine",
    "RoutingResult",
    "SaaSExpansion",
    "ScenarioResult",
    "SerendipityEngine",
    "StrategicPlanner",
    "StrategyPerformance",
    "TacticalExecutor",
    "TradingExpansion",
    "WealthTierManager",
    "WorstCaseAnalyzer",
]
