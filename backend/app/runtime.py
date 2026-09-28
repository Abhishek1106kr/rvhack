"""What a running ARC server is assembled from.

The problem layer builds its own Runtime (tools, prompt, adapters) and passes it to
app.main.create_app; the runtime never imports problem code.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.adapters.base import Adapters
from app.adapters.fake import EchoLLM, ToneTTS
from app.agent.orchestrator import SessionConfig
from app.bus import EventBus
from app.tools.registry import ToolRegistry
from app.trace import TraceStore


def dev_adapters() -> Adapters:
    """No models installed: echo LLM + tone TTS, no microphone input."""
    return Adapters(llm=EchoLLM(), tts=ToneTTS())


@dataclass
class Runtime:
    make_adapters: Callable[[], Adapters] = dev_adapters
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    config: SessionConfig = field(default_factory=SessionConfig)
    bus: EventBus = field(default_factory=EventBus)
    trace: TraceStore = field(default_factory=TraceStore)
    # Run once at server start, in the background (e.g. load the LLM so turn one isn't slow).
    warm_up: Callable[[], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        self.bus.subscribe(self.trace)
