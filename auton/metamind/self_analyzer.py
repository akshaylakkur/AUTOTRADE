"""Self Analyzer: ÆON's ability to 'know thyself' by parsing its own source code."""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

from auton.metamind.dataclasses import ClassInfo, FunctionInfo, ModuleInfo, SourceMap

logger = logging.getLogger(__name__)


class SelfAnalyzer:
    """Parses its own Python source code to understand the codebase architecture."""

    def __init__(self) -> None:
        self._source_dir: Path | None = None
        self._modules: dict[str, ModuleInfo] = {}
        self._dependencies: dict[str, list[str]] = {}

    def analyze_source_tree(self, source_dir: Path | str) -> SourceMap:
        """Walk *source_dir* and parse every ``.py`` file using the ``ast`` module."""
        source_path = Path(source_dir)
        self._source_dir = source_path
        self._modules.clear()
        self._dependencies.clear()

        for py_file in sorted(source_path.rglob("*.py")):
            relative = py_file.relative_to(source_path)
            module_name = str(relative.with_suffix("")).replace("/", ".")
            try:
                self._modules[module_name] = self._parse_module(py_file)
            except SyntaxError as exc:
                logger.warning("Syntax error in %s: %s", py_file, exc)

        self._build_dependency_graph()
        return self.get_source_map()

    def _parse_module(self, path: Path) -> ModuleInfo:
        """Parse a single Python file into a :class:`ModuleInfo`."""
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, str(path))

        classes: list[ClassInfo] = []
        functions: list[FunctionInfo] = []
        imports: list[str] = []
        todos: list[str] = []
        total_complexity = 0

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                imports.append(mod)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                line = node.value
                if "TODO" in line or "FIXME" in line:
                    todos.append(line)

        for top in ast.iter_child_nodes(tree):
            if isinstance(top, ast.ClassDef):
                classes.append(self._parse_class(top))
            elif isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(self._parse_function(top))

        for func in functions:
            total_complexity += func.complexity
        for cls in classes:
            for method in cls.methods:
                total_complexity += method.complexity

        return ModuleInfo(
            path=path,
            classes=classes,
            functions=functions,
            imports=imports,
            todos=todos,
            complexity=float(total_complexity),
        )

    def _parse_class(self, node: ast.ClassDef) -> ClassInfo:
        """Extract information from a class definition."""
        methods: list[FunctionInfo] = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(self._parse_function(child))
        bases = [self._name(base) for base in node.bases]
        return ClassInfo(
            name=node.name,
            methods=methods,
            bases=bases,
            line_number=node.lineno,
        )

    def _parse_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionInfo:
        """Extract information from a function / method definition."""
        doc = ast.get_docstring(node)
        complexity = self._cyclomatic_complexity(node)
        calls: list[str] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                calls.append(self._name(child.func))
        return FunctionInfo(
            name=node.name,
            line_number=node.lineno,
            complexity=complexity,
            docstring=doc,
            calls=calls,
            is_async=isinstance(node, ast.AsyncFunctionDef),
        )

    @staticmethod
    def _cyclomatic_complexity(node: ast.AST) -> int:
        """Rough cyclomatic complexity: 1 + number of branch nodes."""
        branches = (
            ast.If,
            ast.While,
            ast.For,
            ast.ExceptHandler,
            ast.With,
            ast.Assert,
            ast.comprehension,
        )
        return 1 + sum(1 for child in ast.walk(node) if isinstance(child, branches))

    @staticmethod
    def _name(node: ast.AST) -> str:
        """Best-effort name extraction from an AST node."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{SelfAnalyzer._name(node.value)}.{node.attr}"
        if isinstance(node, ast.Call):
            return SelfAnalyzer._name(node.func)
        if isinstance(node, ast.Subscript):
            return SelfAnalyzer._name(node.value)
        return ""

    def build_dependency_graph(self) -> dict[str, list[str]]:
        """Map imports and function calls between modules."""
        self._build_dependency_graph()
        return dict(self._dependencies)

    def _build_dependency_graph(self) -> None:
        if not self._source_dir:
            return
        for mod_name, mod_info in self._modules.items():
            deps: list[str] = []
            for imp in mod_info.imports:
                for other in self._modules:
                    if imp.startswith(other) and other != mod_name:
                        deps.append(other)
            self._dependencies[mod_name] = sorted(set(deps))

    def identify_bottlenecks(self, complexity_threshold: int = 10) -> list[dict[str, Any]]:
        """Find functions with high complexity, long execution paths, or TODO markers."""
        results: list[dict[str, Any]] = []
        for mod_name, mod_info in self._modules.items():
            if mod_info.complexity > complexity_threshold:
                results.append(
                    {
                        "type": "module_complexity",
                        "module": mod_name,
                        "complexity": mod_info.complexity,
                        "path": str(mod_info.path),
                    }
                )
            for func in mod_info.functions:
                if func.complexity > complexity_threshold:
                    results.append(
                        {
                            "type": "function_complexity",
                            "module": mod_name,
                            "function": func.name,
                            "complexity": func.complexity,
                            "path": str(mod_info.path),
                        }
                    )
            for todo in mod_info.todos:
                results.append(
                    {
                        "type": "todo",
                        "module": mod_name,
                        "message": todo,
                        "path": str(mod_info.path),
                    }
                )
        return sorted(results, key=lambda x: x.get("complexity", 0), reverse=True)

    def find_missing_capabilities(
        self, capability_checklist: list[str] | None = None
    ) -> list[str]:
        """Compare existing modules against a capability checklist and identify gaps."""
        default_checklist = [
            "core",
            "ledger",
            "security",
            "senses",
            "limbs",
            "cortex",
            "reflexes",
            "metamind",
        ]
        checklist = capability_checklist or default_checklist
        present = set()
        for name in self._modules:
            parts = name.split(".")
            present.update(parts)
        return [cap for cap in checklist if cap not in present]

    def get_source_map(self) -> SourceMap:
        """Return a structured representation of the entire codebase."""
        entry_points: list[str] = []
        for name, mod in self._modules.items():
            if "__main__" in [f.name for f in mod.functions]:
                entry_points.append(name)
            else:
                for node in ast.walk(ast.parse(mod.path.read_text(encoding="utf-8"), str(mod.path))):
                    if (
                        isinstance(node, ast.If)
                        and isinstance(node.test, ast.Compare)
                        and isinstance(node.test.left, ast.Name)
                        and node.test.left.id == "__name__"
                    ):
                        for comparator in node.test.comparators:
                            if isinstance(comparator, ast.Constant) and comparator.value == "__main__":
                                entry_points.append(name)
                                break
        return SourceMap(
            modules=dict(self._modules),
            dependencies=dict(self._dependencies),
            entry_points=sorted(set(entry_points)),
        )
