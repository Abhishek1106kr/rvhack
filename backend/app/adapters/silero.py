"""Silero VAD (v5 ONNX) via onnxruntime — no torch dependency."""

from pathlib import Path

import numpy as np
import onnxruntime as ort

from app.adapters.base import FRAME_SAMPLES, SAMPLE_RATE, VADSignal

# The v5 model expects each 512-sample frame prefixed with the previous 64 samples.
_CONTEXT = 64
_FRAME_MS = FRAME_SAMPLES / SAMPLE_RATE * 1000


class SileroModel:
    """The ONNX session, loaded once and shared. Each session gets its own SileroVAD state."""

    def __init__(self, model_path: Path) -> None:
        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )

    def new_vad(self, **kwargs: float) -> "SileroVAD":
        return SileroVAD(self, **kwargs)


class SileroVAD:
    name = "silero-vad"

    def __init__(
        self,
        model: SileroModel,
        threshold: float = 0.5,
        min_speech_ms: float = 96,
        silence_ms: float = 480,
    ) -> None:
        self._session = model.session
        self._start_threshold = threshold
        # Hysteresis: speech ends only when probability drops clearly below the start threshold.
        self._end_threshold = max(threshold - 0.15, 0.05)
        self._min_speech_frames = max(1, round(min_speech_ms / _FRAME_MS))
        self._silence_frames = max(1, round(silence_ms / _FRAME_MS))
        self.silence_ms = self._silence_frames * _FRAME_MS
        self.last_probability = 0.0
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT), dtype=np.float32)
        self._speaking = False
        self._speech_run = 0
        self._silence_run = 0

    def process(self, frame: bytes) -> VADSignal | None:
        if len(frame) != FRAME_SAMPLES * 2:
            raise ValueError(f"expected {FRAME_SAMPLES * 2}-byte frames, got {len(frame)}")
        probability = self._infer(frame)
        self.last_probability = probability

        if not self._speaking:
            self._speech_run = self._speech_run + 1 if probability >= self._start_threshold else 0
            if self._speech_run >= self._min_speech_frames:
                self._speaking = True
                self._silence_run = 0
                return VADSignal.SPEECH_START
            return None

        self._silence_run = self._silence_run + 1 if probability < self._end_threshold else 0
        if self._silence_run >= self._silence_frames:
            self._speaking = False
            self._speech_run = 0
            return VADSignal.SPEECH_END
        return None

    def _infer(self, frame: bytes) -> float:
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        x = np.concatenate([self._context, samples[np.newaxis, :]], axis=1)
        output, self._state = self._session.run(
            None, {"input": x, "state": self._state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)}
        )
        self._context = x[:, -_CONTEXT:]
        return float(output[0][0])
