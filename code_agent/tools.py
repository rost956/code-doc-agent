from __future__ import annotations

import json
from typing import Any, Callable

from code_agent.index_store import CodeIndex


SEARCH_SYMBOLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_symbols",
        "description": (
            "Search code symbols by query in indexed metadata: names, signatures, "
            "arguments, decorators, docstrings and file paths. Use it first when you need "
            "to find relevant functions, methods or classes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, for example: access permission execute",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results. Default is 10.",
                },
            },
            "required": ["query"],
        },
    },
}

GET_SYMBOL_DETAILS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_symbol_details",
        "description": (
            "Return detailed information about a function, method or class by symbol_id. "
            "Use this after search_symbols returns a relevant symbol."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol_id": {
                    "type": "string",
                    "description": "Unique symbol id from search_symbols results.",
                },
                "include_body": {
                    "type": "boolean",
                    "description": "Whether to include source code body. Default is true.",
                },
            },
            "required": ["symbol_id"],
        },
    },
}

TOOL_SCHEMAS = [SEARCH_SYMBOLS_SCHEMA, GET_SYMBOL_DETAILS_SCHEMA]


class ToolExecutor:
    def __init__(self, code_index: CodeIndex):
        self.code_index = code_index
        self.tools: dict[str, Callable[..., Any]] = {
            "search_symbols": self.search_symbols,
            "get_symbol_details": self.get_symbol_details,
        }

    def execute(self, name: str, arguments_json: str) -> dict[str, Any] | list[dict[str, Any]]:
        if name not in self.tools:
            return {"error": f"Unknown tool: {name}"}

        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            return {"error": f"Invalid JSON arguments: {exc}"}

        try:
            return self.tools[name](**arguments)
        except TypeError as exc:
            return {"error": f"Invalid arguments for {name}: {exc}"}
        except Exception as exc:
            return {"error": f"Tool {name} failed: {exc}"}

    def search_symbols(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.code_index.search_symbols(query=query, limit=limit)

    def get_symbol_details(self, symbol_id: str, include_body: bool = True) -> dict[str, Any]:
        return self.code_index.get_symbol_details(symbol_id=symbol_id, include_body=include_body)
