"""Model router for ÆON — switches between frugal and deep inference."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from auton.cortex.meta_cognition import MetaCognition


@dataclass(frozen=True, slots=True)
class RoutingResult:
    """Result of a model-routing decision."""

    provider_name: str
    model_name: str
    estimated_cost: float
    mode: str  # "frugal" or "deep"
    metadata: dict[str, Any]


class AbstractLLMProvider(ABC):
    """Abstract interface for an LLM provider."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider human-readable name."""

    @abstractmethod
    async def infer(self, prompt: str, **kwargs: Any) -> str:
        """Run inference and return the text response."""

    @abstractmethod
    def estimate_cost(self, prompt: str, **kwargs: Any) -> float:
        """Return the estimated cost in USD for the given prompt."""


class ModelRouter:
    """Routes prompts to frugal or deep providers based on complexity, balance, and burn rate."""

    def __init__(
        self,
        *,
        frugal_provider: AbstractLLMProvider | None = None,
        deep_provider: AbstractLLMProvider | None = None,
        meta_cognition: MetaCognition | None = None,
        deep_complexity_threshold: float = 0.7,
    ) -> None:
        self._frugal = frugal_provider
        self._deep = deep_provider
        self._meta = meta_cognition or MetaCognition()
        self._deep_complexity_threshold = deep_complexity_threshold

    async def route(
        self,
        prompt: str,
        complexity_score: float,
        balance: float,
        burn_rate: float,
    ) -> RoutingResult:
        """Select the appropriate provider for a prompt.

        High complexity + high balance + low burn = deep mode.
        Everything else = frugal mode.

        Args:
            prompt: The inference prompt.
            complexity_score: 0-1 scalar representing estimated reasoning difficulty.
            balance: Current realised balance in USD.
            burn_rate: Current daily burn in USD.

        Returns:
            A :class:`RoutingResult` describing the chosen provider.
        """
        if not 0.0 <= complexity_score <= 1.0:
            raise ValueError("complexity_score must be in [0, 1]")

        use_deep = False

        if self._deep is not None:
            # Deep mode when complexity is high, balance covers burn buffer,
            # and burn is low relative to balance.
            if complexity_score >= self._deep_complexity_threshold:
                if balance > burn_rate * 7 and burn_rate < balance * 0.1:
                    use_deep = True

            # Also allow deep mode for very high balance with negligible burn,
            # even if complexity is moderate.
            if not use_deep and balance > 1000 and burn_rate < balance * 0.01:
                use_deep = True

        if use_deep and self._deep is not None:
            cost = self._deep.estimate_cost(prompt)
            return RoutingResult(
                provider_name=self._deep.name,
                model_name="deep",
                estimated_cost=cost,
                mode="deep",
                metadata={
                    "complexity_score": complexity_score,
                    "balance": balance,
                    "burn_rate": burn_rate,
                },
            )

        if self._frugal is not None:
            cost = self._frugal.estimate_cost(prompt)
            return RoutingResult(
                provider_name=self._frugal.name,
                model_name="frugal",
                estimated_cost=cost,
                mode="frugal",
                metadata={
                    "complexity_score": complexity_score,
                    "balance": balance,
                    "burn_rate": burn_rate,
                },
            )

        # Fallback: no providers configured
        return RoutingResult(
            provider_name="none",
            model_name="none",
            estimated_cost=0.0,
            mode="frugal",
            metadata={
                "reason": "no_provider_configured",
                "complexity_score": complexity_score,
                "balance": balance,
                "burn_rate": burn_rate,
            },
        )
