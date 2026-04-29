"""Public API for auton.metamind — the self-modification and adaptation system."""

from auton.metamind.adaption_engine import AdaptionConfig, AdaptionEngine
from auton.metamind.ci_cd_generator import CICDArtifact, CICDGenerator
from auton.metamind.code_generator import CodeGenerator, LLMProvider
from auton.metamind.code_introspector import (
    ClassLocation,
    CodeIntrospector,
    FunctionLocation,
    IntrospectionError,
    ModuleComplexity,
    ParseError,
    SelfModificationError,
)
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
from auton.metamind.dependency_manager import (
    DependencyError,
    DependencyManager,
    DependencyReport,
    InstallError,
    UnresolvableImportError,
)
from auton.metamind.deployment_manager import DeploymentError, DeploymentManager, DeploymentRecord
from auton.metamind.evolution_gate import EvolutionGate
from auton.metamind.marketplace_lister import ListingRecord, MarketplaceError, MarketplaceLister
from auton.metamind.module_generator import (
    GeneratedCode as ModuleGeneratedCode,
    GenerationError,
    ModuleGenerator,
    ModuleSpecification,
    TemplateNotFoundError,
)
from auton.metamind.patch_applier import (
    CodePatch,
    DiffParseError,
    HunkApplyError,
    PatchApplier,
    PatchError,
    PatchResult,
    RollbackError,
    RollbackResult,
    TestRunner,
)
from auton.metamind.product_manager import (
    CostEstimate,
    MarketOpportunity,
    ProductCategory,
    ProductManager,
    ProductRecord,
    ProductStage,
)
from auton.metamind.rollback_journal import PatchRecord, RollbackJournal
from auton.metamind.revenue_tracker import ProductMetrics, RevenueEvent, RevenueTracker
from auton.metamind.schema_evolver import (
    ConfigCorruptionError,
    MigrationProposal,
    MigrationRecord,
    MigrationResult,
    SchemaEvolver,
    SchemaMigrationError,
)
from auton.metamind.self_analyzer import SelfAnalyzer
from auton.metamind.self_modification_engine import (
    ModificationResult,
    SelfModificationEngine,
    TierGateError,
)
from auton.metamind.strategy_journal import StrategyJournal

__all__ = [
    "AdaptionConfig",
    "AdaptionEngine",
    "AdaptationProposal",
    "CICDArtifact",
    "CICDGenerator",
    "ClassInfo",
    "ClassLocation",
    "CodeGenerator",
    "CodeIntrospector",
    "CodePatch",
    "ConfigCorruptionError",
    "CostEstimate",
    "DecisionType",
    "DeploymentError",
    "DeploymentManager",
    "DeploymentRecord",
    "DependencyError",
    "DependencyManager",
    "DependencyReport",
    "DiffParseError",
    "EvolutionGate",
    "EvolutionResult",
    "FunctionInfo",
    "FunctionLocation",
    "GeneratedCode",
    "GenerationError",
    "HunkApplyError",
    "InstallError",
    "IntrospectionError",
    "JournalEntry",
    "LLMProvider",
    "ListingRecord",
    "MarketOpportunity",
    "MarketplaceError",
    "MarketplaceLister",
    "MigrationProposal",
    "MigrationRecord",
    "MigrationResult",
    "ModuleComplexity",
    "ModuleGeneratedCode",
    "ModuleGenerator",
    "ModuleInfo",
    "ModuleSpecification",
    "ModificationResult",
    "ParseError",
    "PatchApplier",
    "PatchError",
    "PatchRecord",
    "PatchResult",
    "ProductCategory",
    "ProductManager",
    "ProductMetrics",
    "ProductRecord",
    "ProductStage",
    "RevenueEvent",
    "RevenueTracker",
    "RollbackError",
    "RollbackJournal",
    "RollbackResult",
    "SafetyRating",
    "SchemaEvolver",
    "SchemaMigrationError",
    "SelfAnalyzer",
    "SelfModificationEngine",
    "SelfModificationError",
    "SourceMap",
    "StrategyJournal",
    "SystemMetrics",
    "TemplateNotFoundError",
    "TestRunner",
    "TierGateError",
    "UnresolvableImportError",
]
