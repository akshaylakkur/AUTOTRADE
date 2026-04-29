"""Template + LLM hybrid generator for new Python modules."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auton.metamind.code_generator import LLMProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModuleSpecification:
    """Structured specification for a new module."""

    module_name: str
    module_type: str  # "exchange_connector", "data_source", "commerce", "saas", "generic"
    requirements: list[str] = field(default_factory=list)
    interface_contract: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    target_path: Path | None = None


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


class GenerationError(Exception):
    """Error during code generation."""


class TemplateNotFoundError(GenerationError):
    """Requested template does not exist."""


class ModuleGenerator:
    """Template + LLM hybrid generator for new Python modules."""

    def __init__(
        self,
        llm: LLMProvider,
        template_dir: Path | None = None,
        mutation_dir: Path | None = None,
    ) -> None:
        self.llm = llm
        self.template_dir = template_dir or Path(__file__).with_suffix("").parent / "templates"
        self.mutation_dir = Path(mutation_dir) if mutation_dir else Path("mutations")
        self.mutation_dir.mkdir(parents=True, exist_ok=True)

    def _load_template(self, template_name: str) -> str:
        path = self.template_dir / f"{template_name}.py.tpl"
        if not path.exists():
            raise TemplateNotFoundError(f"Template not found: {path}")
        return path.read_text(encoding="utf-8")

    def _hydrate_template(self, template: str, variables: dict[str, Any]) -> str:
        result = template
        for key, value in variables.items():
            placeholder = f"{{{{ {key} }}}}"
            if isinstance(value, list):
                str_value = ", ".join(str(v) for v in value)
            elif isinstance(value, dict):
                str_value = str(value)
            else:
                str_value = str(value)
            result = result.replace(placeholder, str_value)
        return result

    def _call_llm_for_infills(self, stub_code: str, spec: ModuleSpecification) -> str:
        prompt = (
            f"Complete the following Python module.\n"
            f"Module name: {spec.module_name}\n"
            f"Type: {spec.module_type}\n"
            f"Requirements:\n" + "\n".join(f"- {r}" for r in spec.requirements) + "\n"
            f"Interface contract: {spec.interface_contract}\n"
            f"Context: {spec.context}\n"
            f"Stub code:\n```python\n{stub_code}\n```\n"
            "Fill in all TODO implementations. Output only valid Python code."
        )
        completion = self.llm.complete(prompt)
        cost = self._estimate_cost(prompt, completion)
        return completion, cost

    @staticmethod
    def _estimate_cost(prompt: str, completion: str) -> float:
        tokens = (len(prompt) + len(completion)) / 4
        return round(tokens * 0.00001, 6)

    def generate_from_spec(self, spec: ModuleSpecification) -> GeneratedCode:
        """Generate a module from a structured specification."""
        template_name = spec.module_type if spec.module_type != "generic" else "module"
        try:
            template = self._load_template(template_name)
        except TemplateNotFoundError:
            logger.warning("Template %s not found, falling back to generic", template_name)
            template = self._load_template("module")

        variables = {
            "module_name": spec.module_name,
            "class_name": self._to_class_name(spec.module_name),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **spec.context,
        }
        stub = self._hydrate_template(template, variables)
        source, cost = self._call_llm_for_infills(stub, spec)

        timestamp = datetime.now(timezone.utc)
        file_name = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_{spec.module_name}.py"
        mutation_path = self.mutation_dir / file_name
        mutation_path.write_text(source, encoding="utf-8")

        return GeneratedCode(
            module_name=spec.module_name,
            source=source,
            requirements=list(spec.requirements),
            context=dict(spec.context),
            cost=cost,
            timestamp=timestamp,
            mutation_path=mutation_path,
        )

    def generate_exchange_connector(
        self,
        exchange_name: str,
        api_docs_summary: str,
        required_methods: list[str],
    ) -> GeneratedCode:
        """Use the exchange_connector template."""
        spec = ModuleSpecification(
            module_name=f"connector_{exchange_name.lower()}",
            module_type="exchange_connector",
            requirements=["api_client", exchange_name],
            context={
                "exchange_name": exchange_name,
                "api_docs_summary": api_docs_summary,
                "required_methods": required_methods,
                "class_name": f"{exchange_name}Connector",
            },
        )
        return self.generate_from_spec(spec)

    def generate_data_source(
        self,
        source_name: str,
        protocol: str,
        schema_hints: dict[str, str],
    ) -> GeneratedCode:
        """Use the data_source template."""
        spec = ModuleSpecification(
            module_name=f"source_{source_name.lower()}",
            module_type="data_source",
            requirements=["data_ingestion", protocol],
            context={
                "source_name": source_name,
                "protocol": protocol,
                "schema_hints": schema_hints,
                "class_name": f"{source_name}Source",
            },
        )
        return self.generate_from_spec(spec)

    def generate_commerce_module(
        self,
        provider_name: str,
        integration_type: str,
    ) -> GeneratedCode:
        """Use the commerce_module template."""
        spec = ModuleSpecification(
            module_name=f"commerce_{provider_name.lower()}",
            module_type="commerce",
            requirements=["commerce", integration_type],
            context={
                "provider_name": provider_name,
                "integration_type": integration_type,
                "class_name": f"{provider_name}Commerce",
            },
        )
        return self.generate_from_spec(spec)

    def generate_saas_module(
        self,
        service_name: str,
        api_spec: dict[str, Any],
    ) -> GeneratedCode:
        """Use the saas_module template."""
        spec = ModuleSpecification(
            module_name=f"saas_{service_name.lower()}",
            module_type="saas",
            requirements=["saas", service_name],
            context={
                "service_name": service_name,
                "api_spec": api_spec,
                "class_name": f"{service_name}Client",
            },
        )
        return self.generate_from_spec(spec)

    def generate_fastapi_module(
        self,
        app_name: str,
        endpoints: list[dict[str, Any]],
        models: list[dict[str, Any]] | None = None,
    ) -> GeneratedCode:
        """Generate a FastAPI backend from template + LLM infills."""
        spec = ModuleSpecification(
            module_name=f"api_{app_name.lower()}",
            module_type="fastapi_app",
            requirements=["fastapi", "uvicorn", "pydantic"],
            context={
                "app_name": app_name,
                "endpoints": endpoints,
                "models": models or [],
                "class_name": f"{app_name}API",
            },
        )
        return self.generate_from_spec(spec)

    def generate_react_module(
        self,
        app_name: str,
        pages: list[str],
        api_base_url: str = "http://localhost:8000",
    ) -> GeneratedCode:
        """Generate a React frontend from template + LLM infills."""
        spec = ModuleSpecification(
            module_name=f"ui_{app_name.lower()}",
            module_type="react_app",
            requirements=["react", "vite"],
            context={
                "app_name": app_name,
                "pages": pages,
                "api_base_url": api_base_url,
                "class_name": f"{app_name}UI",
            },
        )
        return self.generate_from_spec(spec)

    @staticmethod
    def _to_class_name(module_name: str) -> str:
        """Convert snake_case module name to CamelCase class name."""
        return "".join(part.capitalize() for part in module_name.split("_"))
