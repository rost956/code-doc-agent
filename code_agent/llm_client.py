from __future__ import annotations

from typing import Any

from openai import OpenAI

from code_agent.config import LLMConfig


class OpenAICompatibleClient:
    """
    Thin wrapper over OpenAI-compatible Chat Completions API.

    Works with OpenAI, Hugging Face Inference Providers and other services
    that expose a compatible /chat/completions endpoint.
    """

    def __init__(self, config: LLMConfig):
        self.config = config

        client_kwargs: dict[str, Any] = {"api_key": config.api_key}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url

        self.client = OpenAI(**client_kwargs)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = "auto",
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }

        if self.config.max_tokens is not None:
            kwargs["max_tokens"] = self.config.max_tokens

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        return self.client.chat.completions.create(**kwargs)
