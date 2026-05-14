from __future__ import annotations

import ast
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable


SKIP_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
}


@dataclass
class CodeSymbol:
    symbol_id: str
    kind: str
    name: str
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    signature: str
    arguments: list[str]
    decorators: list[str]
    docstring: str
    class_name: str | None
    search_text: str


class ProjectIndexer:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def build(self) -> dict[str, Any]:
        symbols: list[CodeSymbol] = []

        for file_path in self._iter_python_files():
            symbols.extend(self._index_file(file_path))

        return {
            "project_root": str(self.project_root),
            "language": "python",
            "symbols": [asdict(symbol) for symbol in symbols],
        }

    def save(self, output_path: str | Path) -> None:
        index_data = self.build()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(index_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _iter_python_files(self) -> Iterable[Path]:
        for path in self.project_root.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            yield path

    def _index_file(self, file_path: Path) -> list[CodeSymbol]:
        try:
            source = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = file_path.read_text(encoding="utf-8", errors="ignore")

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        relative_path = file_path.relative_to(self.project_root).as_posix()
        module_name = relative_path.removesuffix(".py").replace("/", ".")

        symbols: list[CodeSymbol] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbols.append(self._class_symbol(node, module_name, relative_path))

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent_class = self._find_parent_class(tree, node)
                symbols.append(
                    self._function_symbol(
                        node=node,
                        module_name=module_name,
                        relative_path=relative_path,
                        parent_class=parent_class,
                    )
                )

        return symbols

    def _class_symbol(self, node: ast.ClassDef, module_name: str, relative_path: str) -> CodeSymbol:
        qualified_name = f"{module_name}.{node.name}"
        bases = [self._safe_unparse(base) for base in node.bases]
        decorators = [self._safe_unparse(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node) or ""
        line_end = getattr(node, "end_lineno", node.lineno)

        search_text = self._make_search_text(
            [
                node.name,
                qualified_name,
                "class",
                " ".join(bases),
                " ".join(decorators),
                docstring,
                relative_path,
            ]
        )

        return CodeSymbol(
            symbol_id=qualified_name,
            kind="class",
            name=node.name,
            qualified_name=qualified_name,
            file_path=relative_path,
            line_start=node.lineno,
            line_end=line_end,
            signature=f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}",
            arguments=[],
            decorators=decorators,
            docstring=docstring,
            class_name=None,
            search_text=search_text,
        )

    def _function_symbol(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        module_name: str,
        relative_path: str,
        parent_class: str | None,
    ) -> CodeSymbol:
        prefix = f"{module_name}.{parent_class}" if parent_class else module_name
        qualified_name = f"{prefix}.{node.name}"
        decorators = [self._safe_unparse(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node) or ""
        arguments = self._extract_arguments(node.args)
        signature = self._build_signature(node)
        line_end = getattr(node, "end_lineno", node.lineno)

        kind = "method" if parent_class else "function"

        search_text = self._make_search_text(
            [
                node.name,
                qualified_name,
                kind,
                signature,
                " ".join(arguments),
                " ".join(decorators),
                docstring,
                parent_class or "",
                relative_path,
            ]
        )

        return CodeSymbol(
            symbol_id=qualified_name,
            kind=kind,
            name=node.name,
            qualified_name=qualified_name,
            file_path=relative_path,
            line_start=node.lineno,
            line_end=line_end,
            signature=signature,
            arguments=arguments,
            decorators=decorators,
            docstring=docstring,
            class_name=parent_class,
            search_text=search_text,
        )

    def _extract_arguments(self, args: ast.arguments) -> list[str]:
        result: list[str] = []
        all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]

        for arg in all_args:
            if arg.arg not in {"self", "cls"}:
                result.append(arg.arg)

        if args.vararg:
            result.append(f"*{args.vararg.arg}")

        if args.kwarg:
            result.append(f"**{args.kwarg.arg}")

        return result

    def _build_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        parts: list[str] = []

        all_args = [*node.args.posonlyargs, *node.args.args]
        defaults = [None] * (len(all_args) - len(node.args.defaults)) + list(node.args.defaults)

        for arg, default in zip(all_args, defaults):
            parts.append(self._format_arg(arg, default))

        if node.args.vararg:
            parts.append(f"*{self._format_arg(node.args.vararg, None)}")
        elif node.args.kwonlyargs:
            parts.append("*")

        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            parts.append(self._format_arg(arg, default))

        if node.args.kwarg:
            parts.append(f"**{self._format_arg(node.args.kwarg, None)}")

        returns = ""
        if node.returns:
            returns = f" -> {self._safe_unparse(node.returns)}"

        return f"{prefix} {node.name}({', '.join(parts)}){returns}"

    def _format_arg(self, arg: ast.arg, default: ast.expr | None) -> str:
        text = arg.arg
        if arg.annotation:
            text += f": {self._safe_unparse(arg.annotation)}"
        if default is not None:
            text += f" = {self._safe_unparse(default)}"
        return text

    def _find_parent_class(self, tree: ast.AST, target: ast.AST) -> str | None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if child is target:
                        return node.name
        return None

    def _safe_unparse(self, node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return ""

    def _make_search_text(self, parts: list[str]) -> str:
        return " ".join(part for part in parts if part).lower()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build code symbol index for a Python project.")
    parser.add_argument("project_root", help="Path to Python project")
    parser.add_argument("--output", default="code_index.json", help="Output JSON path")
    args = parser.parse_args()

    indexer = ProjectIndexer(args.project_root)
    indexer.save(args.output)
    print(f"Index saved to {args.output}")


if __name__ == "__main__":
    main()
