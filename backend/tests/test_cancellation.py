"""Cancellation paths: what stops, what must not stop, and that the session always recovers."""

import asyncio

from pydantic import BaseModel

from app.adapters.base import TextDelta, ToolCallRequest
from app.adapters.fake import SILENCE, SPEECH, FakeClient, Gate, ScriptedSTT, frames
from app.agent.orchestrator import SessionConfig
from app.events import (
    AgentTurnFinished,
    AgentTurnStarted,
    Error,
    ToolCallFailed,
    ToolCallFinished,
    ToolErrorKind,
    TranscriptFinal,
    UserBargeIn,
    UserSpeechStarted,
)
from app.session.state import SessionState as S
from app.tools.registry import ToolRisk, ToolSpec

OK = [TextDelta(text="Ok.")]


class Order(BaseModel):
    item: str


def blocking_tool(risk: ToolRisk, release: asyncio.Event, executed: list[str]) -> ToolSpec:
    async def handler(args: Order) -> dict:
        await release.wait()
        executed.append(args.item)
        return {"placed": args.item}

    return ToolSpec(
        name="place_order",
        description="Place an order.",
        input_model=Order,
        handler=handler,
        timeout_s=5.0,
        risk=risk,
    )


async def test_barge_in_while_thinking_cancels_generation(make_session) -> None:
    h = await make_session([[Gate(), TextDelta(text="Never said.")], [TextDelta(text="Ok.")]])
    await h.session.handle_text("first")
    await h.until_state(S.THINKING)

    await h.session.handle_text("never mind")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    (barge_in,) = h.events(UserBargeIn)
    assert barge_in.interrupted_state is S.THINKING
    assert barge_in.cancelled == ["generation"]
    assert h.llm.cancelled == 1
    assert h.client.flushes == []  # no audio was ever sent, nothing to flush
    assert h.states()[:5] == [S.LISTENING, S.THINKING, S.INTERRUPTED, S.LISTENING, S.THINKING]


async def test_barge_in_cancels_read_only_tool(make_session) -> None:
    release, executed = asyncio.Event(), []
    h = await make_session(
        [[ToolCallRequest(call_id="c1", name="place_order", arguments={"item": "tea"})], OK],
        tools=[blocking_tool(ToolRisk.READ_ONLY, release, executed)],
    )
    await h.session.handle_text("order tea")
    await h.until_state(S.TOOL_EXECUTION)

    await h.session.handle_text("stop")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    assert "tool" in h.events(UserBargeIn)[0].cancelled
    (failed,) = h.events(ToolCallFailed)
    assert failed.error_kind is ToolErrorKind.CANCELLED
    release.set()
    await asyncio.sleep(0.01)
    assert executed == []
    assert all(m.role != "tool" for m in h.session.conversation.messages)


async def test_barge_in_never_cancels_side_effecting_tool(make_session) -> None:
    release, executed = asyncio.Event(), []
    h = await make_session(
        [
            [ToolCallRequest(call_id="c1", name="place_order", arguments={"item": "tea"})],
            [TextDelta(text="Your tea order went through.")],
        ],
        tools=[blocking_tool(ToolRisk.SIDE_EFFECT, release, executed)],
    )
    await h.session.handle_text("order tea")
    await h.until_state(S.TOOL_EXECUTION)
    await h.session.handle_text("actually wait")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    assert h.events(ToolCallFailed) == []
    assert "tool" not in h.events(UserBargeIn)[0].cancelled  # it was left running

    await asyncio.sleep(0.01)
    assert len(h.llm.calls) == 1  # next turn is held until the side effect resolves

    release.set()
    await h.until(lambda: bool(h.events(ToolCallFinished)))
    assert executed == ["tea"]

    # The side effect is in history, so the next LLM call knows the order was placed.
    await h.until(lambda: len(h.llm.calls) == 2)
    history = h.llm.calls[1]
    assert [m.role for m in history] == ["user", "assistant", "tool", "user"]
    assert history[1].tool_calls[0].name == "place_order"


async def test_flush_timeout_still_recovers(make_session) -> None:
    h = await make_session(
        [[TextDelta(text="One. Two. "), Gate()], OK],
        client=FakeClient(respond_to_flush=False),
        config=SessionConfig(flush_timeout_s=0.05),
    )
    await h.session.handle_text("go")
    turn_id = await h.until_sentence_sent(0, 2)
    await h.client.play(turn_id, 1)

    await h.session.handle_text("stop")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    await h.until(lambda: S.LISTENING in h.states()[3:])
    (error,) = h.events(Error)
    assert error.stage == "transport" and error.recoverable
    assert h.events(AgentTurnFinished)[0].spoken_text == "One."


async def test_ack_after_interrupted_turn_is_rejected(make_session) -> None:
    h = await make_session([[TextDelta(text="One. Two. "), Gate()], OK])
    await h.session.handle_text("go")
    turn_id = await h.until_sentence_sent(0, 2)
    await h.session.handle_text("stop")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))

    await h.session.handle_ack(turn_id, 2)  # arrives after the turn was finalized
    late = [e for e in h.all_events() if e.event_type == "PLAYBACK_ACKED"]
    assert late[-1].accepted is False
    assert h.events(AgentTurnFinished)[0].spoken_text == ""


async def test_resumed_speech_is_merged_into_one_utterance(make_session) -> None:
    gate = asyncio.Event()
    stt = ScriptedSTT(["two coffees please"], gate=gate)
    h = await make_session([[TextDelta(text="Two coffees, noted.")]], stt=stt)

    for frame in frames(SPEECH, 3) + frames(SILENCE, 3):  # "two coffees..." pause
        await h.session.handle_audio(frame)
    await h.until(lambda: len(stt.calls) == 1)
    for frame in frames(SPEECH, 2) + frames(SILENCE, 3):  # "...please"
        await h.session.handle_audio(frame)
    await h.until(lambda: len(stt.calls) == 2)
    gate.set()
    await h.until(lambda: bool(h.events(AgentTurnStarted)))

    assert len(stt.calls[1]) == len(stt.calls[0]) + 5 * 1024
    assert len(h.events(TranscriptFinal)) == 1
    speech_turns = {e.turn_id for e in h.events(UserSpeechStarted)}
    assert speech_turns == {h.events(AgentTurnStarted)[0].turn_id}
