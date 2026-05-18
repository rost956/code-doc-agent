from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Generator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from code_agent.agent import CodeResearchAgent
from code_agent.config import load_config
from code_agent.conversation_store import ConversationStore
from code_agent.llm_client import OpenAICompatibleClient
from code_agent.json_output import JsonDocumentationGenerator


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    index_path: str | None = None
    max_steps: int | None = None


config = load_config()
llm_client = OpenAICompatibleClient(config.llm)
conversation_store = ConversationStore(config.web.conversation_dir)

app = FastAPI(title="Code ReAct Agent Web")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/conversations")
def list_conversations() -> list[dict[str, Any]]:
    return conversation_store.list()


@app.post("/api/conversations")
def create_conversation() -> dict[str, Any]:
    return conversation_store.create()


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict[str, Any]:
    try:
        return conversation_store.get(conversation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc



@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict[str, Any]:
    try:
        conversation_store.delete(conversation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "deleted_id": conversation_id}


@app.post("/api/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message is empty")

    conversation = conversation_store.create() if not request.conversation_id else conversation_store.get(request.conversation_id)
    conversation_id = conversation["id"]
    conversation_store.append_message(conversation_id, "user", request.message.strip())
    conversation = conversation_store.get(conversation_id)

    index_path = request.index_path or config.agent.index_path
    max_steps = request.max_steps or config.agent.max_steps

    agent = CodeResearchAgent(
        index_path=index_path,
        llm_client=llm_client,
        log_path=config.agent.log_path,
        max_steps=max_steps,
        system_prompt_path=config.agent.system_prompt_path,
    )

    def event_generator() -> Generator[str, None, None]:
        final_parts: list[str] = []
        tool_results: list[dict[str, Any]] = []
        turn_id = uuid.uuid4().hex
        yield _sse("conversation", {"conversation_id": conversation_id, "turn_id": turn_id})

        try:
            chat_messages = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in conversation.get("messages", [])
                if msg.get("role") in {"user", "assistant"}
            ]

            for event in agent.run_events(chat_messages, turn_id=turn_id):
                if event["type"] == "final_delta":
                    final_parts.append(event["delta"])
                if event["type"] == "tool_result":
                    tool_results.append(
                        {
                            "turn_id": turn_id,
                            "step": event.get("step"),
                            "tool_name": event.get("tool_name"),
                            "result": event.get("result"),
                        }
                    )
                yield _sse(event["type"], event)

            final_text = "".join(final_parts).strip()
            if final_text:
                conversation_store.append_message(
                    conversation_id,
                    "assistant",
                    final_text,
                    metadata={"turn_id": turn_id, "tool_results": tool_results},
                )

            yield _sse("done", {"conversation_id": conversation_id})
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/conversations/{conversation_id}/structured-json")
def generate_structured_json(conversation_id: str) -> dict[str, Any]:
    """Generate validated JSON documentation from the current chat history."""
    try:
        conversation = conversation_store.get(conversation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    chat_messages = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in conversation.get("messages", [])
        if msg.get("role") in {"user", "assistant"}
    ]

    if not chat_messages:
        raise HTTPException(status_code=400, detail="Conversation is empty")

    turn_id = uuid.uuid4().hex
    generator = JsonDocumentationGenerator(
        llm_client=llm_client,
        log_path=config.agent.log_path,
    )
    tool_results = conversation_store.collect_tool_results(conversation_id)
    result = generator.generate_from_messages(
        chat_messages,
        tool_results=tool_results,
        turn_id=turn_id,
    )

    if result.get("ok"):
        assistant_text = "```json\n" + result["raw"] + "\n```"
        conversation_store.append_message(conversation_id, "assistant", assistant_text)
    else:
        assistant_text = (
            "Не удалось сформировать валидный JSON.\n\n"
            f"Ошибка: {result.get('error')}\n\n"
            f"Последний ответ модели:\n{result.get('raw')}"
        )
        conversation_store.append_message(conversation_id, "assistant", assistant_text)

    return {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        **result,
    }


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
