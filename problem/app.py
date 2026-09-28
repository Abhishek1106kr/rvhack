"""The assembled product-assistant agent: ARC runtime + this problem's catalog, tools, prompt.

Run (from backend/, repo root on PYTHONPATH): `make dev` or
    PYTHONPATH=.. uv run uvicorn problem.app:app
"""

import logging
import os
from pathlib import Path

from app.adapters.base import Adapters, Message
from app.adapters.ollama import OllamaLLM
from app.adapters.piper_tts import PiperTTS
from app.adapters.silero import SileroModel
from app.adapters.whisper import WhisperSTT
from app.agent.orchestrator import SessionConfig
from app.main import create_app
from app.runtime import Runtime
from app.tools.registry import ToolRegistry
from problem.knowledge import Catalog
from problem.prompts import system_prompt
from problem.tools import build_tools
from problem.workflows import build_planner

logging.basicConfig(level=logging.INFO)

MODELS = Path(os.environ.get("ARC_MODELS_DIR", Path(__file__).resolve().parents[1] / "models"))
# 1.5b: passes the live eval at ~half the latency of 3b on a 4-core CPU. Use 3b with a GPU.
LLM_MODEL = os.environ.get("ARC_LLM_MODEL", "qwen2.5:1.5b")
WHISPER_MODEL = os.environ.get("ARC_WHISPER_MODEL", "base.en")

catalog = Catalog.load()
tools = ToolRegistry(build_tools(catalog))
prompt = system_prompt(catalog)

# Loaded once, shared by every session. VAD state is per session.
silero = SileroModel(MODELS / "silero_vad.onnx")
stt = WhisperSTT(
    WHISPER_MODEL, download_root=MODELS / "whisper", initial_prompt=catalog.stt_vocabulary()
)
tts = PiperTTS(MODELS / "piper" / "en_US-lessac-medium.onnx")
llm = OllamaLLM(LLM_MODEL, base_url=os.environ.get("ARC_OLLAMA_URL", "http://127.0.0.1:11434"))


def make_adapters() -> Adapters:
    return Adapters(llm=llm, tts=tts, vad=silero.new_vad(), stt=stt)


async def warm_up() -> None:
    """Load the model and cache the system prompt + tool schemas (the slow part on CPU)."""
    await llm.warm_up()
    messages = [Message(role="system", content=prompt), Message(role="user", content="hello")]
    async for _ in llm.stream(messages, tools.schemas()):
        pass


runtime_config = SessionConfig(
    system_prompt=prompt,
    planner=build_planner(catalog),
    filler_phrases=("Let me check that.", "One moment.", "Sure, checking."),
)

app = create_app(
    Runtime(make_adapters=make_adapters, tools=tools, config=runtime_config, warm_up=warm_up)
)
