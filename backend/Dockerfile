# ARC backend: runtime + problem layer, with speech model weights baked in.
# Built from the repo root:  docker build -f backend/Dockerfile .
# The LLM is not in this image; it is served by the ollama service.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# libgomp: OpenMP runtime used by ctranslate2 (faster-whisper) and onnxruntime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv

WORKDIR /srv/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH=/srv/backend/.venv/bin:$PATH

# Speech models, fetched at build time so a running deployment needs no network.
ARG SILERO_URL=https://github.com/snakers4/silero-vad/raw/v6.2/src/silero_vad/data/silero_vad.onnx
ARG PIPER_URL=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium
ARG PIPER_VOICE=en_US-lessac-medium
ARG WHISPER_MODEL=base.en
RUN mkdir -p /models/piper /models/whisper \
    && curl -fsSL -o /models/silero_vad.onnx "$SILERO_URL" \
    && curl -fsSL -o "/models/piper/$PIPER_VOICE.onnx" "$PIPER_URL/$PIPER_VOICE.onnx" \
    && curl -fsSL -o "/models/piper/$PIPER_VOICE.onnx.json" "$PIPER_URL/$PIPER_VOICE.onnx.json" \
    && python -c "from faster_whisper import WhisperModel; WhisperModel('$WHISPER_MODEL', device='cpu', compute_type='int8', download_root='/models/whisper')"

COPY backend/app ./app
COPY problem /srv/problem

ENV ARC_MODELS_DIR=/models \
    ARC_WHISPER_MODEL=$WHISPER_MODEL \
    PYTHONPATH=/srv/backend:/srv \
    HF_HUB_OFFLINE=1

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1
CMD ["uvicorn", "problem.app:app", "--host", "0.0.0.0", "--port", "8000"]
