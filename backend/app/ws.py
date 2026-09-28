"""WebSocket transport: one VoiceSession per connection.

Client → server
    text frame  {"type": "text", "text": ...}                                  typed utterance
    text frame  {"type": "ack", "turn_id", "sentence_id"}                      sentence played
    text frame  {"type": "flushed", "turn_id", "last_acked_sentence_id"}       flush done
    binary      PCM16 mono 16 kHz frames of FRAME_BYTES                        microphone

Server → client (JSON, in send order)
    {"type": "event", "event": {...}}
    {"type": "audio", "turn_id", "sentence_id", "index", "is_last", "sample_rate", "data": b64}
    {"type": "flush", "turn_id"}
"""

import asyncio
import base64
import json
import logging
import uuid
from typing import Annotated, Literal

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.adapters.base import AudioOut
from app.agent.orchestrator import VoiceSession
from app.events import Error, Event
from app.runtime import Runtime

log = logging.getLogger(__name__)


class TextIn(BaseModel):
    type: Literal["text"]
    text: str = Field(max_length=2000)


class PlaybackAck(BaseModel):
    type: Literal["ack"]
    turn_id: str
    sentence_id: int


class Flushed(BaseModel):
    type: Literal["flushed"]
    turn_id: str
    last_acked_sentence_id: int | None


ClientMessage = TypeAdapter(Annotated[TextIn | PlaybackAck | Flushed, Field(discriminator="type")])


class WebSocketTransport:
    """Serializes everything the client receives through one queue, so audio, flushes and
    events arrive in the order the session produced them."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._outbox: asyncio.Queue[str] = asyncio.Queue()

    async def send_audio(self, chunk: AudioOut) -> None:
        await self._outbox.put(
            json.dumps(
                {
                    "type": "audio",
                    "turn_id": chunk.turn_id,
                    "sentence_id": chunk.sentence_id,
                    "index": chunk.index,
                    "is_last": chunk.is_last,
                    "sample_rate": chunk.sample_rate,
                    "data": base64.b64encode(chunk.data).decode("ascii"),
                }
            )
        )

    async def send_flush(self, turn_id: str) -> None:
        await self._outbox.put(json.dumps({"type": "flush", "turn_id": turn_id}))

    def send_event(self, event: Event) -> None:
        # Called synchronously by the EventBus; never blocks.
        self._outbox.put_nowait(f'{{"type":"event","event":{event.model_dump_json()}}}')

    async def run_writer(self) -> None:
        while True:
            await self._ws.send_text(await self._outbox.get())


async def serve_session(ws: WebSocket, runtime: Runtime) -> None:
    await ws.accept()
    session_id = uuid.uuid4().hex[:12]
    transport = WebSocketTransport(ws)
    unsubscribe = runtime.bus.subscribe(
        lambda event: transport.send_event(event) if event.session_id == session_id else None
    )
    session = VoiceSession(
        session_id=session_id,
        bus=runtime.bus,
        adapters=runtime.make_adapters(),
        transport=transport,
        tools=runtime.tools,
        config=runtime.config,
    )
    writer = asyncio.create_task(transport.run_writer(), name=f"ws-writer-{session_id}")
    try:
        await session.start()
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (frame := message.get("bytes")) is not None:
                await session.handle_audio(frame)
                continue
            try:
                parsed = ClientMessage.validate_json(message.get("text") or "")
            except ValidationError as exc:
                runtime.bus.publish(
                    Error(
                        session_id=session_id,
                        stage="transport",
                        message=f"invalid client message: {exc.errors()[0]['msg']}",
                        recoverable=True,
                    )
                )
                continue
            match parsed:
                case TextIn(text=text):
                    await session.handle_text(text)
                case PlaybackAck(turn_id=turn_id, sentence_id=sentence_id):
                    await session.handle_ack(turn_id, sentence_id)
                case Flushed(turn_id=turn_id, last_acked_sentence_id=last):
                    await session.handle_flushed(turn_id, last)
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
        unsubscribe()
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        log.info("session %s closed", session_id)
