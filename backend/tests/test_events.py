import typing

import pytest
from pydantic import ValidationError

from app.events import (
    AdapterNames,
    AgentTurnFinished,
    AgentTurnStarted,
    Error,
    Event,
    EventAdapter,
    PlaybackAcked,
    SessionStarted,
    SessionStateChanged,
    StageTimings,
    ToolCallFailed,
    ToolCallFinished,
    ToolCallStarted,
    ToolErrorKind,
    TranscriptFinal,
    TranscriptPartial,
    TtsStarted,
    TtsStopped,
    TurnOutcome,
    UserBargeIn,
    UserSpeechEnded,
    UserSpeechStarted,
)
from app.session.state import SessionState, TransitionReason

SID = "s1"

ONE_OF_EACH = [
    SessionStarted(
        session_id=SID,
        adapters=AdapterNames(vad=None, stt=None, llm="echo-llm", tts="tone-tts"),
        tools=["lookup"],
    ),
    SessionStateChanged(
        session_id=SID,
        previous=SessionState.SPEAKING,
        current=SessionState.INTERRUPTED,
        reason=TransitionReason.USER_BARGE_IN,
    ),
    UserSpeechStarted(session_id=SID, turn_id="t1"),
    UserSpeechEnded(session_id=SID, turn_id="t1", duration_ms=820.0, endpoint_ms=96.0),
    TranscriptPartial(session_id=SID, turn_id="t1", text="book a"),
    TranscriptFinal(session_id=SID, turn_id="t1", text="book a table", source="stt", stt_ms=210.0),
    AgentTurnStarted(session_id=SID, turn_id="t1", user_text="book a table"),
    AgentTurnFinished(
        session_id=SID,
        turn_id="t1",
        outcome=TurnOutcome.INTERRUPTED,
        spoken_text="Sure.",
        unspoken_text="For how many?",
        sentences_acked=1,
        sentences_total=2,
        timings=StageTimings(total_ms=640.0),
    ),
    ToolCallStarted(
        session_id=SID,
        turn_id="t1",
        call_id="c1",
        tool="lookup",
        arguments={"q": 1},
        requested_by="llm",
    ),
    ToolCallFinished(
        session_id=SID, turn_id="t1", call_id="c1", tool="lookup", result=[1, 2], duration_ms=3.0
    ),
    ToolCallFailed(
        session_id=SID,
        turn_id="t1",
        call_id="c2",
        tool="lookup",
        error_kind=ToolErrorKind.TIMEOUT,
        message="no result within 1.0s",
        duration_ms=1000.0,
    ),
    TtsStarted(session_id=SID, turn_id="t1", sentence_id=1, text="Sure.", synth_ms=40.0),
    TtsStopped(session_id=SID, turn_id="t1", sentence_id=1, reason="completed"),
    PlaybackAcked(session_id=SID, turn_id="t1", sentence_id=1, accepted=True),
    UserBargeIn(
        session_id=SID,
        turn_id="t1",
        interrupted_state=SessionState.SPEAKING,
        cancelled=["generation", "tts"],
    ),
    Error(session_id=SID, stage="tool", message="boom", recoverable=True),
]


def test_fixture_covers_every_event_type() -> None:
    union = typing.get_args(Event)[0]  # Annotated[Union[...], Field(...)]
    declared = {cls.model_fields["event_type"].default for cls in typing.get_args(union)}
    assert {e.event_type for e in ONE_OF_EACH} == declared


@pytest.mark.parametrize("event", ONE_OF_EACH, ids=lambda e: e.event_type)
def test_round_trips_through_json(event) -> None:
    restored = EventAdapter.validate_json(event.model_dump_json())
    assert type(restored) is type(event)
    assert restored == event


def test_base_fields_present() -> None:
    for event in ONE_OF_EACH:
        data = event.model_dump()
        assert {"session_id", "turn_id", "seq", "timestamp", "mono", "event_type"} <= data.keys()


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValidationError):
        EventAdapter.validate_python({"event_type": "MADE_UP", "session_id": SID})


def test_unexpected_payload_field_rejected() -> None:
    with pytest.raises(ValidationError):
        EventAdapter.validate_python(
            {"event_type": "USER_SPEECH_STARTED", "session_id": SID, "confidence": 0.97}
        )


def test_missing_session_id_rejected() -> None:
    with pytest.raises(ValidationError):
        EventAdapter.validate_python({"event_type": "USER_SPEECH_STARTED"})
