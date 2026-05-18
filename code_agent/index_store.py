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
