"""Session state machine. The only place session state lives."""

from collections.abc import Callable
from enum import StrEnum


class SessionState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    TOOL_EXECUTION = "TOOL_EXECUTION"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    ERROR = "ERROR"


class TransitionReason(StrEnum):
    SESSION_STARTED = "session_started"
    TRANSCRIPT_FINAL = "transcript_final"
    AUDIO_STARTED = "audio_started"
    AWAITING_PLAYBACK = "awaiting_playback"
    TOOL_REQUESTED = "tool_requested"
    TOOL_RESULT = "tool_result"
    EMPTY_RESPONSE = "empty_response"
    PLAYBACK_COMPLETE = "playback_complete"
    USER_BARGE_IN = "user_barge_in"
    INTERRUPTION_HANDLED = "interruption_handled"
    TURN_FAILED = "turn_failed"
    RECOVERED = "recovered"
    SESSION_ENDED = "session_ended"


S = SessionState

# ERROR and IDLE are reachable from every other state; see is_allowed().
_ALLOWED: dict[SessionState, frozenset[SessionState]] = {
    S.IDLE: frozenset({S.LISTENING}),
    S.LISTENING: frozenset({S.THINKING}),
    S.THINKING: frozenset({S.SPEAKING, S.TOOL_EXECUTION, S.INTERRUPTED, S.LISTENING}),
    S.TOOL_EXECUTION: frozenset({S.THINKING, S.INTERRUPTED}),
    S.SPEAKING: frozenset({S.TOOL_EXECUTION, S.LISTENING, S.INTERRUPTED}),
    S.INTERRUPTED: frozenset({S.LISTENING}),
    S.ERROR: frozenset({S.LISTENING}),
}


def is_allowed(current: SessionState, target: SessionState) -> bool:
    if current is target:
        return False
    if target in (S.ERROR, S.IDLE):
        return True
    return target in _ALLOWED[current]


class InvalidTransition(RuntimeError):
    """A runtime bug: code asked for a transition the table does not allow."""


OnTransition = Callable[[SessionState, SessionState, TransitionReason], None]


class SessionStateMachine:
    def __init__(self, on_transition: OnTransition) -> None:
        self._state = SessionState.IDLE
        self._on_transition = on_transition

    @property
    def state(self) -> SessionState:
        return self._state

    def transition(self, target: SessionState, reason: TransitionReason) -> None:
        previous = self._state
        if not is_allowed(previous, target):
            raise InvalidTransition(f"{previous} -> {target} ({reason}) is not allowed")
        self._state = target
        self._on_transition(previous, target, reason)
