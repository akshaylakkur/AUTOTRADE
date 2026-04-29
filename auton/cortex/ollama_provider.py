"""Ollama LLM provider — implements both cortex and metamind interfaces."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Any

import httpx

from auton.cortex.model_router import AbstractLLMProvider
from auton.metamind.code_generator import LLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(AbstractLLMProvider, LLMProvider):
    """Concrete LLM provider backed by a local Ollama server.

    Implements both the async cortex interface (``AbstractLLMProvider``)
    and the sync metamind interface (``LLMProvider``) so a single instance
    can be wired into the ModelRouter *and* the SelfModificationEngine.
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llama3.2",
        timeout: float = 120.0,
    ) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    # ------------------------------------------------------------------
    # AbstractLLMProvider (async cortex interface)
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return f"ollama/{self._model}"

    async def infer(self, prompt: str, **kwargs: Any) -> str:
        """Run async inference against Ollama ``/api/generate``."""
        url = f"{self._host}/api/generate"
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            **kwargs,
        }
        logger.debug("Ollama infer: model=%s prompt_len=%d", self._model, len(prompt))
        resp = await self._http.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")

    def estimate_cost(self, prompt: str, **kwargs: Any) -> float:
        """Ollama runs locally — cost is always zero."""
        return 0.0

    # ------------------------------------------------------------------
    # LLMProvider (sync metamind interface)
    # ------------------------------------------------------------------

    def complete(self, prompt: str) -> str:
        """Synchronous completion via stdlib urllib (thread-safe, no event loop)."""
        url = f"{self._host}/api/generate"
        payload = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        logger.debug("Ollama complete: model=%s prompt_len=%d", self._model, len(prompt))
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read())
        return data.get("response", "")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._http.aclose()

    async def health_check(self) -> bool:
        """Return True if the Ollama server is reachable and the model is loaded."""
        try:
            resp = await self._http.get(f"{self._host}/api/tags", timeout=5.0)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return any(m.get("name", "").startswith(self._model) for m in models)
        except Exception:
            return False
