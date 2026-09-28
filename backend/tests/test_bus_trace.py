from app.bus import EventBus
from app.events import (
    AgentTurnStarted,
    Error,
    EventAdapter,
    ToolCallFinished,
    TranscriptFinal,
    TtsStarted,
    UserSpeechEnded,
    UserSpeechStarted,
)
from app.trace import TraceStore, stage_timings


def speech(session_id: str) -> UserSpeechStarted:
    return UserSpeechStarted(session_id=session_id)


def test_seq_is_strictly_increasing_per_session() -> None:
    bus = EventBus()
    a = [bus.publish(speech("a")).seq for _ in range(3)]
    b = [bus.publish(speech("b")).seq for _ in range(2)]
    assert a == [1, 2, 3]
    assert b == [1, 2]


def test_trace_keeps_order_and_isolates_sessions() -> None:
    bus, trace = EventBus(), TraceStore()
    bus.subscribe(trace)
    for sid in ("a", "b", "a"):
        bus.publish(speech(sid))
    assert [e.seq for e in trace.events("a")] == [1, 2]
    assert [e.seq for e in trace.events("b")] == [1]
    assert sorted(trace.sessions()) == ["a", "b"]


def test_trace_jsonl_round_trips() -> None:
    bus, trace = EventBus(), TraceStore()
    bus.subscribe(trace)
    bus.publish(speech("a"))
    bus.publish(TranscriptFinal(session_id="a", text="hi", source="text", stt_ms=None))
    lines = trace.to_jsonl("a").splitlines()
    assert [EventAdapter.validate_json(line) for line in lines] == trace.events("a")


def test_failing_subscriber_does_not_stop_delivery() -> None:
    bus, trace = EventBus(), TraceStore()

    def broken(event) -> None:
        if not isinstance(event, Error):
            raise ValueError("subscriber bug")

    bus.subscribe(broken)
    bus.subscribe(trace)
    bus.publish(speech("a"))
    events = trace.events("a")
    assert isinstance(events[0], UserSpeechStarted)
    assert isinstance(events[1], Error) and events[1].stage == "bus"
    assert "subscriber bug" in events[1].message


def test_always_failing_subscriber_does_not_recurse() -> None:
    bus, trace = EventBus(), TraceStore()

    def always_broken(event) -> None:
        raise ValueError("always")

    bus.subscribe(always_broken)
    bus.subscribe(trace)
    bus.publish(speech("a"))
    assert [e.event_type for e in trace.events("a")] == ["USER_SPEECH_STARTED", "ERROR"]


def test_unsubscribe() -> None:
    bus, trace = EventBus(), TraceStore()
    unsubscribe = bus.subscribe(trace)
    unsubscribe()
    bus.publish(speech("a"))
    assert trace.events("a") == []


def test_stage_timings_from_turn_events() -> None:
    t0 = 100.0
    sid, tid = "a", "t"
    events = [
        UserSpeechEnded(session_id=sid, turn_id=tid, duration_ms=900, endpoint_ms=96, mono=t0),
        TranscriptFinal(
            session_id=sid, turn_id=tid, text="x", source="stt", stt_ms=200, mono=t0 + 0.200
        ),
        AgentTurnStarted(session_id=sid, turn_id=tid, user_text="x", mono=t0 + 0.201),
        ToolCallFinished(
            session_id=sid,
            turn_id=tid,
            call_id="c",
            tool="lookup",
            result=None,
            duration_ms=150,
            mono=t0 + 0.500,
        ),
        # First sentence ready at +0.801, first chunk sent 50 ms later.
        TtsStarted(
            session_id=sid, turn_id=tid, sentence_id=1, text="x", synth_ms=50, mono=t0 + 0.851
        ),
    ]
    timings = stage_timings(events)
    assert timings.endpoint_ms == 96
    assert timings.stt_ms == 200.0
    assert timings.tool_ms == 150.0
    assert timings.tts_ms == 50.0
    assert timings.llm_ms == 450.0  # 600 ms from turn start to sentence ready, minus 150 ms tool
    assert timings.total_ms == 851.0


def test_stage_timings_for_typed_input_have_no_stt_or_endpoint() -> None:
    events = [
        TranscriptFinal(
            session_id="a", turn_id="t", text="x", source="text", stt_ms=None, mono=1.0
        ),
        AgentTurnStarted(session_id="a", turn_id="t", user_text="x", mono=1.0),
        TtsStarted(session_id="a", turn_id="t", sentence_id=1, text="x", synth_ms=10, mono=1.3),
    ]
    timings = stage_timings(events)
    assert timings.endpoint_ms is None and timings.stt_ms is None and timings.tool_ms is None
    assert timings.total_ms == 300.0
    assert timings.llm_ms == 290.0
