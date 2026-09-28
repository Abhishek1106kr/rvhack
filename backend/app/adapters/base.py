"""Adapter contracts. Model-backed and deterministic adapters implement the same Protocols."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.tools.registry import ToolSchema

# Inbound audio: PCM16 little-endian, mono, 16 kHz, 512-sample (32 ms) frames — Silero VAD's frame.
SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2


class VADSignal(StrEnum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


class VAD(Protocol):
    name: str
    # Silence needed before SPEECH_END (the endpointing window).
    silence_ms: float

    def process(self, frame: bytes) -> VADSignal | None: ...

    def reset(self) -> None: ...


class STT(Protocol):
    name: str

    async def transcribe(self, audio: bytes) -> str: ...


class TextDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str


class ToolCallRequest(BaseModel):
    """A tool call as the LLM asked for it. Untrusted: validated by the ToolExecutor."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


LLMChunk = TextDelta | ToolCallRequest


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class LLM(Protocol):
    name: str

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> AsyncIterator[LLMChunk]: ...


class TTS(Protocol):
    name: str
    sample_rate: int

    def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """PCM16 mono chunks for one sentence."""
        ...


@dataclass(frozen=True)
class AudioOut:
    turn_id: str
    sentence_id: int
    index: int
    data: bytes
    # The client ACKs a sentence once the chunk marked is_last has finished playing.
    is_last: bool
    sample_rate: int


class Transport(Protocol):
    """Server → client. Client → server messages are delivered by calling VoiceSession.handle_*."""

    async def send_audio(self, chunk: AudioOut) -> None: ...

    async def send_flush(self, turn_id: str) -> None:
        """Client must stop playback, drop queued audio for turn_id, and reply flushed."""
        ...


@dataclass(frozen=True)
class Adapters:
    llm: LLM
    tts: TTS
    # None until a real VAD/STT is configured; audio input is then rejected with an ERROR event.
    vad: VAD | None = None
    stt: STT | None = None
