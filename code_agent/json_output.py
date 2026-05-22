from __future__ import annotations

import ast
import json
import re
import uuid
from typing import Any, Iterable

from pydantic import ValidationError

from code_agent.llm_client import OpenAICompatibleClient
from code_agent.logger import JsonlLogger
from code_agent.schemas import MethodDocumentation
from code_agent.index_store import CodeIndex


JSON_SYSTEM_PROMPT = """
Ты модуль структурированного вывода для системы автодокументирования кода.
Твоя задача — сформировать валидный JSON по заданной схеме.

Правила:
1. Верни только JSON-объект, без markdown, без ```json и без пояснений вокруг.
2. Не выдумывай точные имена методов, файлов и классов.
3. В первую очередь используй фактические данные инструментов: symbol_id, qualified_name, file_path, signature, arguments, body.
4. Если тип или класс параметра нельзя определить из кода или фактов инструментов, укажи "unknown".
5. Для вложенных свойств параметров используй только один уровень вложенности.
6. Поле exact_method_name должно содержать точное имя основного описываемого метода/функции.
7. Если в фактах инструментов есть get_symbol_details для описываемого метода, qualified_name и file_path должны быть взяты из него.
""".strip()


class JsonDocumentationGenerator:
    def __init__(
        self,
        llm_client: OpenAICompatibleClient,
        log_path: str = "logs/agent_steps.jsonl",
        max_repair_attempts: int = 2,
        index_path: str | None = None,
    ):
        self.llm_client = llm_client
        self.logger = JsonlLogger(log_path)
        self.max_repair_attempts = max_repair_attempts
        self.index_path = index_path

    def generate_from_messages(
        self,
        chat_messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]] | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate structured JSON from chat history and tool facts.

        The model receives both the natural-language conversation and the factual
        results returned by tools. After validation, the JSON is additionally
        enriched with deterministic data from get_symbol_details results, so
        fields such as qualified_name and file_path are not lost.
        """
        turn_id = turn_id or uuid.uuid4().hex
        schema = MethodDocumentation.model_json_schema()
        normalized_messages = self._normalize_chat_messages(chat_messages)
        normalized_tool_results = self._normalize_tool_results(tool_results or [])
        tool_facts = self._build_tool_facts(normalized_tool_results)

        messages: list[dict[str, str]] = [
            {"role": "system", "content": JSON_SYSTEM_PROMPT},
            *normalized_messages,
            {
                "role": "user",
                "content": (
                    "Сформируй JSON-документацию основного метода/функции по этой JSON Schema:\n"
                    f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
                    "Фактические данные, полученные инструментами анализа кода:\n"
                    f"{json.dumps(tool_facts, ensure_ascii=False, indent=2)}\n\n"
                    "Важно: точные поля exact_method_name, qualified_name, file_path, parameters[].name "
                    "заполняй по фактам инструментов, а не по свободному тексту ответа."
                ),
            },
        ]

        last_raw = ""
        last_error = ""

        for attempt in range(1, self.max_repair_attempts + 2):
            response = self.llm_client.chat(messages=messages, tools=None)
            raw = response.choices[0].message.content or ""
            last_raw = raw

            self.logger.write(
                "json_generation_response",
                {
                    "turn_id": turn_id,
                    "attempt": attempt,
                    "raw": raw,
                    "tool_fact_count": len(tool_facts.get("symbols", [])),
                },
            )

            try:
                data = self._parse_json(raw)
                data = self._enrich_with_index_if_needed(data, tool_facts)
                data = self._enrich_with_tool_facts(data, tool_facts)
                validated = MethodDocumentation.model_validate(data)
                parsed = validated.model_dump()

                self.logger.write(
                    "json_generation_valid",
                    {
                        "turn_id": turn_id,
                        "attempt": attempt,
                        "json": parsed,
                    },
                )

                return {
                    "ok": True,
                    "attempts": attempt,
                    "json": parsed,
                    "raw": json.dumps(parsed, ensure_ascii=False, indent=2),
                }
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                last_error = str(exc)
                self.logger.write(
                    "json_generation_invalid",
                    {
                        "turn_id": turn_id,
                        "attempt": attempt,
                        "error": last_error,
                        "raw": raw,
                    },
                )

                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "JSON не прошёл валидацию. "
                            f"Ошибка:\n{last_error}\n\n"
                            "Исправь ответ. Верни только валидный JSON по той же схеме, "
                            "без markdown и без пояснений. Не теряй фактические данные инструментов."
                        ),
                    }
                )

        return {
            "ok": False,
            "attempts": self.max_repair_attempts + 1,
            "error": last_error or "Unknown validation error",
            "raw": last_raw,
        }

    def _normalize_chat_messages(self, messages: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    def _normalize_tool_results(self, tool_results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            tool_name = item.get("tool_name")
            result = item.get("result")
            if not tool_name:
                continue
            normalized.append({"tool_name": tool_name, "result": result})
        return normalized

    def _build_tool_facts(self, tool_results: list[dict[str, Any]]) -> dict[str, Any]:
        """Convert raw tool results into compact facts for JSON generation."""
        symbols_by_id: dict[str, dict[str, Any]] = {}
        search_results: list[dict[str, Any]] = []

        for item in tool_results:
            tool_name = item.get("tool_name")
            result = item.get("result")

            if tool_name == "search_symbols" and isinstance(result, list):
                for symbol in result:
                    if not isinstance(symbol, dict):
                        continue
                    compact = self._compact_symbol(symbol, include_body=False)
                    search_results.append(compact)
                    symbol_id = compact.get("symbol_id")
                    if symbol_id and symbol_id not in symbols_by_id:
                        symbols_by_id[symbol_id] = compact

            if tool_name == "get_symbol_details" and isinstance(result, dict) and not result.get("error"):
                compact = self._compact_symbol(result, include_body=True)
                symbol_id = compact.get("symbol_id")
                if symbol_id:
                    symbols_by_id[symbol_id] = {**symbols_by_id.get(symbol_id, {}), **compact}

        symbols = list(symbols_by_id.values())
        class_properties = self._extract_class_properties(symbols)
        calls_by_symbol = self._extract_calls(symbols)

        for symbol in symbols:
            symbol_id = symbol.get("symbol_id")
            if symbol_id in calls_by_symbol:
                symbol["calls"] = calls_by_symbol[symbol_id]

        return {
            "symbols": symbols,
            "search_results": search_results,
            "class_properties": class_properties,
        }

    def _compact_symbol(self, symbol: dict[str, Any], include_body: bool) -> dict[str, Any]:
        keys = [
            "symbol_id",
            "kind",
            "name",
            "qualified_name",
            "file_path",
            "line_start",
            "line_end",
            "signature",
            "arguments",
            "decorators",
            "docstring",
            "class_name",
            "score",
        ]
        compact = {key: symbol.get(key) for key in keys if key in symbol}
        if include_body and isinstance(symbol.get("body"), str):
            compact["body"] = symbol["body"]
        return compact

    def _extract_class_properties(self, symbols: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
        class_props: dict[str, list[dict[str, str]]] = {}
        for symbol in symbols:
            if symbol.get("kind") != "class":
                continue
            body = symbol.get("body")
            class_name = symbol.get("name")
            if not isinstance(body, str) or not body.strip() or not isinstance(class_name, str):
                continue
            props: dict[str, dict[str, str]] = {}
            try:
                tree = ast.parse(body)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                else:
                    continue
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        props[target.attr] = {
                            "name": target.attr,
                            "class_name": self._infer_value_type(getattr(node, "value", None)),
                            "description": f"Свойство {target.attr} объекта {class_name}",
                        }
            class_props[class_name] = list(props.values())
        return class_props

    def _extract_calls(self, symbols: list[dict[str, Any]]) -> dict[str, list[str]]:
        calls_by_symbol: dict[str, list[str]] = {}
        for symbol in symbols:
            body = symbol.get("body")
            symbol_id = symbol.get("symbol_id")
            if not isinstance(body, str) or not body.strip() or not isinstance(symbol_id, str):
                continue
            try:
                tree = ast.parse(body)
            except SyntaxError:
                continue
            calls: list[str] = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = self._call_name(node.func)
                if name and name not in calls:
                    calls.append(name)
            if calls:
                calls_by_symbol[symbol_id] = calls
        return calls_by_symbol

    def _call_name(self, func: ast.AST) -> str | None:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts = [func.attr]
            value = func.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            return ".".join(reversed(parts))
        return None

    def _infer_value_type(self, value: ast.AST | None) -> str:
        if isinstance(value, ast.Constant):
            return type(value.value).__name__
        if isinstance(value, ast.Name):
            return "unknown"
        if isinstance(value, ast.List):
            return "list"
        if isinstance(value, ast.Dict):
            return "dict"
        if isinstance(value, ast.Call):
            return self._call_name(value.func) or "unknown"
        return "unknown"

    def _enrich_with_tool_facts(self, data: Any, tool_facts: dict[str, Any]) -> Any:
        """Fill missing exact fields from tool facts after model generation."""
        if not isinstance(data, dict):
            return data

        symbols = tool_facts.get("symbols", []) if isinstance(tool_facts, dict) else []
        if not isinstance(symbols, list) or not symbols:
            return data

        main_symbol = self._find_main_symbol(data, symbols)
        if main_symbol:
            data.setdefault("exact_method_name", main_symbol.get("name"))
            if not data.get("qualified_name"):
                data["qualified_name"] = main_symbol.get("qualified_name") or main_symbol.get("symbol_id")
            if not data.get("file_path"):
                data["file_path"] = main_symbol.get("file_path")

            symbol_args = main_symbol.get("arguments") or []
            if symbol_args and not data.get("parameters"):
                data["parameters"] = [
                    {
                        "name": arg,
                        "class_name": self._infer_parameter_class(arg, symbols, tool_facts),
                        "description": f"Параметр {arg} функции {main_symbol.get('name')}",
                        "properties": self._properties_for_parameter(arg, symbols, tool_facts),
                    }
                    for arg in symbol_args
                ]
            elif isinstance(data.get("parameters"), list):
                for param in data["parameters"]:
                    if not isinstance(param, dict):
                        continue
                    name = param.get("name")
                    if not param.get("class_name") or param.get("class_name") == "unknown":
                        param["class_name"] = self._infer_parameter_class(str(name), symbols, tool_facts)
                    if not param.get("properties"):
                        param["properties"] = self._properties_for_parameter(str(name), symbols, tool_facts)

            existing_related = data.get("related_symbols") if isinstance(data.get("related_symbols"), list) else []
            existing_names = {
                item.get("name")
                for item in existing_related
                if isinstance(item, dict)
            }
            known_symbol_names = {str(symbol.get("name")) for symbol in symbols if symbol.get("name")}
            for call_name in main_symbol.get("calls", []) or []:
                if call_name == main_symbol.get("name") or call_name in existing_names:
                    continue
                if call_name not in known_symbol_names:
                    continue
                existing_related.append(
                    {
                        "name": call_name,
                        "relation": "calls",
                        "description": f"Вызывается внутри {main_symbol.get('name')}",
                    }
                )
                existing_names.add(call_name)
            data["related_symbols"] = existing_related

        return data

    def _find_main_symbol(self, data: dict[str, Any], symbols: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = [s for s in symbols if s.get("kind") in {"function", "method"}]
        if not candidates:
            return None

        exact_name = data.get("exact_method_name")
        qualified_name = data.get("qualified_name")

        for symbol in candidates:
            if qualified_name and qualified_name in {symbol.get("qualified_name"), symbol.get("symbol_id")}:
                return symbol
        for symbol in candidates:
            if exact_name and exact_name == symbol.get("name"):
                return symbol
        for symbol in candidates:
            body = symbol.get("body")
            if isinstance(body, str) and body.strip():
                return symbol
        return candidates[0]

    def _infer_parameter_class(
        self,
        parameter_name: str,
        symbols: list[dict[str, Any]],
        tool_facts: dict[str, Any],
    ) -> str:
        if not parameter_name:
            return "unknown"

        class_names = [
            str(symbol.get("name"))
            for symbol in symbols
            if symbol.get("kind") == "class" and symbol.get("name")
        ]
        for class_name in class_names:
            if parameter_name.lower() == class_name.lower():
                return class_name
            if parameter_name.lower().endswith(class_name.lower()):
                return class_name
            if class_name.lower().endswith(parameter_name.lower()):
                return class_name

        if parameter_name.lower() == "user" and "User" in class_names:
            return "User"

        return "unknown"

    def _properties_for_parameter(
        self,
        parameter_name: str,
        symbols: list[dict[str, Any]],
        tool_facts: dict[str, Any],
    ) -> list[dict[str, str]]:
        class_name = self._infer_parameter_class(parameter_name, symbols, tool_facts)
        class_properties = tool_facts.get("class_properties", {}) if isinstance(tool_facts, dict) else {}
        props = class_properties.get(class_name, []) if isinstance(class_properties, dict) else []
        return props if isinstance(props, list) else []


    def _enrich_with_index_if_needed(self, data: Any, tool_facts: dict[str, Any]) -> Any:
        """Load missing symbol facts from code_index.json when conversation metadata has no tool results.

        This makes JSON generation robust for old dialogs, refreshed pages, or cases where
        tool results were not saved in conversation metadata. The LLM may output only
        exact_method_name, and this method fills the missing facts directly from the index.
        """
        if not isinstance(data, dict):
            return data
        if not self.index_path:
            return data

        symbols = tool_facts.get("symbols") if isinstance(tool_facts, dict) else None
        already_has_main_fact = False
        if isinstance(symbols, list):
            exact = data.get("exact_method_name")
            qualified = data.get("qualified_name")
            for symbol in symbols:
                if not isinstance(symbol, dict):
                    continue
                if qualified and qualified in {symbol.get("qualified_name"), symbol.get("symbol_id")}:
                    already_has_main_fact = True
                    break
                if exact and exact == symbol.get("name") and symbol.get("file_path"):
                    already_has_main_fact = True
                    break
        if already_has_main_fact:
            return data

        try:
            index = CodeIndex(self.index_path)
        except Exception:
            return data

        exact_name = data.get("exact_method_name")
        qualified_name = data.get("qualified_name")
        main_symbol = None

        for symbol in index.symbols:
            if symbol.get("kind") not in {"function", "method"}:
                continue
            if qualified_name and qualified_name in {symbol.get("qualified_name"), symbol.get("symbol_id")}:
                main_symbol = symbol
                break
            if exact_name and exact_name == symbol.get("name"):
                main_symbol = symbol
                break

        if not main_symbol and exact_name:
            results = index.search_symbols(str(exact_name), limit=5)
            for result in results:
                candidate = index.by_id.get(result.get("symbol_id"))
                if candidate and candidate.get("kind") in {"function", "method"}:
                    main_symbol = candidate
                    break

        if not main_symbol:
            return data

        collected: dict[str, dict[str, Any]] = {}
        main_details = index.get_symbol_details(main_symbol["symbol_id"], include_body=True)
        collected[main_symbol["symbol_id"]] = main_details

        # Add directly called project symbols from the main body.
        calls = self._extract_calls([self._compact_symbol(main_details, include_body=True)])
        called_names = calls.get(main_symbol["symbol_id"], [])
        for call_name in called_names:
            for symbol in index.symbols:
                if symbol.get("name") == call_name and symbol.get("symbol_id") not in collected:
                    collected[symbol["symbol_id"]] = index.get_symbol_details(symbol["symbol_id"], include_body=True)
                    break

        # Add classes from parameters and obvious User class for user parameter.
        arg_names = set(main_symbol.get("arguments") or [])
        for symbol in index.symbols:
            if symbol.get("kind") != "class":
                continue
            class_name = str(symbol.get("name") or "")
            if class_name.lower() in {arg.lower() for arg in arg_names} or ("user" in arg_names and class_name == "User"):
                collected[symbol["symbol_id"]] = index.get_symbol_details(symbol["symbol_id"], include_body=True)

        generated_facts = self._build_tool_facts([
            {"tool_name": "get_symbol_details", "result": symbol}
            for symbol in collected.values()
        ])

        existing_symbols = tool_facts.setdefault("symbols", [])
        existing_ids = {s.get("symbol_id") for s in existing_symbols if isinstance(s, dict)}
        for symbol in generated_facts.get("symbols", []):
            if symbol.get("symbol_id") not in existing_ids:
                existing_symbols.append(symbol)
                existing_ids.add(symbol.get("symbol_id"))

        existing_props = tool_facts.setdefault("class_properties", {})
        for class_name, props in generated_facts.get("class_properties", {}).items():
            existing_props.setdefault(class_name, props)

        return data

    def _parse_json(self, raw: str) -> Any:
        text = raw.strip()
        if text.startswith("```"):
            text = self._strip_markdown_code_fence(text)
        return json.loads(text)

    def _strip_markdown_code_fence(self, text: str) -> str:
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text.strip("`").strip()
