from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceBundle:
    """Facts extracted from real tool results for one agent turn."""

    symbols: list[dict[str, Any]] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, str]] = field(default_factory=list)
    raises: list[dict[str, str]] = field(default_factory=list)
    config_mentions: list[str] = field(default_factory=list)
    text_matches: list[dict[str, Any]] = field(default_factory=list)
    file_ranges: list[dict[str, Any]] = field(default_factory=list)
    raw_tool_count: int = 0

    def has_facts(self) -> bool:
        return bool(self.symbols or self.details or self.calls or self.raises or self.config_mentions or self.text_matches or self.file_ranges)

    def to_prompt_text(self, max_body_chars: int = 1600) -> str:
        """Return compact text that can be passed to LLM for grounded final answer."""
        parts: list[str] = []

        if self.symbols:
            parts.append("Найденные символы из search_symbols:")
            for item in self.symbols[:30]:
                parts.append(
                    "- "
                    f"{item.get('qualified_name') or item.get('symbol_id') or item.get('name')} "
                    f"[{item.get('kind')}] "
                    f"file={item.get('file_path')} "
                    f"signature={item.get('signature')} "
                    f"docstring={item.get('docstring') or ''}"
                )

        if self.details:
            parts.append("\nПодробности по символам из get_symbol_details:")
            for item in self.details[:20]:
                body = (item.get("body") or "").strip()
                if len(body) > max_body_chars:
                    body = body[:max_body_chars] + "\n... <body truncated>"
                parts.append(
                    "- "
                    f"{item.get('qualified_name') or item.get('symbol_id') or item.get('name')} "
                    f"[{item.get('kind')}]\n"
                    f"  file: {item.get('file_path')}\n"
                    f"  signature: {item.get('signature')}\n"
                    f"  arguments: {item.get('arguments') or []}\n"
                    f"  docstring: {item.get('docstring') or ''}\n"
                    f"  body:\n{body if body else '<body is empty or unavailable>'}"
                )

        if self.calls:
            parts.append("\nПодтверждённые вызовы функций/методов:")
            for item in self.calls[:60]:
                parts.append(f"- {item['source']} -> {item['target']}")

        if self.raises:
            parts.append("\nПодтверждённые исключения:")
            for item in self.raises[:30]:
                parts.append(f"- {item['source']} raises {item['exception']}")

        if self.text_matches:
            parts.append("\nСовпадения текстового поиска по проекту:")
            for item in self.text_matches[:30]:
                parts.append(
                    f"- file={item.get('file_path')} lines={item.get('line_start')}-{item.get('line_end')} score={item.get('score')}\n"
                    f"{item.get('snippet') or ''}"
                )

        if self.file_ranges:
            parts.append("\nПрочитанные фрагменты файлов:")
            for item in self.file_ranges[:10]:
                content = item.get("content") or ""
                if len(content) > 1600:
                    content = content[:1600] + "\n... <file content truncated>"
                parts.append(
                    f"- file={item.get('file_path')} lines={item.get('line_start')}-{item.get('line_end')}\n"
                    f"{content}"
                )

        if self.config_mentions:
            parts.append("\nУпоминания конфигурационных файлов/путей, найденные в коде:")
            for value in sorted(set(self.config_mentions))[:30]:
                parts.append(f"- {value}")

        parts.append(
            "\nОграничения для ответа:\n"
            "- Нельзя утверждать наличие RBAC, ролей, middleware, 403 Forbidden, YAML-конфигов или конкретных файлов, если они не указаны выше.\n"
            "- Если информация не найдена в evidence, нужно прямо написать, что она не подтверждена найденными данными.\n"
            "- Не приводи фрагменты кода, если пользователь просил не приводить код."
        )
        return "\n".join(parts)

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "details": [self._strip_large_body(item) for item in self.details],
            "calls": self.calls,
            "raises": self.raises,
            "config_mentions": self.config_mentions,
            "text_matches": self.text_matches,
            "file_ranges": self.file_ranges,
            "raw_tool_count": self.raw_tool_count,
        }

    def _strip_large_body(self, item: dict[str, Any]) -> dict[str, Any]:
        copy = dict(item)
        body = copy.get("body")
        if isinstance(body, str) and len(body) > 500:
            copy["body"] = body[:500] + "... <truncated>"
        return copy


class EvidenceCollector:
    """Collect grounded facts from tool outputs of the current agent turn."""

    def __init__(self) -> None:
        self._bundle = EvidenceBundle()
        self._known_symbol_names: set[str] = set()

    def add_tool_result(self, tool_name: str, result: Any) -> None:
        self._bundle.raw_tool_count += 1
        if tool_name == "search_symbols" and isinstance(result, list):
            for item in result:
                if not isinstance(item, dict):
                    continue
                normalized = self._normalize_symbol_item(item)
                self._bundle.symbols.append(normalized)
                name = normalized.get("name")
                if name:
                    self._known_symbol_names.add(str(name))
        elif tool_name == "get_symbol_details" and isinstance(result, dict):
            if result.get("error"):
                return
            normalized = self._normalize_symbol_item(result)
            # Preserve body for analysis and grounding.
            normalized["body"] = result.get("body") or ""
            self._bundle.details.append(normalized)
            name = normalized.get("name")
            if name:
                self._known_symbol_names.add(str(name))
            self._extract_relations(normalized)
        elif tool_name == "search_project_text" and isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    self._bundle.text_matches.append(dict(item))
                    snippet = item.get("snippet") or ""
                    for mention in self._extract_config_mentions(snippet):
                        self._bundle.config_mentions.append(mention)
        elif tool_name == "read_file_range" and isinstance(result, dict):
            if result.get("error"):
                return
            self._bundle.file_ranges.append(dict(result))
            content = result.get("content") or ""
            for mention in self._extract_config_mentions(content):
                self._bundle.config_mentions.append(mention)

    def bundle(self) -> EvidenceBundle:
        self._deduplicate()
        return self._bundle

    def _normalize_symbol_item(self, item: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "symbol_id",
            "name",
            "kind",
            "qualified_name",
            "file_path",
            "line_start",
            "line_end",
            "signature",
            "arguments",
            "class_name",
            "docstring",
            "score",
        )
        return {key: item.get(key) for key in keys if key in item}

    def _extract_relations(self, symbol: dict[str, Any]) -> None:
        body = symbol.get("body") or ""
        source = symbol.get("qualified_name") or symbol.get("symbol_id") or symbol.get("name") or "<unknown>"
        if not body.strip():
            return

        for target in self._extract_python_calls(body):
            if target == symbol.get("name"):
                continue
            self._bundle.calls.append({"source": str(source), "target": target})

        for exception in self._extract_python_raises(body):
            self._bundle.raises.append({"source": str(source), "exception": exception})

        for mention in self._extract_config_mentions(body):
            self._bundle.config_mentions.append(mention)

    def _extract_python_calls(self, body: str) -> list[str]:
        calls: list[str] = []
        try:
            tree = ast.parse(body)
        except SyntaxError:
            return self._extract_calls_regex(body)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = self._call_name(node.func)
            if name:
                calls.append(name)
        return self._unique_preserve_order(calls)

    def _extract_calls_regex(self, body: str) -> list[str]:
        candidates = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body)
        ignored = {"if", "for", "while", "return", "raise"}
        return self._unique_preserve_order([item for item in candidates if item not in ignored])

    def _call_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    def _extract_python_raises(self, body: str) -> list[str]:
        exceptions: list[str] = []
        try:
            tree = ast.parse(body)
        except SyntaxError:
            return self._unique_preserve_order(re.findall(r"raise\s+([A-Za-z_][A-Za-z0-9_]*)", body))

        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                if isinstance(node.exc, ast.Call):
                    name = self._call_name(node.exc.func)
                else:
                    name = self._call_name(node.exc)
                if name:
                    exceptions.append(name)
        return self._unique_preserve_order(exceptions)

    def _extract_config_mentions(self, body: str) -> list[str]:
        # Extract literal-looking config/resource paths from strings.
        mentions = re.findall(
            r"[A-Za-z0-9_./\\-]+\.(?:json|ya?ml|toml|ini|env)",
            body,
            flags=re.IGNORECASE,
        )
        return self._unique_preserve_order(mentions)

    def _unique_preserve_order(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _deduplicate(self) -> None:
        self._bundle.symbols = self._dedupe_dicts(self._bundle.symbols, ("symbol_id", "qualified_name", "name"))
        self._bundle.details = self._dedupe_dicts(self._bundle.details, ("symbol_id", "qualified_name", "name"))
        self._bundle.calls = self._dedupe_dicts(self._bundle.calls, ("source", "target"))
        self._bundle.raises = self._dedupe_dicts(self._bundle.raises, ("source", "exception"))
        self._bundle.config_mentions = self._unique_preserve_order(self._bundle.config_mentions)
        self._bundle.text_matches = self._dedupe_dicts(self._bundle.text_matches, ("file_path", "line_start", "line_end"))
        self._bundle.file_ranges = self._dedupe_dicts(self._bundle.file_ranges, ("file_path", "line_start", "line_end"))

    def _dedupe_dicts(self, items: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        seen: set[tuple[Any, ...]] = set()
        result: list[dict[str, Any]] = []
        for item in items:
            marker = tuple(item.get(key) for key in keys)
            if marker in seen:
                continue
            seen.add(marker)
            result.append(item)
        return result
