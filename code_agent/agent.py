from __future__ import annotations

import json
from typing import Any

from code_agent.index_store import CodeIndex
from code_agent.llm_client import OpenAICompatibleClient
from code_agent.logger import JsonlLogger
from code_agent.tools import TOOL_SCHEMAS, ToolExecutor


SYSTEM_PROMPT = """
Ты агент для анализа исходного кода проекта.

Твоя задача — ответить на вопрос пользователя, используя только данные, полученные через инструменты.

Правила:
1. Не выдумывай имена файлов, классов, методов и функций.
2. Сначала ищи релевантные единицы кода через search_symbols.
3. Если найден подходящий символ, запроси подробности через get_symbol_details.
4. Если в коде найден вызов неизвестной функции, можешь снова использовать search_symbols по её имени.
5. Не делай один и тот же поиск несколько раз.
6. Когда данных достаточно, дай финальный ответ по-русски:
   - какие функции/классы отвечают за логику;
   - где они находятся;
   - какие параметры принимают;
   - что делают;
   - какие связи между ними обнаружены.
7. Если данных не хватает, честно напиши, что именно не удалось найти.
""".strip()


class CodeResearchAgent:
    def __init__(
        self,
        index_path: str,
        llm_client: OpenAICompatibleClient,
        log_path: str = "logs/agent_steps.jsonl",
        max_steps: int = 8,
    ):
        self.index = CodeIndex(index_path)
        self.llm_client = llm_client
        self.tool_executor = ToolExecutor(self.index)
        self.logger = JsonlLogger(log_path)
        self.max_steps = max_steps

    def answer(self, question: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        seen_tool_requests: set[str] = set()
        empty_search_count = 0

        for step in range(1, self.max_steps + 1):
            response = self.llm_client.chat(messages=messages, tools=TOOL_SCHEMAS)
            message = response.choices[0].message

            self.logger.write(
                "llm_response",
                {
                    "step": step,
                    "content": message.content,
                    "tool_calls": self._serialize_tool_calls(message.tool_calls),
                },
            )

            if not message.tool_calls:
                return message.content or "Модель завершила работу без текстового ответа."

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": self._serialize_tool_calls_for_messages(message.tool_calls),
                }
            )

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                arguments_json = tool_call.function.arguments or "{}"
                request_key = f"{tool_name}:{arguments_json}"

                if request_key in seen_tool_requests:
                    result: dict[str, Any] | list[dict[str, Any]] = {
                        "error": "Repeated tool request. Stop repeating the same request and produce a final answer using known data."
                    }
                else:
                    seen_tool_requests.add(request_key)
                    result = self.tool_executor.execute(tool_name, arguments_json)

                if tool_name == "search_symbols" and isinstance(result, list) and not result:
                    empty_search_count += 1
                else:
                    empty_search_count = 0

                self.logger.write(
                    "tool_call",
                    {
                        "step": step,
                        "tool_name": tool_name,
                        "arguments": arguments_json,
                        "result": result,
                    },
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            if empty_search_count >= 2:
                messages.append(
                    {
                        "role": "user",
                        "content": "Два поиска подряд ничего не нашли. Сформируй финальный ответ на основе уже найденных данных.",
                    }
                )

        messages.append(
            {
                "role": "user",
                "content": "Достигнут лимит итераций. Сформируй финальный ответ на основе уже найденных данных.",
            }
        )
        final_response = self.llm_client.chat(messages=messages, tools=None)
        final_message = final_response.choices[0].message.content
        return final_message or "Достигнут лимит итераций, но модель не вернула итоговый текст."

    def _serialize_tool_calls(self, tool_calls: Any) -> list[dict[str, Any]]:
        if not tool_calls:
            return []

        return [
            {
                "id": call.id,
                "type": call.type,
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ]

    def _serialize_tool_calls_for_messages(self, tool_calls: Any) -> list[dict[str, Any]]:
        return self._serialize_tool_calls(tool_calls)
