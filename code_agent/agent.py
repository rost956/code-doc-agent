from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Generator, Iterable

from code_agent.config import load_text_file
from code_agent.index_store import CodeIndex
from code_agent.llm_client import OpenAICompatibleClient
from code_agent.logger import JsonlLogger
from code_agent.tools import TOOL_SCHEMAS, ToolExecutor


DEFAULT_SYSTEM_PROMPT = """
Ты агент для анализа исходного кода проекта.
Используй инструменты для поиска по индексу кода и не выдумывай имена функций, классов и файлов.
Отвечай по-русски.
""".strip()


class CodeResearchAgent:
    def __init__(
        self,
        index_path: str,
        llm_client: OpenAICompatibleClient,
        log_path: str = "logs/agent_steps.jsonl",
        max_steps: int = 8,
        system_prompt_path: str = "prompts/system_prompt.txt",
    ):
        self.index = CodeIndex(index_path)
        self.llm_client = llm_client
        self.tool_executor = ToolExecutor(self.index)
        self.logger = JsonlLogger(log_path)
        self.max_steps = max_steps
        self.system_prompt_path = system_prompt_path
        self.system_prompt = self._load_system_prompt(system_prompt_path)

    def answer(self, question: str) -> str:
        """CLI-compatible method for one question.

        The tool-call limit is reset for every call of this method.
        """
        final_answer = ""
        for event in self.run_events([{"role": "user", "content": question}]):
            if event["type"] == "final_delta":
                final_answer += event["delta"]
            elif event["type"] == "final":
                final_answer = event["content"]
        return final_answer or "Модель завершила работу без текстового ответа."

    def answer_from_history(self, chat_messages: list[dict[str, str]]) -> str:
        """Generate an answer using previous user/assistant messages.

        The history is preserved, but the tool-call limit is reset for the new user turn.
        """
        final_answer = ""
        for event in self.run_events(chat_messages):
            if event["type"] == "final_delta":
                final_answer += event["delta"]
            elif event["type"] == "final":
                final_answer = event["content"]
        return final_answer or "Модель завершила работу без текстового ответа."

    def run_events(
        self,
        chat_messages: list[dict[str, str]],
        turn_id: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Run the agent and yield events for web streaming.

        max_steps, repeated tool request detection and empty search detection
        are scoped to this single user turn. When a new chat message arrives,
        the web layer calls run_events again and these counters start from zero.

        Event types:
        - status: intermediate LLM text before tool calls;
        - tool_call: requested tool;
        - tool_result: tool execution result;
        - final_delta: piece of final answer text;
        - final: full final answer;
        - error: error message.
        """
        turn_id = turn_id or uuid.uuid4().hex

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *self._normalize_chat_messages(chat_messages),
        ]

        # These counters are deliberately local to one run_events call.
        # A new user message in the chat starts a new run_events call,
        # therefore the tool-call limit is reset automatically.
        seen_tool_requests: set[str] = set()
        empty_search_count = 0

        self.logger.write(
            "agent_turn_start",
            {
                "turn_id": turn_id,
                "max_steps": self.max_steps,
                "history_messages": len(messages),
            },
        )

        for step in range(1, self.max_steps + 1):
            try:
                response = self.llm_client.chat(messages=messages, tools=TOOL_SCHEMAS)
            except Exception as exc:
                yield {"type": "error", "message": f"Ошибка LLM-запроса: {exc}"}
                return

            message = response.choices[0].message

            self.logger.write(
                "llm_response",
                {
                    "turn_id": turn_id,
                    "step": step,
                    "content": message.content,
                    "tool_calls": self._serialize_tool_calls(message.tool_calls),
                },
            )

            if not message.tool_calls:
                final_text = message.content or "Модель завершила работу без текстового ответа."
                yield from self._stream_final(final_text)
                return

            if message.content:
                yield {"type": "status", "step": step, "message": message.content}

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

                yield {
                    "type": "tool_call",
                    "step": step,
                    "tool_name": tool_name,
                    "arguments": arguments_json,
                }

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
                        "turn_id": turn_id,
                        "step": step,
                        "tool_name": tool_name,
                        "arguments": arguments_json,
                        "result": result,
                    },
                )

                yield {
                    "type": "tool_result",
                    "step": step,
                    "tool_name": tool_name,
                    "result": result,
                }

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

        self.logger.write(
            "agent_turn_max_steps",
            {
                "turn_id": turn_id,
                "max_steps": self.max_steps,
            },
        )

        messages.append(
            {
                "role": "user",
                "content": "Достигнут лимит итераций. Сформируй финальный ответ на основе уже найденных данных.",
            }
        )
        try:
            final_response = self.llm_client.chat(messages=messages, tools=None)
        except Exception as exc:
            yield {"type": "error", "message": f"Ошибка финального LLM-запроса: {exc}"}
            return

        final_text = final_response.choices[0].message.content
        yield from self._stream_final(final_text or "Достигнут лимит итераций, но модель не вернула итоговый текст.")

    def _load_system_prompt(self, system_prompt_path: str) -> str:
        if not system_prompt_path:
            return DEFAULT_SYSTEM_PROMPT
        path = Path(system_prompt_path)
        if not path.exists():
            return DEFAULT_SYSTEM_PROMPT
        return load_text_file(path)

    def _normalize_chat_messages(self, messages: Iterable[dict[str, str]]) -> list[dict[str, str]]:
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

    def _stream_final(self, text: str) -> Generator[dict[str, Any], None, None]:
        for chunk in self._split_for_stream(text):
            yield {"type": "final_delta", "delta": chunk}
            time.sleep(0.01)
        yield {"type": "final", "content": text}

    def _split_for_stream(self, text: str, chunk_size: int = 12) -> list[str]:
        if not text:
            return []
        chunks: list[str] = []
        current = ""
        for token in text.split(" "):
            candidate = token if not current else current + " " + token
            if len(candidate) >= chunk_size:
                chunks.append(candidate + " ")
                current = ""
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

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
