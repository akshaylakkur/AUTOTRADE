"""Tactical executor for ÆON."""

from __future__ import annotations

from typing import Any

from auton.core.config import Capability, TierGate
from auton.core.event_bus import EventBus
from auton.core.events import TradeSignal
from auton.cortex.dataclasses import Decision, DecisionType


class TacticalExecutor:
    """Evaluates opportunities and emits trade/product decisions via the event bus."""

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        tier_gate: type[TierGate] = TierGate,
    ) -> None:
        self._event_bus = event_bus
        self._tier_gate = tier_gate

    async def evaluate_opportunity(self, opportunity: dict[str, Any]) -> Decision:
        """Score an opportunity and return a tactical Decision.

        The opportunity dict must contain at least a ``type`` key.
        Recognised types: ``"trade"``, ``"product_launch"``.

        Args:
            opportunity: Raw opportunity payload.

        Returns:
            A :class:`Decision` with a go / no-go verdict.
        """
        opp_type = opportunity.get("type", "unknown")
        balance = opportunity.get("balance", 0.0)

        if opp_type == "trade":
            return self._evaluate_trade(opportunity, balance)
        elif opp_type == "product_launch":
            return self._evaluate_product_launch(opportunity, balance)

        return Decision(
            decision_type=DecisionType.NO_OP,
            symbol=None,
            side=None,
            quantity=0.0,
            price=None,
            confidence=0.0,
            expected_profit=0.0,
            metadata={"reason": "unrecognised_opportunity_type", "opportunity_type": opp_type},
        )

    def _evaluate_trade(self, opportunity: dict[str, Any], balance: float) -> Decision:
        symbol = opportunity.get("symbol", "UNKNOWN")
        side = opportunity.get("side", "BUY")
        quantity = opportunity.get("quantity", 0.0)
        price = opportunity.get("price")
        confidence = opportunity.get("confidence", 0.0)
        expected_profit = opportunity.get("expected_profit", 0.0)

        if not self._tier_gate.is_allowed(Capability.SPOT_TRADING, balance):
            return Decision(
                decision_type=DecisionType.NO_OP,
                symbol=symbol,
                side=side,
                quantity=0.0,
                price=price,
                confidence=confidence,
                expected_profit=expected_profit,
                metadata={"reason": "tier_gate_denied", "capability": "SPOT_TRADING"},
            )

        # Conservative filter: only act on high-confidence opportunities
        if confidence < 0.5:
            return Decision(
                decision_type=DecisionType.NO_OP,
                symbol=symbol,
                side=side,
                quantity=0.0,
                price=price,
                confidence=confidence,
                expected_profit=expected_profit,
                metadata={"reason": "insufficient_confidence", "threshold": 0.5},
            )

        return Decision(
            decision_type=DecisionType.TRADE,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            confidence=confidence,
            expected_profit=expected_profit,
            metadata=opportunity,
        )

    def _evaluate_product_launch(self, opportunity: dict[str, Any], balance: float) -> Decision:
        product_id = opportunity.get("product_id", "UNKNOWN")
        confidence = opportunity.get("confidence", 0.0)
        expected_profit = opportunity.get("expected_profit", 0.0)

        if not self._tier_gate.is_allowed(Capability.SAAS_HOSTING, balance):
            return Decision(
                decision_type=DecisionType.NO_OP,
                symbol=product_id,
                side=None,
                quantity=0.0,
                price=None,
                confidence=confidence,
                expected_profit=expected_profit,
                metadata={"reason": "tier_gate_denied", "capability": "SAAS_HOSTING"},
            )

        return Decision(
            decision_type=DecisionType.PRODUCT_LAUNCH,
            symbol=product_id,
            side=None,
            quantity=0.0,
            price=None,
            confidence=confidence,
            expected_profit=expected_profit,
            metadata=opportunity,
        )

    async def execute_decision(self, decision: Decision) -> dict[str, Any]:
        """Publish the appropriate event(s) for a approved Decision.

        Args:
            decision: The decision to action.

        Returns:
            A result dict describing what was emitted.
        """
        if decision.decision_type == DecisionType.NO_OP:
            return {"executed": False, "reason": "no_op_decision"}

        if decision.decision_type == DecisionType.TRADE:
            if self._event_bus is None:
                return {"executed": False, "reason": "no_event_bus"}
            signal = TradeSignal(
                symbol=decision.symbol or "UNKNOWN",
                side=decision.side or "BUY",
                quantity=decision.quantity,
                price=decision.price,
                metadata={
                    "confidence": decision.confidence,
                    "expected_profit": decision.expected_profit,
                    **decision.metadata,
                },
            )
            await self._event_bus.publish(TradeSignal, signal)
            return {"executed": True, "event": "TradeSignal", "symbol": signal.symbol}

        if decision.decision_type == DecisionType.PRODUCT_LAUNCH:
            if self._event_bus is None:
                return {"executed": False, "reason": "no_event_bus"}
            # ProductLaunch event is not defined in core.events; emit a generic dict
            # so the event bus can still carry it (subscribers use dict type).
            payload = {
                "product_id": decision.symbol,
                "confidence": decision.confidence,
                "expected_profit": decision.expected_profit,
                **decision.metadata,
            }
            await self._event_bus.publish(dict, payload)
            return {"executed": True, "event": "ProductLaunch", "product_id": decision.symbol}

        return {"executed": False, "reason": "unsupported_decision_type"}
