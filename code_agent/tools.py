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

SEARCH_PROJECT_TEXT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_project_text",
        "description": (
            "Search plain text in project files. Use this for routes/endpoints, FastAPI decorators, "
            "Depends(...), configuration files, JSON/YAML/TOML settings, constants and other text that may not be indexed as symbols."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text query, for example: require_access admin APIRouter Depends",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matches. Default is 10.",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Number of context lines around each match. Default is 2.",
                },
            },
            "required": ["query"],
        },
    },
}

READ_FILE_RANGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file_range",
        "description": (
            "Read a small line range from a project file by relative path. Use it after search_project_text "
            "when a snippet is not enough."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative file path inside project.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Start line, 1-based. Default is 1.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line, 1-based inclusive.",
                },
            },
            "required": ["file_path"],
        },
    },
}

TOOL_SCHEMAS = [
    SEARCH_SYMBOLS_SCHEMA,
    GET_SYMBOL_DETAILS_SCHEMA,
    SEARCH_PROJECT_TEXT_SCHEMA,
    READ_FILE_RANGE_SCHEMA,
]


class ToolExecutor:
    def __init__(self, code_index: CodeIndex):
        self.code_index = code_index
        self.tools: dict[str, Callable[..., Any]] = {
            "search_symbols": self.search_symbols,
            "get_symbol_details": self.get_symbol_details,
            "search_project_text": self.search_project_text,
            "read_file_range": self.read_file_range,
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

    def search_project_text(self, query: str, limit: int = 10, context_lines: int = 2) -> list[dict[str, Any]]:
        return self.code_index.search_project_text(query=query, limit=limit, context_lines=context_lines)

    def read_file_range(self, file_path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        return self.code_index.read_file_range(file_path=file_path, start_line=start_line, end_line=end_line)
