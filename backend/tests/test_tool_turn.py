"""Tool calls inside a live turn. The LLM requests; the runtime validates, runs, records."""

import json

from pydantic import BaseModel

from app.adapters.base import TextDelta, ToolCallRequest
from app.agent.orchestrator import SessionConfig
from app.events import AgentTurnFinished, Error, ToolCallFailed, ToolCallFinished, ToolErrorKind
from app.session.state import SessionState as S
from app.tools.registry import ToolRisk, ToolSpec


class Query(BaseModel):
    city: str


async def forecast(args: Query) -> dict:
    return {"city": args.city, "high_c": 31}


async def broken(args: Query) -> dict:
    raise ConnectionError("weather service unreachable")


def tool(handler=forecast) -> ToolSpec:
    return ToolSpec(
        name="forecast",
        description="Forecast for a city.",
        input_model=Query,
        handler=handler,
        timeout_s=1.0,
        risk=ToolRisk.READ_ONLY,
    )


def request(arguments: dict, name: str = "forecast") -> ToolCallRequest:
    return ToolCallRequest(call_id="c1", name=name, arguments=arguments)


async def finish(h, turn_index: int = 0) -> AgentTurnFinished:
    turn_id = await h.until_sentence_sent(turn_index, 1)
    await h.client.play(turn_id, 1)
    await h.until(lambda: len(h.events(AgentTurnFinished)) > turn_index)
    await h.until_state(S.LISTENING)
    return h.events(AgentTurnFinished)[turn_index]


def tool_message(h, call_index: int = 1) -> dict:
    message = h.llm.calls[call_index][-1]
    assert message.role == "tool"
    return json.loads(message.content)


async def test_tool_success(make_session) -> None:
    h = await make_session(
        [[request({"city": "Pune"})], [TextDelta(text="It will be 31 degrees.")]],
        tools=[tool()],
    )
    await h.session.handle_text("weather in pune?")
    finished = await finish(h)

    assert h.states() == [
        S.LISTENING,
        S.THINKING,
        S.TOOL_EXECUTION,
        S.THINKING,
        S.SPEAKING,
        S.LISTENING,
    ]
    assert h.llm.tool_schemas[0][0].name == "forecast"
    assert tool_message(h) == {"ok": True, "output": {"city": "Pune", "high_c": 31}}
    assert [e.result for e in h.events(ToolCallFinished)] == [{"city": "Pune", "high_c": 31}]
    assert finished.spoken_text == "It will be 31 degrees."
    assert finished.timings.tool_ms is not None


async def test_tool_failure_does_not_kill_the_session(make_session) -> None:
    h = await make_session(
        [
            [request({"city": "Pune"})],
            [TextDelta(text="The weather service is down.")],
            [TextDelta(text="Still here.")],
        ],
        tools=[tool(broken)],
    )
    await h.session.handle_text("weather?")
    await finish(h)
    (failed,) = h.events(ToolCallFailed)
    assert failed.error_kind is ToolErrorKind.EXCEPTION
    assert tool_message(h)["ok"] is False

    await h.session.handle_text("are you ok?")
    assert (await finish(h, 1)).spoken_text == "Still here."


async def test_invalid_arguments_reported_to_llm(make_session) -> None:
    h = await make_session(
        [[request({"town": "Pune"})], [TextDelta(text="Which city?")]],
        tools=[tool()],
    )
    await h.session.handle_text("weather?")
    await finish(h)
    assert h.events(ToolCallFailed)[0].error_kind is ToolErrorKind.INVALID_ARGUMENTS
    assert tool_message(h)["error"] == "invalid_arguments"
    assert [e.event_type for e in h.all_events()].count("TOOL_CALL_STARTED") == 0


async def test_unknown_tool_reported_to_llm(make_session) -> None:
    h = await make_session(
        [[request({}, name="transfer_money")], [TextDelta(text="I can't do that.")]],
        tools=[tool()],
    )
    await h.session.handle_text("send money")
    await finish(h)
    assert tool_message(h)["error"] == "unknown_tool"


async def test_tool_round_limit(make_session) -> None:
    h = await make_session(
        [[request({"city": "A"})], [request({"city": "B"})]],
        tools=[tool()],
        config=SessionConfig(max_tool_rounds=1),
    )
    await h.session.handle_text("loop forever")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    await h.until_state(S.LISTENING)
    assert len(h.events(ToolCallFinished)) == 1
    assert "tool-call limit" in h.events(Error)[0].message
    assert h.states()[-1] is S.LISTENING


async def test_planner_grounds_the_llm_before_it_speaks(make_session) -> None:
    def planner(messages) -> list[ToolCallRequest]:
        assert messages[-1].content == "weather in pune?"
        return [request({"city": "Pune"})]

    h = await make_session(
        [[TextDelta(text="31 degrees in Pune.")]],
        tools=[tool()],
        config=SessionConfig(planner=planner),
    )
    await h.session.handle_text("weather in pune?")
    await finish(h)

    (started,) = [e for e in h.all_events() if e.event_type == "TOOL_CALL_STARTED"]
    assert started.requested_by == "planner"
    # One LLM call, and it already has the tool result: no tool-call round-trip.
    assert len(h.llm.calls) == 1
    assert json.loads(h.llm.calls[0][-1].content)["output"]["high_c"] == 31
    assert h.states() == [
        S.LISTENING,
        S.THINKING,
        S.TOOL_EXECUTION,
        S.THINKING,
        S.SPEAKING,
        S.LISTENING,
    ]


async def test_planner_crash_is_reported_and_llm_still_answers(make_session) -> None:
    def planner(messages):
        raise ValueError("bad regex")

    h = await make_session(
        [[TextDelta(text="Still answering.")]],
        tools=[tool()],
        config=SessionConfig(planner=planner),
    )
    await h.session.handle_text("hello")
    finished = await finish(h)
    (error,) = h.events(Error)
    assert error.stage == "planner" and "bad regex" in error.message
    assert finished.spoken_text == "Still answering."
