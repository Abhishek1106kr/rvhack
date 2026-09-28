"""faster-whisper STT. Batch transcription of one endpointed utterance (not streaming)."""

import asyncio
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel


class WhisperSTT:
    def __init__(
        self,
        model: str = "base.en",
        download_root: Path | None = None,
        compute_type: str = "int8",
        cpu_threads: int = 0,
        language: str = "en",
        initial_prompt: str | None = None,
    ) -> None:
        """initial_prompt biases recognition toward domain vocabulary (supplied by problem/)."""
        self.name = f"faster-whisper:{model}"
        self._model = WhisperModel(
            model,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            download_root=str(download_root) if download_root else None,
        )
        self._language = language
        self._initial_prompt = initial_prompt
        # One transcription at a time: parallel runs only fight over the same CPU cores.
        self._lock = asyncio.Lock()

    async def transcribe(self, audio: bytes) -> str:
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        async with self._lock:
            # If the turn is cancelled, the worker thread finishes and its result is dropped.
            return await asyncio.to_thread(self._run, samples)

    def _run(self, samples: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            samples,
            language=self._language,
            beam_size=1,
            condition_on_previous_text=False,
            initial_prompt=self._initial_prompt,
            vad_filter=False,  # utterances are already endpointed by our VAD
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
