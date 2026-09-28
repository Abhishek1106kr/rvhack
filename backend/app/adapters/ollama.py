"""Ollama chat with tool calling, streamed. Output is untrusted: the runtime validates calls."""

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.adapters.base import LLMChunk, Message, TextDelta, ToolCallRequest
from app.tools.registry import ToolSchema


def _to_ollama(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments}} for c in message.tool_calls
        ]
    if message.role == "tool" and message.name:
        out["tool_name"] = message.name
    return out


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    # Leave it to the executor's validation to reject; keep the raw value for the trace.
    return {"_unparsed_arguments": raw}


class OllamaLLM:
    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        temperature: float = 0.2,
        num_ctx: int = 4096,
        keep_alive: str = "30m",
        max_tokens: int = 160,
    ) -> None:
        """max_tokens bounds one reply: a voice answer that runs on is worse than a cut one."""
        self.name = f"ollama:{model}"
        self._model = model
        self._base_url = base_url
        self._options = {"temperature": temperature, "num_ctx": num_ctx, "num_predict": max_tokens}
        self._keep_alive = keep_alive

    async def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> AsyncIterator[LLMChunk]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_to_ollama(m) for m in messages],
            "stream": True,
            "keep_alive": self._keep_alive,
            "options": self._options,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
        async with (
            httpx.AsyncClient(base_url=self._base_url, timeout=timeout) as client,
            client.stream("POST", "/api/chat", json=payload) as response,
        ):
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")[:300]
                raise RuntimeError(f"ollama HTTP {response.status_code}: {body}")
            async for line in response.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise RuntimeError(f"ollama: {data['error']}")
                message = data.get("message") or {}
                if content := message.get("content"):
                    yield TextDelta(text=content)
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    yield ToolCallRequest(
                        call_id=call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        name=str(function.get("name", "")),
                        arguments=_parse_arguments(function.get("arguments")),
                    )
                if data.get("done"):
                    return

    async def warm_up(self) -> None:
        """Load the model into memory so the first user turn doesn't pay the load time."""
        async with httpx.AsyncClient(base_url=self._base_url, timeout=120.0) as client:
            response = await client.post(
                "/api/generate", json={"model": self._model, "keep_alive": self._keep_alive}
            )
            response.raise_for_status()
