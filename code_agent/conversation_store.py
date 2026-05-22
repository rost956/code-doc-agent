from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ConversationStore:
    def __init__(self, root_dir: str | Path = "data/conversations"):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def create(self) -> dict[str, Any]:
        conversation_id = uuid.uuid4().hex
        now = self._now()
        conversation = {
            "id": conversation_id,
            "title": "Новый диалог",
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "repository": None,
        }
        self.save(conversation)
        return conversation

    def list(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.root_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items.append(
                {
                    "id": data.get("id"),
                    "title": data.get("title") or "Без названия",
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "message_count": len(data.get("messages", [])),
                    "repository": data.get("repository"),
                }
            )
        return items

    def get(self, conversation_id: str) -> dict[str, Any]:
        path = self._path(conversation_id)
        if not path.exists():
            raise FileNotFoundError(f"Conversation not found: {conversation_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, conversation: dict[str, Any]) -> None:
        conversation["updated_at"] = self._now()
        self._path(conversation["id"]).write_text(
            json.dumps(conversation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


    def delete(self, conversation_id: str) -> None:
        path = self._path(conversation_id)
        if not path.exists():
            raise FileNotFoundError(f"Conversation not found: {conversation_id}")
        path.unlink()

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversation = self.get(conversation_id)
        message = {
            "role": role,
            "content": content,
            "created_at": self._now(),
        }
        if metadata:
            message["metadata"] = metadata
        conversation.setdefault("messages", []).append(message)

        if role == "user" and conversation.get("title") == "Новый диалог":
            title = content.strip().replace("\n", " ")[:60]
            conversation["title"] = title or "Новый диалог"

        self.save(conversation)
        return message


    def set_repository(self, conversation_id: str, repository: dict[str, Any] | None) -> dict[str, Any]:
        conversation = self.get(conversation_id)
        conversation["repository"] = repository
        self.save(conversation)
        return conversation

    def get_repository(self, conversation_id: str) -> dict[str, Any] | None:
        conversation = self.get(conversation_id)
        repository = conversation.get("repository")
        return repository if isinstance(repository, dict) else None

    def collect_tool_results(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return tool results saved in assistant message metadata."""
        conversation = self.get(conversation_id)
        results: list[dict[str, Any]] = []
        for message in conversation.get("messages", []):
            metadata = message.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            for item in metadata.get("tool_results", []):
                if isinstance(item, dict):
                    results.append(item)
        return results

    def _path(self, conversation_id: str) -> Path:
        safe_id = "".join(ch for ch in conversation_id if ch.isalnum() or ch in {"-", "_"})
        return self.root_dir / f"{safe_id}.json"

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
