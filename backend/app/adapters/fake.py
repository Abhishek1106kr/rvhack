"""Deterministic adapters. They implement the Protocols in base.py exactly as model-backed
adapters will, so tests and evals drive the real VoiceSession.

Timing in tests is controlled with asyncio.Event gates, not sleeps.
"""

import asyncio
import math
import zlib
from array import array
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.adapters.base import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    AudioOut,
    LLMChunk,
    Message,
    TextDelta,
    VADSignal,
)
from app.tools.registry import ToolSchema

if TYPE_CHECKING:
    from app.agent.orchestrator import VoiceSession

# ── VAD ───────────────────────────────────────────────────────────────────────────────────

SPEECH, SILENCE = 1, 0


def frames(kind: int, count: int) -> list[bytes]:
    """Tagged fake audio frames: byte 0 says whether the frame is speech."""
    return [bytes([kind]) + bytes(FRAME_BYTES - 1) for _ in range(count)]


class FakeVAD:
    """Speech = frames whose first byte is 1. Ends after `silence_frames` silent frames."""

    name = "fake-vad"

    def __init__(self, silence_frames: int = 3) -> None:
        self._silence_frames = silence_frames
        self.silence_ms = silence_frames * FRAME_SAMPLES / SAMPLE_RATE * 1000
        self.reset()

    def reset(self) -> None:
        self._speaking = False
        self._silent_run = 0

    def process(self, frame: bytes) -> VADSignal | None:
        is_speech = bool(frame) and frame[0] == SPEECH
        if is_speech:
            self._silent_run = 0
            if not self._speaking:
                self._speaking = True
                return VADSignal.SPEECH_START
            return None
        if self._speaking:
            self._silent_run += 1
            if self._silent_run >= self._silence_frames:
                self.reset()
                return VADSignal.SPEECH_END
        return None


# ── STT ───────────────────────────────────────────────────────────────────────────────────


class ScriptedSTT:
    """Returns scripted transcripts in call order. An Exception in the script is raised."""

    name = "scripted-stt"

    def __init__(self, script: Sequence[str | Exception], gate: asyncio.Event | None = None):
        self._script = list(script)
        self._gate = gate
        self.calls: list[bytes] = []

    async def transcribe(self, audio: bytes) -> str:
        self.calls.append(audio)
        if self._gate is not None:
            await self._gate.wait()
        if not self._script:
            raise RuntimeError("ScriptedSTT: script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ── LLM ───────────────────────────────────────────────────────────────────────────────────


@dataclass
class Gate:
    """Placed in an LLM script: generation pauses here until the event is set."""

    event: asyncio.Event = field(default_factory=asyncio.Event)

    def open(self) -> None:
        self.event.set()


ScriptItem = LLMChunk | Gate | Exception


class ScriptedLLM:
    """Each stream() call plays the next script. Records inputs and cancellations."""

    name = "scripted-llm"

    def __init__(self, scripts: Sequence[Sequence[ScriptItem]]) -> None:
        self._scripts = [list(s) for s in scripts]
        self.calls: list[list[Message]] = []
        self.tool_schemas: list[list[ToolSchema]] = []
        self.cancelled = 0

    async def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> AsyncIterator[LLMChunk]:
        self.calls.append(list(messages))
        self.tool_schemas.append(list(tools))
        if not self._scripts:
            raise RuntimeError("ScriptedLLM: no script left for this call")
        script = self._scripts.pop(0)
        try:
            for item in script:
                if isinstance(item, Gate):
                    await item.event.wait()
                elif isinstance(item, Exception):
                    raise item
                else:
                    yield item
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class EchoLLM:
    """Development LLM with no model: acknowledges what it heard in three sentences.

    Exists so the realtime path (streaming, TTS, playback ACKs, barge-in) can be exercised
    end-to-end before a model is installed. Never calls tools.
    """

    name = "echo-llm"

    def __init__(self, word_delay_s: float = 0.03) -> None:
        self._word_delay_s = word_delay_s

    async def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> AsyncIterator[LLMChunk]:
        heard = next((m.content for m in reversed(messages) if m.role == "user"), "")
        reply = (
            f"I heard: {heard.rstrip('.!?')}. "
            "This reply comes from the echo adapter, not a language model. "
            "Interrupt me at any point to test barge-in."
        )
        for word in reply.split(" "):
            await asyncio.sleep(self._word_delay_s)
            yield TextDelta(text=word + " ")


# ── TTS ───────────────────────────────────────────────────────────────────────────────────


class ToneTTS:
    """Audible placeholder speech: a soft tone per sentence, 180 ms per word.

    Pitch is derived from the sentence text, so sentence boundaries are audible and
    output is deterministic. `gate`, when set, is awaited before every chunk after the
    first, so a test can hold a sentence mid-synthesis.
    """

    name = "tone-tts"
    sample_rate = SAMPLE_RATE

    def __init__(
        self,
        chunk_ms: int = 100,
        ms_per_word: int = 180,
        chunk_delay_s: float = 0.0,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._chunk_samples = self.sample_rate * chunk_ms // 1000
        self._ms_per_word = ms_per_word
        self._chunk_delay_s = chunk_delay_s
        self._gate = gate
        self.cancelled = 0

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        total = self.sample_rate * max(400, self._ms_per_word * len(text.split())) // 1000
        freq = 196 + (zlib.crc32(text.encode()) % 6) * 33
        fade = self.sample_rate // 100  # 10 ms fade in/out avoids clicks
        try:
            for start in range(0, total, self._chunk_samples):
                if start > 0 and self._gate is not None:
                    await self._gate.wait()
                if self._chunk_delay_s:
                    await asyncio.sleep(self._chunk_delay_s)
                samples = array("h")
                for n in range(start, min(start + self._chunk_samples, total)):
                    envelope = min(1.0, n / fade, (total - n) / fade)
                    value = 0.12 * envelope * math.sin(2 * math.pi * freq * n / self.sample_rate)
                    samples.append(int(value * 32767))
                yield samples.tobytes()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


# ── client (Transport) ────────────────────────────────────────────────────────────────────


class FakeClient:
    """Plays the browser's role. Playback is driven by the test, sentence by sentence.

    play(sentence_id)        the sentence finished playing; ACK is sent.
    play_unreported(id)      it finished playing but the ACK is still in flight;
                             it is reported only in the flush reply.
    """

    def __init__(self, respond_to_flush: bool = True) -> None:
        self.session: VoiceSession | None = None
        self.audio: list[AudioOut] = []
        self.flushes: list[str] = []
        # Audio chunks and flushes in the order the server sent them.
        self.log: list[AudioOut | str] = []
        self.respond_to_flush = respond_to_flush
        self._played: dict[str, int] = {}

    async def send_audio(self, chunk: AudioOut) -> None:
        self.audio.append(chunk)
        self.log.append(chunk)

    async def send_flush(self, turn_id: str) -> None:
        self.flushes.append(turn_id)
        self.log.append(f"flush:{turn_id}")
        if self.respond_to_flush:
            assert self.session is not None
            await self.session.handle_flushed(turn_id, self._played.get(turn_id))

    async def play(self, turn_id: str, sentence_id: int) -> None:
        self.play_unreported(turn_id, sentence_id)
        assert self.session is not None
        await self.session.handle_ack(turn_id, sentence_id)

    def play_unreported(self, turn_id: str, sentence_id: int) -> None:
        self._played[turn_id] = max(sentence_id, self._played.get(turn_id, 0))

    def audio_after_flush(self, turn_id: str) -> list[AudioOut]:
        """Chunks for turn_id sent after its flush. Must always be empty."""
        marker = f"flush:{turn_id}"
        if marker not in self.log:
            return []
        after = self.log[self.log.index(marker) + 1 :]
        return [c for c in after if isinstance(c, AudioOut) and c.turn_id == turn_id]

    def sentence_complete(self, turn_id: str, sentence_id: int) -> bool:
        """True once the terminal (is_last) chunk of the sentence has been received."""
        return any(
            c.turn_id == turn_id and c.sentence_id == sentence_id and c.is_last for c in self.audio
        )
