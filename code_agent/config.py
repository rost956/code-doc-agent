from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str
    base_url: Optional[str] = None
    temperature: float = 0.1
    max_tokens: Optional[int] = None


@dataclass(frozen=True)
class AgentConfig:
    index_path: str = "code_index.json"
    log_path: str = "logs/agent_steps.jsonl"
    max_steps: int = 8


@dataclass(frozen=True)
class AppConfig:
    llm: LLMConfig
    agent: AgentConfig


def load_config(env_path: str | Path = ".env") -> AppConfig:
    """
    Load application settings from .env and environment variables.

    Values from real environment variables have priority over .env values.
    """
    env_file = Path(env_path)
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=False)

    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    base_url = os.getenv("LLM_BASE_URL", "").strip() or None

    if not api_key:
        raise RuntimeError(
            "Не задан LLM_API_KEY. Укажи ключ в .env, например: LLM_API_KEY=hf_xxx"
        )

    if not model:
        raise RuntimeError(
            "Не задан LLM_MODEL. Укажи модель в .env, например: "
            "LLM_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct"
        )

    return AppConfig(
        llm=LLMConfig(
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=_get_float("LLM_TEMPERATURE", 0.1),
            max_tokens=_get_optional_int("LLM_MAX_TOKENS"),
        ),
        agent=AgentConfig(
            index_path=os.getenv("AGENT_INDEX_PATH", "code_index.json"),
            log_path=os.getenv("AGENT_LOG_PATH", "logs/agent_steps.jsonl"),
            max_steps=_get_int("AGENT_MAX_STEPS", 8),
        ),
    )


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Переменная {name} должна быть целым числом") from exc


def _get_optional_int(name: str) -> Optional[int]:
    value = os.getenv(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Переменная {name} должна быть целым числом") from exc


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"Переменная {name} должна быть числом") from exc
