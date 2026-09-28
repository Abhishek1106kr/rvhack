"""Session traces: an in-memory event store plus per-turn stage timings."""

from collections import defaultdict
from collections.abc import Iterable

from app.events import (
    AgentTurnStarted,
    Event,
    StageTimings,
    ToolCallFailed,
    ToolCallFinished,
    TranscriptFinal,
    TtsStarted,
    UserSpeechEnded,
)


class TraceStore:
    """EventBus subscriber that keeps every event, in order, per session."""

    def __init__(self) -> None:
        self._events: defaultdict[str, list[Event]] = defaultdict(list)

    def __call__(self, event: Event) -> None:
        self._events[event.session_id].append(event)

    def sessions(self) -> list[str]:
        return list(self._events)

    def events(self, session_id: str) -> list[Event]:
        return list(self._events.get(session_id, []))

    def to_jsonl(self, session_id: str) -> str:
        return "\n".join(event.model_dump_json() for event in self.events(session_id))


def _first[T](events: list[Event], kind: type[T]) -> T | None:
    return next((e for e in events if isinstance(e, kind)), None)


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def stage_timings(turn_events: Iterable[Event]) -> StageTimings:
    """Latency for one turn, computed from that turn's events.

    TOTAL is server-side: end of user speech (or typed input) to the first audio
    chunk leaving the server. Network and browser buffering are not included.
    """
    events = list(turn_events)
    speech_ended = _first(events, UserSpeechEnded)
    transcript = _first(events, TranscriptFinal)
    turn_started = _first(events, AgentTurnStarted)
    first_tts = _first(events, TtsStarted)
    tool_events = [e for e in events if isinstance(e, ToolCallFinished | ToolCallFailed)]

    tool_ms = sum(e.duration_ms or 0.0 for e in tool_events) if tool_events else None

    stt_ms = None
    if speech_ended and transcript:
        stt_ms = _ms(transcript.mono - speech_ended.mono)

    llm_ms = tts_ms = total_ms = None
    if first_tts:
        tts_ms = round(first_tts.synth_ms, 1)
        first_sentence_ready = first_tts.mono - first_tts.synth_ms / 1000
        if turn_started:
            tools_before = sum(
                e.duration_ms or 0.0 for e in tool_events if e.mono <= first_tts.mono
            )
            llm_ms = round(_ms(first_sentence_ready - turn_started.mono) - tools_before, 1)
        anchor = speech_ended or transcript
        if anchor:
            total_ms = _ms(first_tts.mono - anchor.mono)

    return StageTimings(
        endpoint_ms=speech_ended.endpoint_ms if speech_ended else None,
        stt_ms=stt_ms,
        llm_ms=llm_ms,
        tool_ms=round(tool_ms, 1) if tool_ms is not None else None,
        tts_ms=tts_ms,
        total_ms=total_ms,
    )
