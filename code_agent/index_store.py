from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class CodeIndex:
    def __init__(self, index_path: str | Path):
        self.index_path = Path(index_path)
        self.data = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.project_root = Path(self.data["project_root"])
        self.symbols: list[dict[str, Any]] = self.data.get("symbols", [])
        self.by_id = {symbol["symbol_id"]: symbol for symbol in self.symbols}

    def search_symbols(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []

        scored: list[tuple[float, dict[str, Any]]] = []

        for symbol in self.symbols:
            search_text = symbol.get("search_text", "")
            name = symbol.get("name", "").lower()
            qualified_name = symbol.get("qualified_name", "").lower()
            file_path = symbol.get("file_path", "").lower()

            score = 0.0

            for token in query_tokens:
                if token == name:
                    score += 8
                elif token in name:
                    score += 5

                if token in qualified_name:
                    score += 3

                if token in file_path:
                    score += 2

                if token in search_text:
                    score += 1

            if query.lower() in search_text:
                score += 3

            if score > 0:
                scored.append((score, symbol))

        scored.sort(key=lambda item: item[0], reverse=True)
        result = []

        for score, symbol in scored[:limit]:
            result.append(
                {
                    "symbol_id": symbol["symbol_id"],
                    "name": symbol["name"],
                    "kind": symbol["kind"],
                    "file_path": symbol["file_path"],
                    "line_start": symbol["line_start"],
                    "line_end": symbol["line_end"],
                    "qualified_name": symbol.get("qualified_name"),
                    "arguments": symbol.get("arguments", []),
                    "class_name": symbol.get("class_name"),
                    "signature": symbol["signature"],
                    "docstring": symbol["docstring"],
                    "score": round(score, 3),
                }
            )

        return result

    def get_symbol_details(self, symbol_id: str, include_body: bool = True) -> dict[str, Any]:
        symbol = self.by_id.get(symbol_id)
        if not symbol:
            return {"error": f"Symbol not found: {symbol_id}"}

        result = dict(symbol)
        result.pop("search_text", None)

        if include_body:
            result["body"] = self._read_symbol_body(symbol)

        return result


    def search_project_text(self, query: str, limit: int = 10, context_lines: int = 2) -> list[dict[str, Any]]:
        """Search plain text in project files.

        This complements symbol search and is useful for routes, dependencies,
        configuration files and constants that are not represented as symbols.
        """
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []

        matches: list[tuple[float, dict[str, Any]]] = []
        for path in self._iter_searchable_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            lines = text.splitlines()
            lower_lines = [line.lower() for line in lines]
            relative_path = self._relative_to_project(path)
            lower_path = relative_path.lower()

            for idx, line in enumerate(lower_lines):
                score = 0.0
                for token in query_tokens:
                    if token in line:
                        score += 3
                    if token in lower_path:
                        score += 2
                if query.lower() in line:
                    score += 5
                if score <= 0:
                    continue

                start = max(0, idx - context_lines)
                end = min(len(lines), idx + context_lines + 1)
                snippet = "\n".join(f"{line_no + 1}: {lines[line_no]}" for line_no in range(start, end))
                matches.append(
                    (
                        score,
                        {
                            "file_path": relative_path,
                            "line_start": start + 1,
                            "line_end": end,
                            "snippet": snippet,
                            "score": round(score, 3),
                        },
                    )
                )

        matches.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in matches[:limit]]

    def read_file_range(self, file_path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        """Read a small range of a project file by relative file path."""
        resolved = self._resolve_relative_project_file(file_path)
        if not resolved:
            return {"error": f"File not found in project: {file_path}"}

        try:
            lines = resolved.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = resolved.read_text(encoding="utf-8", errors="ignore").splitlines()

        start = max(int(start_line or 1), 1)
        end = int(end_line) if end_line is not None else min(start + 80 - 1, len(lines))
        end = min(max(end, start), len(lines))
        snippet = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, end))
        return {
            "file_path": self._relative_to_project(resolved),
            "line_start": start,
            "line_end": end,
            "content": snippet,
        }

    def _iter_searchable_files(self) -> list[Path]:
        root = self.project_root if self.project_root.exists() else self.index_path.parent
        allowed_suffixes = {
            ".py", ".json", ".yaml", ".yml", ".toml", ".ini", ".env", ".md", ".txt",
            ".cfg", ".conf",
        }
        ignored_dirs = {".git", ".venv", "venv", "env", "__pycache__", "node_modules", ".idea", ".vscode"}
        result: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in ignored_dirs for part in path.parts):
                continue
            if path.suffix.lower() not in allowed_suffixes and path.name != ".env":
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
            except OSError:
                continue
            result.append(path)
        return result

    def _resolve_relative_project_file(self, file_path: str) -> Path | None:
        relative = Path(file_path)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        candidates = [
            self.project_root / relative,
            self.index_path.parent / relative,
            self.index_path.parent / self.project_root.name / relative,
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _relative_to_project(self, path: Path) -> str:
        try:
            return path.relative_to(self.project_root).as_posix()
        except Exception:
            try:
                return path.relative_to(self.index_path.parent).as_posix()
            except Exception:
                return path.as_posix()

    def _read_symbol_body(self, symbol: dict[str, Any]) -> str:
        file_path = self._resolve_symbol_file(symbol)
        if not file_path:
            return ""

        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except FileNotFoundError:
            return ""

        start = max(symbol["line_start"] - 1, 0)
        end = symbol["line_end"]
        return "\n".join(lines[start:end])

    def _resolve_symbol_file(self, symbol: dict[str, Any]) -> Path | None:
        """Resolve symbol file path even if the project folder was moved."""
        relative_path = Path(symbol["file_path"])
        candidates = [
            self.project_root / relative_path,
            self.index_path.parent / relative_path,
            self.index_path.parent / self.project_root.name / relative_path,
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        return None

    def _tokens(self, text: str) -> list[str]:
        return [token.lower() for token in re.findall(r"[a-zA-Zа-яА-Я0-9_]+", text)]
