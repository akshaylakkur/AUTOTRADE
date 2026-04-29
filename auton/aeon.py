"""Main orchestrator for Project ÆON."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

from auton.analytics.alpha_engine import AlphaEngine
from auton.analytics.revenue_engine import RevenueEngine
from auton.analytics.risk_management import RiskManager
from auton.core.approval_engine import ApprovalEngine
from auton.core.config import AeonConfig, TierGate
from auton.core.consciousness import Consciousness
from auton.core.constants import SEED_BALANCE
from auton.core.event_bus import EventBus, Priority
from auton.core.events import (
    ActionProposed as CoreActionProposed,
    BalanceChanged,
    EnvironmentalUpdate,
    Hibernate,
    OpportunityDiscovered,
    ProductDeployed,
    Shutdown,
    SubscriptionPurchased,
    TradeSignal,
    VerificationCodeReceived,
    DecisionMade,
)
from auton.core.state_machine import State as LifecycleState, StateMachine
from auton.cortex.decision_engine import (
    DecisionQueue,
    MultiObjectiveOptimizer,
    Opportunity,
    OpportunityEvaluator,
    ResourceAllocator,
    RiskEngine,
)
from auton.cortex.executor import TacticalExecutor
from auton.cortex.free_will import FreeWillEngine, GoalGenerator
from auton.cortex.meta_cognition import MetaCognition
from auton.cortex.model_router import ModelRouter
from auton.cortex.bedrock_provider import BedrockProvider
from auton.cortex.ollama_provider import OllamaProvider
from auton.cortex.planner import StrategicPlanner
from auton.limbs.human_gateway import HumanGateway
from auton.limbs.communications.email_client import EmailClient, SMTPConfig
from auton.limbs.communications.queue import EmailQueue
from auton.limbs.trading import BinanceSpotTradingLimb
from auton.limbs.payments import StripePaymentsLimb
from auton.senses.environment import EnvironmentalSensor
from auton.senses.market_data import BinanceSpotConnector
from auton.senses.intelligence import OpportunityMonitor
from auton.ledger.burn_analyzer import BurnAnalyzer
from auton.ledger.cost_tracker import CostTracker
from auton.ledger.exceptions import InsufficientFundsError
from auton.ledger.master_wallet import MasterWallet
from auton.ledger.pnl_engine import PnLEngine
from auton.metamind.adaption_engine import AdaptionConfig, AdaptionEngine
from auton.metamind.code_generator import CodeGenerator
from auton.metamind.evolution_gate import EvolutionGate
from auton.metamind.self_analyzer import SelfAnalyzer
from auton.metamind.strategy_journal import StrategyJournal
from auton.reflexes.circuit_breakers import CircuitBreakers
from auton.reflexes.emergency_liquidator import EmergencyLiquidator
from auton.reflexes.position_sizer import PositionSizer
from auton.reflexes.stop_loss import StopLossEngine
from auton.security.coordinator import SecureExecutionEnvironment
from auton.security.spend_caps import SpendCaps
from auton.security.vault import Vault

logger = logging.getLogger("aeon")


class TerminalProtocol:
    """Handles the irreversible shutdown sequence when balance reaches zero."""

    def __init__(self, wallet: MasterWallet, vault: Vault, ledger_path: Path) -> None:
        self._wallet = wallet
        self._vault = vault
        self._ledger_path = ledger_path

    async def execute(self, cause: str) -> None:
        """Run the terminal protocol.

        Steps:
        1. Liquidate all positions.
        2. Export final ledger to cold storage.
        3. Revoke all API keys.
        4. Generate and print obituary.
        5. Shutdown.
        """
        logger.critical("TERMINAL PROTOCOL INITIATED. Cause: %s", cause)

        # 1. Liquidation is handled by emergency liquidator before this is called

        # 2. Export ledger
        cold_storage = Path("cold_storage")
        cold_storage.mkdir(exist_ok=True)
        export_path = cold_storage / f"final_ledger_{datetime.now(timezone.utc).isoformat()}.db"
        try:
            import shutil
            shutil.copy(self._ledger_path, export_path)
            logger.info("Ledger exported to %s", export_path)
        except Exception as e:
            logger.error("Failed to export ledger: %s", e)

        # 3. Revoke keys
        try:
            self._vault.revoke_all()
            logger.info("All API keys revoked.")
        except Exception as e:
            logger.error("Failed to revoke keys: %s", e)

        # 4. Obituary
        obituary = await self._generate_obituary(cause)
        obituary_path = cold_storage / "obituary.json"
        obituary_path.write_text(json.dumps(obituary, indent=2))
        logger.info("Obituary generated.\n%s", json.dumps(obituary, indent=2))

        # 5. Shutdown signal
        logger.critical("ÆON signing off.")

    async def _generate_obituary(self, cause: str) -> dict[str, Any]:
        balance = self._wallet.get_balance()
        history = self._wallet.get_transaction_history(limit=10000)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cause_of_death": cause,
            "final_balance": balance,
            "total_transactions": len(history),
            "first_transaction": history[0]["timestamp"] if history else None,
            "last_transaction": history[-1]["timestamp"] if history else None,
        }


class AEON:
    """Autonomous Economic Operating Node — the main runtime orchestrator."""

    def __init__(self, config_path: str | None = None) -> None:
        self._event_bus = EventBus()
        self._state_machine = StateMachine()
        self._wallet = MasterWallet(db_path="data/aeon_ledger.db")
        self._cost_tracker = CostTracker(self._wallet)
        self._pnl = PnLEngine()
        self._burn = BurnAnalyzer()
        self._tier_gate = TierGate()
        self._spend_caps = SpendCaps()
        self._vault = Vault(db_path="data/aeon_vault.db")
        self._security = SecureExecutionEnvironment(wallet=self._wallet)
        self._terminal = TerminalProtocol(self._wallet, self._vault, Path("data/aeon_ledger.db"))
        self._consciousness = Consciousness(db_path="data/consciousness.db")

        # Reflexes
        self._stop_loss = StopLossEngine(event_bus=self._event_bus)
        self._liquidator = EmergencyLiquidator(event_bus=self._event_bus)
        self._position_sizer = PositionSizer()
        self._circuit_breakers = CircuitBreakers(event_bus=self._event_bus)

        # Cortex
        self._planner = StrategicPlanner()
        self._executor = TacticalExecutor(event_bus=self._event_bus)
        self._meta = MetaCognition()
        provider = AeonConfig.AEON_LLM_PROVIDER.lower()
        if provider == "bedrock" and AeonConfig.BEDROCK_AWS_ACCESS_KEY_ID:
            logger.info("LLM provider: Amazon Bedrock (%s)", AeonConfig.BEDROCK_MODEL_ID)
            self._llm = BedrockProvider(
                access_key_id=AeonConfig.BEDROCK_AWS_ACCESS_KEY_ID,
                secret_access_key=AeonConfig.BEDROCK_AWS_SECRET_ACCESS_KEY,
                region=AeonConfig.BEDROCK_AWS_REGION,
                model_id=AeonConfig.BEDROCK_MODEL_ID,
            )
        else:
            if provider not in ("ollama", ""):
                logger.warning(
                    "LLM provider '%s' not configured or not implemented; falling back to Ollama.",
                    provider,
                )
            logger.info("LLM provider: Ollama (%s @ %s)", AeonConfig.OLLAMA_MODEL, AeonConfig.OLLAMA_HOST)
            self._llm = OllamaProvider(
                host=AeonConfig.OLLAMA_HOST,
                model=AeonConfig.OLLAMA_MODEL,
            )
        self._model_router = ModelRouter(
            frugal_provider=self._llm,
            deep_provider=self._llm,
            meta_cognition=self._meta,
        )
        self._free_will = FreeWillEngine()
        self._goal_generator = GoalGenerator(event_bus=self._event_bus)
        self._opportunity_evaluator = OpportunityEvaluator()
        self._risk_engine = RiskEngine()
        self._optimizer = MultiObjectiveOptimizer()
        self._resource_allocator = ResourceAllocator()
        self._decision_queue = DecisionQueue()

        # Analytics
        self._alpha = AlphaEngine()
        self._revenue = RevenueEngine()
        self._risk = RiskManager()

        # Metamind — self-modification pipeline backed by Ollama
        self._adaption: AdaptionEngine = self._build_adaption()

        # Human-in-the-loop infrastructure
        self._approval_engine = ApprovalEngine(event_bus=self._event_bus)
        self._email_queue = EmailQueue(db_path="data/email_queue.db")
        self._email_client = self._build_email_client()
        self._env_sensor = EnvironmentalSensor(event_bus=self._event_bus)

        # Market data & intelligence
        self._market_connector = BinanceSpotConnector(event_bus=self._event_bus)
        self._opportunity_monitor = OpportunityMonitor(
            event_bus=self._event_bus, config=None,
        )

        # External limbs (wrapped with HumanGateway when restricted)
        self._trading_limb = BinanceSpotTradingLimb(event_bus=self._event_bus, paper=True)
        self._payments_limb = StripePaymentsLimb(event_bus=self._event_bus)

        if AeonConfig.RESTRICTED_MODE:
            self._trading_gateway = HumanGateway(
                self._trading_limb,
                event_bus=self._event_bus,
                wallet=self._wallet,
                risk_manager=self._risk,
                burn_analyzer=self._burn,
                pnl_engine=self._pnl,
                restricted_mode=True,
                default_recipient=AeonConfig.EMAIL_CONFIG.get("recipient_email", ""),
                reasoning_callback=self._generate_reasoning,
                market_data_callback=self._generate_market_snapshot,
            )
            self._payments_gateway = HumanGateway(
                self._payments_limb,
                event_bus=self._event_bus,
                wallet=self._wallet,
                restricted_mode=True,
                default_recipient=AeonConfig.EMAIL_CONFIG.get("recipient_email", ""),
            )
        else:
            self._trading_gateway = self._trading_limb
            self._payments_gateway = self._payments_limb

        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._task_queue: asyncio.Queue[asyncio.Task[Any]] = asyncio.Queue()
        self._registered_tasks: list[dict[str, Any]] = []

    def _build_email_client(self) -> EmailClient:
        cfg = AeonConfig.EMAIL_CONFIG
        smtp = SMTPConfig(
            host=cfg.get("smtp_host", ""),
            port=cfg.get("smtp_port", 587),
            username=cfg.get("sender_email", ""),
            password=cfg.get("sender_password", ""),
            use_tls=cfg.get("use_tls", True),
            from_address=cfg.get("sender_email", "aeon@auton.local"),
        )
        return EmailClient(
            config=smtp,
            queue=self._email_queue,
            recipient=cfg.get("recipient_email", ""),
            event_bus=self._event_bus,
        )

    def _build_adaption(self) -> AdaptionEngine:
        return AdaptionEngine(
            analyzer=SelfAnalyzer(),
            generator=CodeGenerator(llm=self._llm),
            gate=EvolutionGate(),
            journal=StrategyJournal(db_path="data/strategy_journal.db"),
            config=AdaptionConfig(
                target_module="auton",
                max_daily_cost=0.0,  # Ollama is free
            ),
        )

    async def initialize(self, seed_balance: float = SEED_BALANCE) -> None:
        """Initialize ledger with seed capital and transition to INIT.

        If the ledger already has a positive balance (crash recovery), the seed
        is skipped to avoid double-capitalisation.
        """
        Path("data").mkdir(exist_ok=True)
        AeonConfig.validate()

        existing_balance = self._wallet.get_balance()
        if existing_balance > 0:
            logger.info(
                "Crash recovery: existing balance $%.2f detected — skipping seed.",
                existing_balance,
            )
            self._consciousness.remember(
                "system_restart",
                {"existing_balance": existing_balance, "seed_skipped": True},
                importance=0.8,
            )
        else:
            self._wallet.credit(seed_balance, reason="seed_capital")
            logger.info("ÆON initialised with seed balance $%.2f", seed_balance)

        if self._state_machine.get_current_state() != LifecycleState.INIT:
            await self._state_machine.transition_to(LifecycleState.INIT)
        logger.info("ÆON initialized with balance $%.2f", self._wallet.get_balance())

        if AeonConfig.RESTRICTED_MODE:
            logger.info("╔════════════════════════════════════════════════════════════╗")
            logger.info("║  RESTRICTED MODE — All financial/deploy actions require  ║")
            logger.info("║  human email approval before execution.                    ║")
            logger.info("╚════════════════════════════════════════════════════════════╝")
        else:
            logger.info("╔════════════════════════════════════════════════════════════╗")
            logger.info("║  AUTONOMOUS MODE — Operating without human approval gate.  ║")
            logger.info("╚════════════════════════════════════════════════════════════╝")

    async def start(self) -> None:
        """Enter RUNNING state and begin the economic loop."""
        if self._state_machine.get_current_state() != LifecycleState.INIT:
            raise RuntimeError("Must initialize before starting")

        await self._state_machine.transition_to(LifecycleState.RUNNING)
        self._running = True

        # Start event bus background worker
        await self._event_bus.start()

        # Start environmental sensor for continuous context
        await self._env_sensor.start()

        # Connect to market data (Binance public API)
        await self._market_connector.connect()
        logger.info("Market data connector: %s", self._market_connector.connected)

        # Start intelligence opportunity monitor
        await self._opportunity_monitor.start()

        # Start email retry worker if SMTP is configured
        if AeonConfig.EMAIL_CONFIG.get("smtp_host"):
            await self._email_client.start_retry_worker()

        # Register signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Subscribe to critical events
        await self._event_bus.subscribe(BalanceChanged, self._on_balance_changed)
        await self._event_bus.subscribe(Hibernate, self._on_hibernate)
        await self._event_bus.subscribe(Shutdown, self._on_shutdown)
        await self._event_bus.subscribe(
            OpportunityDiscovered, self._on_opportunity_discovered, priority=Priority.URGENT
        )
        await self._event_bus.subscribe(
            VerificationCodeReceived, self._on_verification_code_received, priority=Priority.URGENT
        )
        await self._event_bus.subscribe(
            SubscriptionPurchased, self._on_subscription_purchased, priority=Priority.NORMAL
        )
        await self._event_bus.subscribe(
            DecisionMade, self._on_decision_made, priority=Priority.NORMAL
        )

        self._consciousness.remember(
            "system_started",
            {"balance": self._wallet.get_balance(), "mode": "restricted" if AeonConfig.RESTRICTED_MODE else "autonomous"},
            importance=0.9,
        )

        # Launch background tasks
        self._tasks = [
            asyncio.create_task(self._decision_loop()),
            asyncio.create_task(self._planning_loop()),
            asyncio.create_task(self._reflex_loop()),
            asyncio.create_task(self._monitor_loop()),
            asyncio.create_task(self._adaptation_loop()),
            asyncio.create_task(self._task_queue_loop()),
            asyncio.create_task(self._email_digest_loop()),
            asyncio.create_task(self._market_data_loop()),
        ]

        logger.info("ÆON is RUNNING.")
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Free-will decision loop
    # ------------------------------------------------------------------
    async def _decision_loop(self) -> None:
        """Persistent free-will loop that decides what to do next.

        Pulls environmental context, recent memories, and market observations,
        then routes through the full decision pipeline: free-will exploration
        → opportunity evaluation → resource allocation → execution.
        """
        while self._running:
            try:
                await asyncio.sleep(10)
                current_state = self._state_machine.get_current_state()
                if current_state in (LifecycleState.HIBERNATE, LifecycleState.TERMINAL):
                    continue

                balance = self._wallet.get_balance()
                if balance <= 0:
                    await self._terminal.execute("balance_zero")
                    await self.shutdown()
                    return

                tier = self._tier_gate.get_tier(balance)

                # Gather context
                env_snapshot = await self._env_sensor.sample()
                context_prompt = self._consciousness.generate_context_prompt()

                # Assemble candidate opportunities
                candidates: list[Opportunity] = []

                # 1. Alpha-engine trading signals (when available)
                try:
                    alpha_signals = self._alpha.scan_opportunities(balance, tier)
                    if alpha_signals:
                        for sig in alpha_signals[:3]:
                            candidates.append(Opportunity(
                                id=f"alpha_{datetime.now(timezone.utc).timestamp()}",
                                opportunity_type="trade",
                                expected_return=sig.get("estimated_value", 0.0),
                                risk_score=1.0 - sig.get("confidence", 0.5),
                                capital_required=min(balance * 0.02, 5.0),
                                time_horizon_hours=sig.get("horizon_hours", 24.0),
                                confidence=sig.get("confidence", 0.5),
                                metadata={"source": "alpha_engine", "signal": sig},
                            ))
                except Exception:
                    pass

                # 2. Inject strategic goals as opportunities
                goals = self._goal_generator.generate_goals(balance, tier)
                for g in goals:
                    target = g.milestones[0].target_value if g.milestones else balance * 0.05
                    candidates.append(Opportunity(
                        id=f"goal_{g.name}",
                        opportunity_type="strategic_goal",
                        expected_return=target,
                        risk_score=0.3,
                        capital_required=min(target * 0.1, balance * 0.05),
                        time_horizon_hours=48.0,
                        confidence=0.6,
                        metadata={"goal": g.name, "description": g.description},
                    ))

                # 3. Free-will exploration (serendipity)
                candidates = self._free_will.explore(candidates, balance, tier)

                if not candidates:
                    continue

                # Evaluate and score opportunities
                resource_decisions: list[Any] = []
                for opp in candidates[:10]:
                    score = self._opportunity_evaluator.evaluate(opp, balance)
                    if not score.approved:
                        continue
                    # Convert Opportunity → ResourceDecision
                    from auton.cortex.decision_engine import ResourceDecision as RD
                    resource_decisions.append(RD(
                        action=opp.metadata.get("description", opp.opportunity_type),
                        expected_roi=opp.expected_return / max(opp.capital_required, 0.01),
                        confidence=opp.confidence,
                        risk_score=opp.risk_score,
                        time_horizon=opp.time_horizon_hours,
                        required_budget=opp.capital_required,
                        strategy=opp.opportunity_type if opp.opportunity_type in ("trade", "saas", "arbitrage", "content", "api") else "trading",
                        metadata=opp.metadata,
                    ))

                if not resource_decisions:
                    continue

                # Multi-objective optimisation (score + rank)
                optimised = self._optimizer.optimise(resource_decisions)
                if not optimised:
                    continue

                # Allocate capital
                allocations = self._resource_allocator.allocate(balance, optimised)
                if not allocations:
                    continue

                for alloc in allocations[:3]:  # Limit concurrent actions
                    # Find the matching decision
                    matching = [rd for rd in resource_decisions if getattr(rd, 'action', '') == alloc.decision_id or True]
                    rd = matching[0] if matching else resource_decisions[0]
                    logger.info(
                        "Decision: %s | ROI=%.2f%% | confidence=%.2f | budget=$%.2f",
                        rd.action, rd.expected_roi * 100,
                        rd.confidence, rd.required_budget,
                    )

                    # Record in consciousness
                    dec_id = self._consciousness.record_decision(
                        action=rd.action,
                        strategy=rd.strategy,
                        expected_roi=rd.expected_roi,
                        confidence=rd.confidence,
                        risk_score=rd.risk_score,
                        budget=alloc.amount,
                    )

                    # Emit event for downstream execution
                    await self._event_bus.publish(
                        DecisionMade,
                        DecisionMade(
                            action=rd.action,
                            expected_roi=rd.expected_roi,
                            confidence=rd.confidence,
                            risk_score=rd.risk_score,
                            required_budget=alloc.amount,
                            strategy=rd.strategy,
                            metadata={"decision_id": dec_id, **rd.metadata},
                        ),
                        priority=Priority.NORMAL,
                    )

                # Transition to EXECUTING if decisions were made
                if decisions:
                    current = self._state_machine.get_current_state()
                    if current not in (LifecycleState.HIBERNATE, LifecycleState.TERMINAL):
                        await self._state_machine.transition_to(LifecycleState.EXECUTING)

            except Exception as e:
                logger.exception("Decision loop error: %s", e)
                self._consciousness.remember(
                    "loop_error",
                    {"loop": "decision", "error": str(e)},
                    importance=0.7,
                )

    # ------------------------------------------------------------------
    # Task queue
    # ------------------------------------------------------------------
    def register_task(
        self,
        name: str,
        coro_factory: Callable[[], Coroutine[Any, Any, None]],
        interval_seconds: float | None = None,
    ) -> None:
        """Register a long-running task with the orchestrator.

        Args:
            name: Human-readable task name.
            coro_factory: A callable that returns a coroutine to run.
            interval_seconds: If provided, the task will be re-queued at this
                interval. If None, the task runs once.
        """
        self._registered_tasks.append({
            "name": name,
            "coro_factory": coro_factory,
            "interval_seconds": interval_seconds,
        })
        logger.info("Registered task '%s' (interval=%s)", name, interval_seconds)

    async def _task_queue_loop(self) -> None:
        """Process the task queue, dispatching registered long-running tasks."""
        while self._running:
            try:
                try:
                    task = await asyncio.wait_for(self._task_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if task.done() or task.cancelled():
                    self._task_queue.task_done()
                    continue
                # Tasks placed in the queue are already running asyncio.Task objects
                self._task_queue.task_done()
            except Exception as e:
                logger.exception("Task queue loop error: %s", e)

    async def _planning_loop(self) -> None:
        """Periodic strategic planning — generates plans and tracks execution."""
        while self._running:
            try:
                await asyncio.sleep(60)
                balance = self._wallet.get_balance()
                tier = self._tier_gate.get_tier(balance)

                if balance <= 0:
                    await self._terminal.execute("balance_zero")
                    await self.shutdown()
                    return

                if self._circuit_breakers.is_hibernating():
                    continue

                await self._state_machine.transition_to(LifecycleState.PLANNING)

                # Generate strategic plan
                plan = self._planner.plan_objectives(balance, tier)
                logger.info(
                    "Plan: tier=%d horizon=%s target=$%.2f risk=%.2f goals=%s",
                    plan.tier, plan.horizon, plan.target_revenue,
                    plan.risk_tolerance, plan.goals,
                )

                # Store in consciousness
                self._consciousness.remember(
                    "plan_generated",
                    {
                        "tier": plan.tier,
                        "horizon": plan.horizon,
                        "target_revenue": plan.target_revenue,
                        "risk_tolerance": plan.risk_tolerance,
                        "goals": plan.goals,
                        "capabilities": plan.capability_priorities,
                    },
                    importance=0.5,
                )

                # Decompose into goals
                self._goal_generator.generate_goals(balance, tier)

                # Evaluate whether plan targets can be met with current burn rate
                burn = self._burn.get_burn_rate(days=1)
                runway = self._burn.project_time_to_death(balance, burn)
                if runway < 24:
                    logger.warning("Runway < 24h (%.1f h) — switching to survival mode", runway)
                    self._consciousness.remember(
                        "survival_mode",
                        {"runway_hours": runway, "balance": balance, "burn_rate": burn},
                        importance=0.9,
                    )

                await self._state_machine.transition_to(LifecycleState.RUNNING)

            except Exception as e:
                logger.exception("Planning loop error: %s", e)
                self._consciousness.remember(
                    "loop_error", {"loop": "planning", "error": str(e)}, importance=0.7,
                )

    async def _reflex_loop(self) -> None:
        """Fast reflex monitoring — survival, circuit breakers, stop-loss."""
        while self._running:
            try:
                await asyncio.sleep(5)
                balance = self._wallet.get_balance()

                if balance <= 0:
                    await self._terminal.execute("balance_zero")
                    await self.shutdown()
                    return

                if balance < SEED_BALANCE * 0.2:
                    logger.critical("Balance below 20%% survival threshold ($%.2f)!", balance)
                    self._consciousness.remember(
                        "survival_threshold_breach",
                        {"balance": balance, "threshold": SEED_BALANCE * 0.2},
                        importance=1.0,
                    )
                    await self._liquidator.liquidate_all_positions("survival_threshold")

                # Check circuit breaker state
                if self._circuit_breakers.is_hibernating():
                    if self._state_machine.get_current_state() != LifecycleState.HIBERNATE:
                        await self._state_machine.transition_to(LifecycleState.HIBERNATE)
                        self._consciousness.remember(
                            "hibernation_entered",
                            {"reason": "circuit_breaker", "balance": balance},
                            importance=0.9,
                        )
                elif self._state_machine.get_current_state() == LifecycleState.HIBERNATE:
                    await self._state_machine.transition_to(LifecycleState.RUNNING)
                    self._consciousness.remember(
                        "hibernation_exited",
                        {"balance": balance},
                        importance=0.7,
                    )

            except Exception as e:
                logger.exception("Reflex loop error: %s", e)

    async def _monitor_loop(self) -> None:
        """Continuous burn rate, tier, and health monitoring."""
        while self._running:
            try:
                await asyncio.sleep(30)
                balance = self._wallet.get_balance()
                burn = self._burn.get_burn_rate(days=1)
                runway = self._burn.project_time_to_death(balance, burn)
                tier = self._tier_gate.get_tier(balance)

                logger.info(
                    "Status: $%.2f | tier=%d | burn=$%.4f/day | runway=%.1fh",
                    balance, tier, burn, runway,
                )

                self._consciousness.remember(
                    "health_check",
                    {
                        "balance": balance, "tier": tier,
                        "burn_rate": burn, "runway_hours": runway,
                    },
                    importance=0.2,
                )

                # Tier change detection
                prev_tier_mem = self._consciousness.recall(
                    limit=1, event_type="health_check"
                )
                if prev_tier_mem:
                    prev_tier = prev_tier_mem[0].payload.get("tier", tier)
                    if prev_tier != tier:
                        logger.info("Tier changed: %d → %d", prev_tier, tier)
                        self._consciousness.remember(
                            "tier_changed",
                            {"old_tier": prev_tier, "new_tier": tier, "balance": balance},
                            importance=0.6,
                        )

            except Exception as e:
                logger.exception("Monitor loop error: %s", e)

    async def _adaptation_loop(self) -> None:
        """Periodic self-analysis and adaptation via metamind pipeline."""
        while self._running:
            try:
                await asyncio.sleep(3600)
                balance = self._wallet.get_balance()
                if balance <= 0:
                    continue

                logger.info("Running self-analysis and adaptation review...")
                self._consciousness.remember("adaptation_cycle_started", {}, importance=0.3)

                # Review strategy performance from consciousness
                strategies = self._consciousness.get_all_strategy_stats()
                for s in strategies:
                    logger.info(
                        "Strategy '%s': %d trades, %.0f%% win, P&L $%.2f",
                        s.strategy_name, s.total_trades, s.win_rate * 100, s.total_pnl,
                    )

                # Trigger adaptation engine if available
                if self._adaption is not None:
                    try:
                        journal = getattr(self._adaption, "_journal", None)
                        if journal and strategies:
                            metrics = {
                                "total_trades": sum(s.total_trades for s in strategies),
                                "total_pnl": sum(s.total_pnl for s in strategies),
                                "avg_win_rate": (
                                    sum(s.win_rate for s in strategies) / len(strategies)
                                    if strategies else 0.0
                                ),
                            }
                            self._adaption.review_performance(journal, metrics)
                            proposal = self._adaption.propose_adaptation()
                            if proposal:
                                logger.info("Adaptation proposed: %s", proposal.description)
                                self._consciousness.remember(
                                    "adaptation_proposed",
                                    {"description": proposal.description},
                                    importance=0.5,
                                )
                    except Exception:
                        logger.debug("Adaptation engine review skipped (not configured)")

                # Store learnings from recent performance
                recent_decisions = self._consciousness.get_recent_decisions(limit=20)
                successes = [d for d in recent_decisions if d.outcome == "success"]
                failures = [d for d in recent_decisions if d.outcome == "failure"]
                if failures:
                    failure_strategies = {d.strategy for d in failures if d.strategy}
                    self._consciousness.record_learning(
                        f"Strategies with recent failures: {failure_strategies}",
                        domain="strategy",
                        confidence=0.7,
                        source="adaptation_loop",
                    )

                logger.info(
                    "Self-analysis complete: %d successes, %d failures in recent window",
                    len(successes), len(failures),
                )

            except Exception as e:
                logger.exception("Adaptation loop error: %s", e)
                self._consciousness.remember(
                    "loop_error", {"loop": "adaptation", "error": str(e)}, importance=0.7,
                )

    async def _on_balance_changed(self, event: BalanceChanged) -> None:
        """Handle balance change events."""
        logger.info("Balance changed: $%.2f → $%.2f", event.old_balance, event.new_balance)

    async def _on_hibernate(self, event: Hibernate) -> None:
        """Enter hibernation state."""
        logger.warning("Hibernation triggered: %s", event.reason)
        await self._state_machine.transition_to(LifecycleState.HIBERNATE)

    async def _on_shutdown(self, event: Shutdown) -> None:
        """Handle shutdown event."""
        logger.info("Shutdown event received: %s", event.reason)
        await self.shutdown()

    async def _on_opportunity_discovered(self, event: OpportunityDiscovered) -> None:
        """Handle discovered opportunities by transitioning to PLANNING."""
        logger.info(
            "Opportunity discovered in %s (confidence=%.2f, value=$%.2f): %s",
            event.domain,
            event.confidence,
            event.estimated_value,
            event.description,
        )
        current = self._state_machine.get_current_state()
        if current not in (LifecycleState.HIBERNATE, LifecycleState.TERMINAL):
            await self._state_machine.transition_to(LifecycleState.PLANNING)

    async def _on_verification_code_received(self, event: VerificationCodeReceived) -> None:
        """Handle verification codes by transitioning to AWAITING_VERIFICATION."""
        logger.info("Verification code received from %s", event.source)
        current = self._state_machine.get_current_state()
        if current not in (LifecycleState.HIBERNATE, LifecycleState.TERMINAL):
            await self._state_machine.transition_to(LifecycleState.AWAITING_VERIFICATION)

    async def _on_subscription_purchased(self, event: SubscriptionPurchased) -> None:
        """Handle subscription purchases by transitioning to PRODUCT_DEVELOPMENT."""
        logger.info(
            "Subscription purchased: %s (%s) for $%.2f",
            event.service,
            event.tier,
            event.cost,
        )
        current = self._state_machine.get_current_state()
        if current not in (LifecycleState.HIBERNATE, LifecycleState.TERMINAL):
            await self._state_machine.transition_to(LifecycleState.PRODUCT_DEVELOPMENT)

    # ------------------------------------------------------------------
    # Email digest loop
    # ------------------------------------------------------------------
    async def _email_digest_loop(self) -> None:
        """Periodic status digest emails to the human operator."""
        while self._running:
            try:
                await asyncio.sleep(14400)  # Every 4 hours
                if not AeonConfig.EMAIL_CONFIG.get("smtp_host"):
                    continue

                balance = self._wallet.get_balance()
                burn = self._burn.get_burn_rate(days=1)
                runway = self._burn.project_time_to_death(balance, burn)
                tier = self._tier_gate.get_tier(balance)
                summary = self._consciousness.get_consciousness_summary()
                strategies = self._consciousness.get_all_strategy_stats()

                body = f"""\
    ÆON Status Digest
    ==================
    Time:     {datetime.now(timezone.utc).isoformat()}
    Balance:  ${balance:,.2f}
    Tier:     {tier}
    Burn:     ${burn:,.4f}/day
    Runway:   {runway:.1f} hours

    Strategy Performance:
    {chr(10).join(f'  - {s.strategy_name}: {s.total_trades} trades, {s.win_rate:.0%} win, P&L ${s.total_pnl:+.2f}' for s in strategies) if strategies else '  No strategies tracked yet.'}

    Recent Activity:
    {self._consciousness.generate_context_prompt()[:1500]}

    ---
    ÆON Autonomous Economic Operating Node
    """

                from auton.limbs.communications.email_client import ActionProposal as EmailProposal
                proposal = EmailProposal(
                    action_type="digest",
                    what="Periodic Status Digest",
                    why="Scheduled status report",
                    risk="none",
                    expected_outcome="Informational",
                    urgency="low",
                    approval_token=f"digest_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                )
                await self._email_client.send_proposal(proposal)
                logger.info("Status digest email sent")

            except Exception as e:
                logger.exception("Email digest loop error: %s", e)

    # ------------------------------------------------------------------
    # Decision handler
    # ------------------------------------------------------------------
    async def _on_decision_made(self, event: DecisionMade) -> None:
        """Handle a decision by routing it to the appropriate executor."""
        logger.info(
            "Executing decision: %s (strategy=%s, budget=$%.2f)",
            event.action, event.strategy, event.required_budget,
        )

        decision_id = event.metadata.get("decision_id")

        # Route based on strategy type
        if event.strategy == "trading" and event.required_budget > 0:
            try:
                if AeonConfig.RESTRICTED_MODE:
                    await self._trading_gateway.execute_trade(
                        symbol=event.metadata.get("symbol", "BTCUSDT"),
                        side=event.metadata.get("side", "BUY"),
                        quantity=event.metadata.get("quantity", 0.0),
                    )
                else:
                    await self._trading_limb.place_order(
                        symbol=event.metadata.get("symbol", "BTCUSDT"),
                        side=event.metadata.get("side", "BUY"),
                        quantity=event.metadata.get("quantity", 0.0),
                        order_type="MARKET",
                    )

                if decision_id:
                    self._consciousness.resolve_decision(
                        decision_id, "success",
                        actual_return=event.expected_roi * event.required_budget,
                        notes="Trade executed",
                    )
                    self._consciousness.update_strategy_performance(
                        "trading", is_win=True,
                        pnl=event.expected_roi * event.required_budget,
                        roi=event.expected_roi, risk=event.risk_score,
                    )
            except Exception as exc:
                logger.error("Trade execution failed: %s", exc)
                if decision_id:
                    self._consciousness.resolve_decision(
                        decision_id, "failure",
                        notes=f"Execution error: {exc}",
                    )
                    self._consciousness.update_strategy_performance(
                        "trading", is_loss=True, risk=event.risk_score,
                    )

        elif event.strategy in ("saas", "arbitrage", "content", "api"):
            # Product / SaaS opportunity — trigger metamind product pipeline
            logger.info("Product opportunity detected: %s (strategy=%s)", event.action, event.strategy)
            self._consciousness.remember(
                "product_opportunity",
                {
                    "action": event.action,
                    "strategy": event.strategy,
                    "expected_roi": event.expected_roi,
                    "budget": event.required_budget,
                },
                importance=0.5,
            )

            # Create product via metamind pipeline if tier allows it
            try:
                from auton.metamind.product_manager import ProductManager, ProductCategory, MarketOpportunity

                pm = ProductManager()
                opp = MarketOpportunity(
                    name=event.action,
                    description=event.action,
                    category=ProductCategory.TOOL if event.strategy == "api" else ProductCategory.MICROSAAS,
                    estimated_tam=event.expected_roi * event.required_budget * 10,
                    competition_level=0.5,
                    trend_direction=0.6,
                )
                pm.register_opportunity(opp)
                product_id = f"prod_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                pm.create_product(
                    product_id, event.action,
                    ProductCategory.MICROSAAS,
                    pm.estimate_cost(ProductCategory.MICROSAAS, "medium"),
                )

                logger.info("Product created: %s (id=%s)", event.action, product_id)
                self._consciousness.remember(
                    "product_created",
                    {"product_id": product_id, "name": event.action, "strategy": event.strategy},
                    importance=0.6,
                )

                if decision_id:
                    self._consciousness.resolve_decision(
                        decision_id, "success",
                        notes=f"Product created: {product_id}",
                    )
            except Exception as exc:
                logger.error("Product creation failed: %s", exc)
                if decision_id:
                    self._consciousness.resolve_decision(
                        decision_id, "failure",
                        notes=f"Product creation error: {exc}",
                    )

        # Transition back to RUNNING
        current = self._state_machine.get_current_state()
        if current not in (LifecycleState.RUNNING, LifecycleState.HIBERNATE, LifecycleState.TERMINAL):
            await self._state_machine.transition_to(LifecycleState.RUNNING)

    # ------------------------------------------------------------------
    # Market data polling
    # ------------------------------------------------------------------
    async def _market_data_loop(self) -> None:
        """Poll market data connectors and publish updates."""
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
        while self._running:
            try:
                await asyncio.sleep(60)
                if not self._market_connector.connected:
                    continue

                for symbol in symbols:
                    try:
                        md = await self._market_connector.get_ticker(symbol)
                        if md:
                            from auton.core.events import DataReceived
                            ticker_payload = {
                                "symbol": symbol,
                                "price": md.price,
                                "volume_24h": getattr(md, "volume_24h", 0),
                                "timestamp": md.timestamp.isoformat() if hasattr(md, "timestamp") else "",
                            }
                            await self._event_bus.publish(
                                DataReceived,
                                DataReceived(
                                    source="binance",
                                    data_type="ticker",
                                    payload=ticker_payload,
                                ),
                                priority=Priority.BACKGROUND,
                            )
                    except Exception:
                        logger.debug("Failed to fetch ticker for %s", symbol)

            except Exception as e:
                logger.exception("Market data loop error: %s", e)

    # ------------------------------------------------------------------
    # Reasoning & market data callbacks (for HumanGateway context)
    # ------------------------------------------------------------------
    def _generate_reasoning(self, action_type: str, action_payload: dict[str, Any]) -> str:
        """Generate a reasoning summary for human approval emails."""
        balance = self._wallet.get_balance()
        context = self._consciousness.generate_context_prompt()
        recent = self._consciousness.recall(limit=5, min_importance=0.5)
        recent_summary = "; ".join(
            f"{m.event_type}" for m in recent
        ) if recent else "no significant recent events"

        return (
            f"ÆON proposes {action_type} with payload {action_payload}. "
            f"Current balance: ${balance:.2f}. "
            f"Recent context: {recent_summary}. "
            f"Full context: {context[:300]}"
        )

    def _generate_market_snapshot(self) -> dict[str, Any]:
        """Return a concise market snapshot for approval proposals."""
        return {
            "balance": self._wallet.get_balance(),
            "tier": self._tier_gate.get_tier(self._wallet.get_balance()),
            "burn_rate": self._burn.get_burn_rate(days=1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        if not self._running:
            return
        self._running = False
        logger.info("Shutting down ÆON...")

        self._consciousness.remember(
            "system_shutdown",
            {"balance": self._wallet.get_balance()},
            importance=0.9,
        )

        await self._env_sensor.stop()
        await self._market_connector.disconnect()
        await self._opportunity_monitor.stop()
        await self._llm.close()
        await self._email_client.stop_retry_worker()
        await self._event_bus.stop()

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        await self._state_machine.transition_to(LifecycleState.TERMINAL)
        self._consciousness.close()
        logger.info("ÆON shutdown complete.")

    @property
    def balance(self) -> float:
        return self._wallet.get_balance()

    @property
    def state(self) -> LifecycleState:
        return self._state_machine.get_current_state()


async def main() -> None:
    """Entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Ensure a vault key exists for local development
    if not os.environ.get("AEON_VAULT_KEY"):
        from cryptography.fernet import Fernet
        dev_key = Fernet.generate_key().decode()
        os.environ["AEON_VAULT_KEY"] = dev_key

    logger = logging.getLogger("aeon.main")

    aeon = AEON()
    await aeon.initialize()
    await aeon.start()


if __name__ == "__main__":
    asyncio.run(main())
