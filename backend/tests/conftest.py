import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pytest

from app.adapters.base import Adapters
from app.adapters.fake import FakeClient, FakeVAD, ScriptedLLM, ScriptedSTT, ScriptItem, ToneTTS
from app.agent.orchestrator import SessionConfig, VoiceSession
from app.bus import EventBus
from app.events import AgentTurnStarted, Event, SessionStateChanged
from app.session.state import SessionState
from app.tools.registry import ToolRegistry, ToolSpec
from app.trace import TraceStore


@dataclass
class Harness:
    session: VoiceSession
    client: FakeClient
    trace: TraceStore
    llm: ScriptedLLM
    tts: ToneTTS
    stt: ScriptedSTT

    def events[T](self, kind: type[T]) -> list[T]:
        return [e for e in self.trace.events(self.session.session_id) if isinstance(e, kind)]

    def all_events(self) -> list[Event]:
        return self.trace.events(self.session.session_id)

    def states(self) -> list[SessionState]:
        return [e.current for e in self.events(SessionStateChanged)]

    def turn_ids(self) -> list[str]:
        return [e.turn_id for e in self.events(AgentTurnStarted) if e.turn_id]

    async def until(self, predicate: Callable[[], bool], limit_s: float = 2.0) -> None:
        """Yield to the loop until predicate holds. Waits on a condition, never a fixed time."""
        async with asyncio.timeout(limit_s):
            # Predicates inspect arbitrary session state; there is no single Event to await.
            while not predicate():  # noqa: ASYNC110
                await asyncio.sleep(0.001)

    async def until_state(self, state: SessionState) -> None:
        await self.until(lambda: self.session.state is state)

    async def until_sentence_sent(self, turn_index: int, sentence_id: int) -> str:
        await self.until(lambda: len(self.turn_ids()) > turn_index)
        turn_id = self.turn_ids()[turn_index]
        await self.until(lambda: self.client.sentence_complete(turn_id, sentence_id))
        return turn_id


@pytest.fixture
async def make_session():
    sessions: list[VoiceSession] = []

    async def factory(
        llm_scripts: Sequence[Sequence[ScriptItem]] = (),
        *,
        stt_script: Sequence[str | Exception] = (),
        stt: ScriptedSTT | None = None,
        tools: list[ToolSpec] | None = None,
        tts: ToneTTS | None = None,
        client: FakeClient | None = None,
        config: SessionConfig | None = None,
        with_audio: bool = True,
    ) -> Harness:
        bus = EventBus()
        trace = TraceStore()
        bus.subscribe(trace)
        llm = ScriptedLLM(llm_scripts)
        stt = stt or ScriptedSTT(stt_script)
        tts = tts or ToneTTS(ms_per_word=100)
        client = client or FakeClient()
        session = VoiceSession(
            session_id=f"s{len(sessions) + 1}",
            bus=bus,
            adapters=Adapters(
                llm=llm,
                tts=tts,
                vad=FakeVAD() if with_audio else None,
                stt=stt if with_audio else None,
            ),
            transport=client,
            tools=ToolRegistry(tools),
            config=config,
        )
        client.session = session
        sessions.append(session)
        await session.start()
        return Harness(session, client, trace, llm, tts, stt)

    yield factory
    for session in sessions:
        await session.close()
