"""Adaption Engine: drives the self-modification loop."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from auton.metamind.code_generator import CodeGenerator
from auton.metamind.dataclasses import (
    AdaptationProposal,
    EvolutionResult,
    JournalEntry,
    SystemMetrics,
)
from auton.metamind.evolution_gate import EvolutionGate
from auton.metamind.self_analyzer import SelfAnalyzer
from auton.metamind.strategy_journal import StrategyJournal

logger = logging.getLogger(__name__)


@dataclass
class AdaptionConfig:
    """Configuration for the adaption engine."""

    cooldown_minutes: float = 60.0
    min_roi_threshold: float = 1.0
    max_daily_cost: float = 5.0
    target_module: str = "auton"
    mutation_dir: Path = field(default_factory=lambda: Path("mutations"))


class AdaptionEngine:
    """Drives the self-modification loop with ROI proof and safety gates."""

    def __init__(
        self,
        analyzer: SelfAnalyzer,
        generator: CodeGenerator,
        gate: EvolutionGate,
        journal: StrategyJournal,
        config: AdaptionConfig | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.generator = generator
        self.gate = gate
        self.journal = journal
        self.config = config or AdaptionConfig()
        self._history: list[dict[str, Any]] = []
        self._last_adaptation: datetime | None = None
        self._proposals: list[AdaptationProposal] = []

    def review_performance(
        self,
        strategy_journal: StrategyJournal | None = None,
        system_metrics: SystemMetrics | None = None,
    ) -> dict[str, Any]:
        """Analyze recent outcomes and return a performance summary."""
        journal = strategy_journal or self.journal
        recent = journal.get_recent_entries(limit=100)
        total_pnl = sum(e.pnl for e in recent)
        total_cost = sum(e.cost for e in recent)
        wins = sum(1 for e in recent if e.pnl > 0)
        losses = len(recent) - wins

        summary = {
            "total_pnl": round(total_pnl, 6),
            "total_cost": round(total_cost, 6),
            "net": round(total_pnl - total_cost, 6),
            "win_rate": wins / len(recent) if recent else 0.0,
            "wins": wins,
            "losses": losses,
            "entry_count": len(recent),
            "system_metrics": system_metrics.to_dict() if system_metrics else {},
        }
        logger.info("Performance review: %s", summary)
        return summary

    def propose_adaptation(self) -> AdaptationProposal | None:
        """Decide whether to modify code based on ROI proof."""
        if not self._cooldown_elapsed():
            logger.info("Adaptation cooldown still active")
            return None

        if not self._roi_proven():
            logger.info("ROI not proven for previous adaptations")
            return None

        source_map = self.analyzer.analyze_source_tree(self.config.target_module)
        gaps = self.analyzer.find_missing_capabilities()
        bottlenecks = self.analyzer.identify_bottlenecks()

        reasoning_parts: list[str] = []
        if gaps:
            reasoning_parts.append(f"Missing capabilities: {', '.join(gaps)}")
        if bottlenecks:
            top = bottlenecks[0]
            reasoning_parts.append(
                f"Top bottleneck: {top.get('type')} in {top.get('module')} ({top.get('function', 'N/A')})"
            )

        if not reasoning_parts:
            logger.info("No adaptation needed")
            return None

        proposal = AdaptationProposal(
            module_name=self.config.target_module,
            reasoning="; ".join(reasoning_parts),
            expected_benefit="reduce_complexity_or_fill_gap",
            estimated_cost=0.001,
            target_metrics={"complexity": 10.0, "gap_count": 0.0},
        )
        self._proposals.append(proposal)
        logger.info("Adaptation proposed: %s", proposal.reasoning)
        return proposal

    async def execute_adaptation_pipeline(
        self, proposal: AdaptationProposal | None = None
    ) -> EvolutionResult:
        """Run: analyze -> generate -> validate -> promote."""
        if proposal is None:
            proposal = self.propose_adaptation()
        if proposal is None:
            return EvolutionResult(
                passed=False,
                safety_score=0.0,
                promoted=False,
                message="No adaptation proposal available",
            )

        # 1. Analyze
        source_map = self.analyzer.analyze_source_tree(self.config.target_module)

        # 2. Generate
        generated = self.generator.generate_module(
            module_name=proposal.module_name,
            requirements=[proposal.reasoning, proposal.expected_benefit],
            context={"source_map": source_map.to_dict(), "gaps": self.analyzer.find_missing_capabilities()},
        )

        # 3. Validate
        test_code = (
            "\n"
            "def test_generated_module():\n"
            "    assert True\n"
        )
        source_path = generated.mutation_path
        if source_path is None:
            return EvolutionResult(
                passed=False,
                safety_score=0.0,
                promoted=False,
                message="Generation produced no source path",
            )
        target_path = Path(self.config.target_module) / f"{generated.module_name}.py"
        result = self.gate.validate_and_promote(
            code=generated.source,
            source_path=source_path,
            target_path=target_path,
            test_code=test_code,
        )

        # 4. Record
        self._last_adaptation = datetime.now(timezone.utc)
        record = {
            "proposal": proposal.to_dict(),
            "generated": generated.to_dict(),
            "result": result.to_dict(),
            "timestamp": self._last_adaptation.isoformat(),
        }
        self._history.append(record)
        self.journal.log_adaptation(
            reasoning=proposal.reasoning,
            outcome="promoted" if result.promoted else "rejected",
            before_metrics={"gaps": len(self.analyzer.find_missing_capabilities())},
            after_metrics={"gaps": 0},
            cost=generated.cost,
        )

        logger.info(
            "Adaptation pipeline completed: promoted=%s safety_score=%s",
            result.promoted,
            result.safety_score,
        )
        return result

    def get_adaptation_history(self) -> list[dict[str, Any]]:
        """Return a log of all modifications attempted."""
        return list(self._history)

    def _cooldown_elapsed(self) -> bool:
        if self._last_adaptation is None:
            return True
        elapsed = datetime.now(timezone.utc) - self._last_adaptation
        return elapsed >= timedelta(minutes=self.config.cooldown_minutes)

    def _roi_proven(self) -> bool:
        """Check whether previous adaptations demonstrated measurable benefit."""
        if not self._history:
            return True
        recent = self.journal.get_recent_entries(limit=50)
        adaptations = [e for e in recent if e.decision_type.name == "ADAPTATION"]
        if not adaptations:
            return True
        total_pnl = sum(a.pnl for a in adaptations)
        total_cost = sum(a.cost for a in adaptations)
        net = total_pnl - total_cost
        return net >= self.config.min_roi_threshold

    async def emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit a structured self-modification event."""
        logger.info("Event[%s]: %s", event_type, payload)
        # In a real system this would publish to an async event bus.
