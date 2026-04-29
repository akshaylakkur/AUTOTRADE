"""AST-based code introspection for the auton/ tree."""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auton.metamind.dataclasses import ClassInfo, FunctionInfo, ModuleInfo, SourceMap

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FunctionLocation:
    """Location of a function definition."""

    module_path: str
    class_name: str | None
    function_name: str
    line_number: int


@dataclass(frozen=True)
class ClassLocation:
    """Location of a class definition."""

    module_path: str
    class_name: str
    line_number: int
    bases: list[str]


@dataclass(frozen=True)
class CallerInfo:
    """Information about a caller of a target function/class."""

    module_path: str
    caller_name: str
    line_number: int
    is_method: bool


@dataclass(frozen=True)
class ModuleComplexity:
    """Complexity metrics for a module."""

    module_path: str
    function_complexities: dict[str, int] = field(default_factory=dict)
    average: float = 0.0
    total: int = 0


class CodeIntrospector:
    """AST-based analysis of the auton/ tree."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self._source_map: SourceMap | None = None
        self._cache_mtime: dict[str, float] = {}

    def _parse_file(self, path: Path) -> ast.AST | None:
        try:
            return ast.parse(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError as exc:
            logger.warning("Syntax error in %s: %s", path, exc)
            return None

    def build_source_map(self, package: str = "auton") -> SourceMap:
        """Recursively parse every .py file under *package* and return SourceMap."""
        root = self.project_root / package.replace(".", "/")
        modules: dict[str, ModuleInfo] = {}
        dependencies: dict[str, list[str]] = {}
        entry_points: list[str] = []

        if not root.exists():
            return SourceMap()

        for py_file in sorted(root.rglob("*.py")):
            relative = py_file.relative_to(self.project_root)
            module_name = str(relative.with_suffix("")).replace("/", ".")
            try:
                tree = self._parse_file(py_file)
                if tree is None:
                    continue
                info = self._walk_module(tree, py_file)
                modules[module_name] = info
                self._cache_mtime[str(py_file)] = py_file.stat().st_mtime

                if self._has_main_guard(tree):
                    entry_points.append(module_name)

            except Exception as exc:
                logger.warning("Failed to parse %s: %s", py_file, exc)

        for mod_name, mod_info in modules.items():
            deps: list[str] = []
            for imp in mod_info.imports:
                for other in modules:
                    if imp.startswith(other) and other != mod_name:
                        deps.append(other)
            dependencies[mod_name] = sorted(set(deps))

        self._source_map = SourceMap(
            modules=modules,
            dependencies=dependencies,
            entry_points=sorted(set(entry_points)),
        )
        return self._source_map

    def _walk_module(self, tree: ast.AST, path: Path) -> ModuleInfo:
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
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{CodeIntrospector._name(node.value)}.{node.attr}"
        if isinstance(node, ast.Call):
            return CodeIntrospector._name(node.func)
        if isinstance(node, ast.Subscript):
            return CodeIntrospector._name(node.value)
        return ""

    @staticmethod
    def _has_main_guard(tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
            ):
                for comparator in node.test.comparators:
                    if isinstance(comparator, ast.Constant) and comparator.value == "__main__":
                        return True
        return False

    def _ensure_source_map(self) -> SourceMap:
        if self._source_map is None:
            self.build_source_map()
        assert self._source_map is not None
        return self._source_map

    def locate_function(self, function_name: str) -> list[FunctionLocation]:
        """Answer 'where is function Y defined?'"""
        sm = self._ensure_source_map()
        results: list[FunctionLocation] = []
        for mod_name, mod_info in sm.modules.items():
            for func in mod_info.functions:
                if func.name == function_name:
                    results.append(
                        FunctionLocation(
                            module_path=mod_name,
                            class_name=None,
                            function_name=func.name,
                            line_number=func.line_number,
                        )
                    )
            for cls in mod_info.classes:
                for method in cls.methods:
                    if method.name == function_name:
                        results.append(
                            FunctionLocation(
                                module_path=mod_name,
                                class_name=cls.name,
                                function_name=method.name,
                                line_number=method.line_number,
                            )
                        )
        return results

    def locate_class(self, class_name: str) -> list[ClassLocation]:
        """Answer 'where is class X defined?'"""
        sm = self._ensure_source_map()
        results: list[ClassLocation] = []
        for mod_name, mod_info in sm.modules.items():
            for cls in mod_info.classes:
                if cls.name == class_name:
                    results.append(
                        ClassLocation(
                            module_path=mod_name,
                            class_name=cls.name,
                            line_number=cls.line_number,
                            bases=cls.bases,
                        )
                    )
        return results

    def describe_module(self, module_path: str) -> ModuleInfo:
        """Answer 'what does module X do?' Returns classes, functions, imports, docstrings."""
        sm = self._ensure_source_map()
        if module_path not in sm.modules:
            raise IntrospectionError(f"Module '{module_path}' not found in source map")
        return sm.modules[module_path]

    def extract_dependencies(self, module_path: str) -> list[str]:
        """Return all top-level and relative imports for a module."""
        info = self.describe_module(module_path)
        return list(info.imports)

    def compute_complexity(self, module_path: str) -> ModuleComplexity:
        """Compute cyclomatic complexity per function and module average."""
        info = self.describe_module(module_path)
        func_complexities: dict[str, int] = {}
        total = 0
        for func in info.functions:
            func_complexities[func.name] = func.complexity
            total += func.complexity
        for cls in info.classes:
            for method in cls.methods:
                key = f"{cls.name}.{method.name}"
                func_complexities[key] = method.complexity
                total += method.complexity

        count = len(func_complexities)
        average = total / count if count else 0.0
        return ModuleComplexity(
            module_path=module_path,
            function_complexities=func_complexities,
            average=round(average, 2),
            total=total,
        )

    def find_callers(self, target: str, package: str = "auton") -> list[CallerInfo]:
        """Reverse-dependency: who calls function/class *target*?"""
        sm = self._ensure_source_map()
        results: list[CallerInfo] = []
        for mod_name, mod_info in sm.modules.items():
            for func in mod_info.functions:
                if target in func.calls:
                    results.append(
                        CallerInfo(
                            module_path=mod_name,
                            caller_name=func.name,
                            line_number=func.line_number,
                            is_method=False,
                        )
                    )
            for cls in mod_info.classes:
                for method in cls.methods:
                    if target in method.calls:
                        results.append(
                            CallerInfo(
                                module_path=mod_name,
                                caller_name=f"{cls.name}.{method.name}",
                                line_number=method.line_number,
                                is_method=True,
                            )
                        )
        return results


class SelfModificationError(Exception):
    """Base exception for self-modification errors."""


class IntrospectionError(SelfModificationError):
    """Error during code introspection."""


class ParseError(IntrospectionError):
    """Failed to parse a Python file."""
