"""Typed runtime events. The only definition of event shapes in ARC.

Every event carries session_id, turn_id, seq (assigned by EventBus), a wall-clock
timestamp for humans, and a monotonic clock reading used for all latency math.
"""

import time
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.session.state import SessionState, TransitionReason


class ToolErrorKind(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    TIMEOUT = "timeout"
    # A side-effecting tool timed out: it may or may not have acted.
    TIMEOUT_OUTCOME_UNKNOWN = "timeout_outcome_unknown"
    EXCEPTION = "exception"
    INVALID_OUTPUT = "invalid_output"
    CANCELLED = "cancelled"


class TurnOutcome(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class StageTimings(BaseModel):
    """Per-turn latency, derived from the turn's events (see app.trace.stage_timings)."""

    model_config = ConfigDict(frozen=True)

    endpoint_ms: float | None = None
    stt_ms: float | None = None
    llm_ms: float | None = None
    tool_ms: float | None = None
    tts_ms: float | None = None
    # End of user input → first audio of the actual answer (fillers excluded).
    total_ms: float | None = None
    # End of user input → first audio of any kind, including a filler acknowledgement.
    first_audio_ms: float | None = None


class AdapterNames(BaseModel):
    model_config = ConfigDict(frozen=True)

    vad: str | None
    stt: str | None
    llm: str
    tts: str


class _EventBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    turn_id: str | None = None
    seq: int = 0
    timestamp: float = Field(default_factory=time.time)
    mono: float = Field(default_factory=time.monotonic)


class SessionStarted(_EventBase):
    event_type: Literal["SESSION_STARTED"] = "SESSION_STARTED"
    adapters: AdapterNames
    tools: list[str]


class SessionStateChanged(_EventBase):
    event_type: Literal["SESSION_STATE_CHANGED"] = "SESSION_STATE_CHANGED"
    previous: SessionState
    current: SessionState
    reason: TransitionReason


class UserSpeechStarted(_EventBase):
    event_type: Literal["USER_SPEECH_STARTED"] = "USER_SPEECH_STARTED"


class UserSpeechEnded(_EventBase):
    event_type: Literal["USER_SPEECH_ENDED"] = "USER_SPEECH_ENDED"
    duration_ms: float
    endpoint_ms: float


class TranscriptPartial(_EventBase):
    event_type: Literal["TRANSCRIPT_PARTIAL"] = "TRANSCRIPT_PARTIAL"
    text: str


class TranscriptFinal(_EventBase):
    event_type: Literal["TRANSCRIPT_FINAL"] = "TRANSCRIPT_FINAL"
    text: str
    source: Literal["stt", "text"]
    stt_ms: float | None


class AgentTurnStarted(_EventBase):
    event_type: Literal["AGENT_TURN_STARTED"] = "AGENT_TURN_STARTED"
    user_text: str


class AgentTurnFinished(_EventBase):
    event_type: Literal["AGENT_TURN_FINISHED"] = "AGENT_TURN_FINISHED"
    outcome: TurnOutcome
    spoken_text: str
    unspoken_text: str
    sentences_acked: int
    sentences_total: int
    timings: StageTimings


class ToolCallStarted(_EventBase):
    event_type: Literal["TOOL_CALL_STARTED"] = "TOOL_CALL_STARTED"
    call_id: str
    tool: str
    # Tool schemas are defined per tool, so validated arguments are carried as JSON.
    arguments: dict[str, Any]
    # Who decided to call it: the model, or the problem layer's deterministic planner.
    requested_by: Literal["llm", "planner"]


class ToolCallFinished(_EventBase):
    event_type: Literal["TOOL_CALL_FINISHED"] = "TOOL_CALL_FINISHED"
    call_id: str
    tool: str
    result: Any
    duration_ms: float


class ToolCallFailed(_EventBase):
    event_type: Literal["TOOL_CALL_FAILED"] = "TOOL_CALL_FAILED"
    call_id: str
    tool: str
    error_kind: ToolErrorKind
    message: str
    # None when the call failed before execution (unknown tool, invalid arguments).
    duration_ms: float | None


class TtsStarted(_EventBase):
    event_type: Literal["TTS_STARTED"] = "TTS_STARTED"
    sentence_id: int
    text: str
    synth_ms: float
    filler: bool = False


class TtsStopped(_EventBase):
    event_type: Literal["TTS_STOPPED"] = "TTS_STOPPED"
    sentence_id: int
    reason: Literal["completed", "cancelled"]


class PlaybackAcked(_EventBase):
    event_type: Literal["PLAYBACK_ACKED"] = "PLAYBACK_ACKED"
    sentence_id: int
    # False when the ACK arrived for a finalized turn or an unknown sentence.
    accepted: bool


class UserBargeIn(_EventBase):
    event_type: Literal["USER_BARGE_IN"] = "USER_BARGE_IN"
    interrupted_state: SessionState
    # generation: LLM stream · tts: synthesis · tool: a read-only tool call
    # playback: audio already sent was stopped and dropped on the client
    cancelled: list[Literal["generation", "tts", "tool", "playback"]]


ErrorStage = Literal[
    "audio", "stt", "llm", "tool", "tts", "transport", "cancellation", "bus", "planner", "runtime"
]


class Error(_EventBase):
    event_type: Literal["ERROR"] = "ERROR"
    stage: ErrorStage
    message: str
    recoverable: bool


Event = Annotated[
    SessionStarted
    | SessionStateChanged
    | UserSpeechStarted
    | UserSpeechEnded
    | TranscriptPartial
    | TranscriptFinal
    | AgentTurnStarted
    | AgentTurnFinished
    | ToolCallStarted
    | ToolCallFinished
    | ToolCallFailed
    | TtsStarted
    | TtsStopped
    | PlaybackAcked
    | UserBargeIn
    | Error,
    Field(discriminator="event_type"),
]

EventAdapter: TypeAdapter[Event] = TypeAdapter(Event)
