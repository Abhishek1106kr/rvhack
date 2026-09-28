"""Piper TTS. Synthesizes one sentence on a worker thread and streams it in small chunks."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from piper import PiperVoice

_CHUNK_S = 0.25


class PiperTTS:
    def __init__(self, voice_path: Path) -> None:
        self._voice = PiperVoice.load(voice_path)
        self.name = f"piper:{voice_path.stem}"
        self.sample_rate: int = self._voice.config.sample_rate
        self._lock = asyncio.Lock()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        async with self._lock:
            audio = await asyncio.to_thread(self._run, text)
        step = int(self.sample_rate * _CHUNK_S) * 2
        for start in range(0, len(audio), step):
            yield audio[start : start + step]

    def _run(self, text: str) -> bytes:
        return b"".join(chunk.audio_int16_bytes for chunk in self._voice.synthesize(text))
