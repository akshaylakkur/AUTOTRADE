"""Code Generator: produces new code to improve the system."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auton.metamind.dataclasses import GeneratedCode

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Mockable interface for LLM completion."""

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Return the LLM completion for *prompt*."""
        ...


class CodeGenerator:
    """Generates new code to improve the system."""

    def __init__(
        self,
        llm: LLMProvider,
        mutation_dir: Path | str = Path("mutations"),
    ) -> None:
        self.llm = llm
        self.mutation_dir = Path(mutation_dir)
        self.mutation_dir.mkdir(parents=True, exist_ok=True)

    def generate_module(
        self,
        module_name: str,
        requirements: list[str],
        context: dict[str, Any],
    ) -> GeneratedCode:
        """Produce Python code for a new module."""
        prompt = self._build_module_prompt(module_name, requirements, context)
        source, cost = self._call_llm(prompt)
        timestamp = datetime.now(timezone.utc)
        file_name = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_{module_name}.py"
        mutation_path = self.mutation_dir / file_name
        mutation_path.write_text(source, encoding="utf-8")
        return GeneratedCode(
            module_name=module_name,
            source=source,
            requirements=list(requirements),
            context=dict(context),
            cost=cost,
            timestamp=timestamp,
            mutation_path=mutation_path,
        )

    def optimize_function(
        self,
        function_source: str,
        optimization_goal: str,
    ) -> GeneratedCode:
        """Rewrite a function for speed or readability."""
        prompt = self._build_optimization_prompt(function_source, optimization_goal)
        source, cost = self._call_llm(prompt)
        timestamp = datetime.now(timezone.utc)
        file_name = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_optimized.py"
        mutation_path = self.mutation_dir / file_name
        mutation_path.write_text(source, encoding="utf-8")
        return GeneratedCode(
            module_name="optimized_function",
            source=source,
            requirements=[optimization_goal],
            cost=cost,
            timestamp=timestamp,
            mutation_path=mutation_path,
        )

    def generate_connector(
        self,
        exchange_name: str,
        api_docs_summary: str,
    ) -> GeneratedCode:
        """Generate a new senses / limbs connector."""
        prompt = self._build_connector_prompt(exchange_name, api_docs_summary)
        source, cost = self._call_llm(prompt)
        timestamp = datetime.now(timezone.utc)
        file_name = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_connector_{exchange_name.lower()}.py"
        mutation_path = self.mutation_dir / file_name
        mutation_path.write_text(source, encoding="utf-8")
        return GeneratedCode(
            module_name=f"connector_{exchange_name.lower()}",
            source=source,
            requirements=["api_client", exchange_name],
            context={"api_docs_summary": api_docs_summary},
            cost=cost,
            timestamp=timestamp,
            mutation_path=mutation_path,
        )

    def generate_fastapi_app(
        self,
        app_name: str,
        endpoints: list[dict[str, Any]],
        models: list[dict[str, Any]] | None = None,
    ) -> GeneratedCode:
        """Generate a complete FastAPI backend application."""
        prompt = self._build_fastapi_prompt(app_name, endpoints, models)
        source, cost = self._call_llm(prompt)
        timestamp = datetime.now(timezone.utc)
        file_name = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_fastapi_{app_name.lower()}.py"
        mutation_path = self.mutation_dir / file_name
        mutation_path.write_text(source, encoding="utf-8")
        return GeneratedCode(
            module_name=f"fastapi_{app_name.lower()}",
            source=source,
            requirements=["fastapi", "uvicorn", "pydantic"],
            context={"app_name": app_name, "endpoints": endpoints, "models": models},
            cost=cost,
            timestamp=timestamp,
            mutation_path=mutation_path,
        )

    def generate_react_frontend(
        self,
        app_name: str,
        pages: list[str],
        api_base_url: str = "http://localhost:8000",
    ) -> GeneratedCode:
        """Generate a React frontend application."""
        prompt = self._build_react_prompt(app_name, pages, api_base_url)
        source, cost = self._call_llm(prompt)
        timestamp = datetime.now(timezone.utc)
        file_name = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_react_{app_name.lower()}.jsx"
        mutation_path = self.mutation_dir / file_name
        mutation_path.write_text(source, encoding="utf-8")
        return GeneratedCode(
            module_name=f"react_{app_name.lower()}",
            source=source,
            requirements=["react", "vite"],
            context={"app_name": app_name, "pages": pages, "api_base_url": api_base_url},
            cost=cost,
            timestamp=timestamp,
            mutation_path=mutation_path,
        )

    def generate_fullstack_app(
        self,
        app_name: str,
        endpoints: list[dict[str, Any]],
        pages: list[str],
        models: list[dict[str, Any]] | None = None,
    ) -> dict[str, GeneratedCode]:
        """Generate both FastAPI backend and React frontend for a SaaS product."""
        backend = self.generate_fastapi_app(app_name, endpoints, models)
        frontend = self.generate_react_frontend(
            app_name, pages, api_base_url=f"https://{app_name.lower()}.fly.dev"
        )
        return {"backend": backend, "frontend": frontend}

    def _call_llm(self, prompt: str) -> tuple[str, float]:
        """Invoke the LLM and return (completion, cost)."""
        completion = self.llm.complete(prompt)
        cost = self._estimate_cost(prompt, completion)
        return completion, cost

    @staticmethod
    def _estimate_cost(prompt: str, completion: str) -> float:
        """Rough token-cost estimation."""
        # Approximate 4 chars per token; $0.00001 per token
        tokens = (len(prompt) + len(completion)) / 4
        return round(tokens * 0.00001, 6)

    @staticmethod
    def _build_module_prompt(
        module_name: str, requirements: list[str], context: dict[str, Any]
    ) -> str:
        return (
            f"Generate a production-quality Python 3.12+ module named '{module_name}'.\n"
            f"Requirements:\n" + "\n".join(f"- {r}" for r in requirements) + "\n"
            f"Context:\n{context}\n"
            "Output only valid Python code with no markdown formatting."
        )

    @staticmethod
    def _build_optimization_prompt(function_source: str, optimization_goal: str) -> str:
        return (
            f"Optimize the following Python function for: {optimization_goal}\n"
            f"```python\n{function_source}\n```\n"
            "Output only the optimized function with no markdown formatting."
        )

    @staticmethod
    def _build_connector_prompt(exchange_name: str, api_docs_summary: str) -> str:
        return (
            f"Generate a Python 3.12+ client connector for '{exchange_name}'.\n"
            f"API summary: {api_docs_summary}\n"
            "The connector must inherit from a base connector class, implement "
            "connect(), authenticate(), and fetch_data() methods, "
            "and handle retries and rate limits safely.\n"
            "Output only valid Python code with no markdown formatting."
        )

    @staticmethod
    def _build_fastapi_prompt(
        app_name: str,
        endpoints: list[dict[str, Any]],
        models: list[dict[str, Any]] | None,
    ) -> str:
        models_str = json.dumps(models) if models else "[]"
        endpoints_str = json.dumps(endpoints)
        return (
            f"Generate a production-quality FastAPI application named '{app_name}'.\n"
            f"Models: {models_str}\n"
            f"Endpoints: {endpoints_str}\n"
            "Requirements:\n"
            "- Use Pydantic v2 models for request/response validation\n"
            "- Include health check endpoint at /health\n"
            "- Include OpenAPI docs at /docs\n"
            "- Use async route handlers where appropriate\n"
            "- Include proper error handling with HTTPException\n"
            "- No hardcoded secrets\n"
            "Output only valid Python code with no markdown formatting."
        )

    @staticmethod
    def _build_react_prompt(
        app_name: str,
        pages: list[str],
        api_base_url: str,
    ) -> str:
        return (
            f"Generate a React frontend application named '{app_name}'.\n"
            f"Pages: {', '.join(pages)}\n"
            f"API base URL: {api_base_url}\n"
            "Requirements:\n"
            "- Use functional components with hooks\n"
            "- Use fetch or axios for API calls\n"
            "- Include React Router for navigation\n"
            "- Include basic CSS styling\n"
            "- No hardcoded secrets\n"
            "Output only valid JSX/JavaScript code with no markdown formatting."
        )
