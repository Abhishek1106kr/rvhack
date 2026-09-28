import pytest

from app.session.state import (
    InvalidTransition,
    SessionState,
    SessionStateMachine,
    TransitionReason,
    is_allowed,
)

S = SessionState
R = TransitionReason

# The table from docs/phase1-runtime-core.md §4, excluding the any→ERROR / any→IDLE rules.
EXPECTED = {
    (S.IDLE, S.LISTENING),
    (S.LISTENING, S.THINKING),
    (S.THINKING, S.SPEAKING),
    (S.THINKING, S.TOOL_EXECUTION),
    (S.THINKING, S.INTERRUPTED),
    (S.THINKING, S.LISTENING),
    (S.TOOL_EXECUTION, S.THINKING),
    (S.TOOL_EXECUTION, S.INTERRUPTED),
    (S.SPEAKING, S.TOOL_EXECUTION),
    (S.SPEAKING, S.LISTENING),
    (S.SPEAKING, S.INTERRUPTED),
    (S.INTERRUPTED, S.LISTENING),
    (S.ERROR, S.LISTENING),
}


def machine_in(state: SessionState) -> tuple[SessionStateMachine, list]:
    record: list = []
    machine = SessionStateMachine(lambda *args: record.append(args))
    machine._state = state  # test setup only
    return machine, record


@pytest.mark.parametrize("source", list(S))
@pytest.mark.parametrize("target", list(S))
def test_transition_table(source: S, target: S) -> None:
    universal = target in (S.ERROR, S.IDLE) and source is not target
    assert is_allowed(source, target) == ((source, target) in EXPECTED or universal)


@pytest.mark.parametrize(("source", "target"), sorted(EXPECTED))
def test_allowed_transition_reports_previous_current_reason(source: S, target: S) -> None:
    machine, record = machine_in(source)
    machine.transition(target, R.PLAYBACK_COMPLETE)
    assert machine.state is target
    assert record == [(source, target, R.PLAYBACK_COMPLETE)]


def test_disallowed_transition_raises_and_keeps_state() -> None:
    machine, record = machine_in(S.LISTENING)
    with pytest.raises(InvalidTransition):
        machine.transition(S.SPEAKING, R.AUDIO_STARTED)
    assert machine.state is S.LISTENING
    assert record == []


def test_self_transition_is_rejected() -> None:
    machine, _ = machine_in(S.SPEAKING)
    with pytest.raises(InvalidTransition):
        machine.transition(S.SPEAKING, R.AUDIO_STARTED)


@pytest.mark.parametrize("source", [s for s in S if s is not S.ERROR])
def test_error_then_recovery_from_any_state(source: S) -> None:
    machine, record = machine_in(source)
    machine.transition(S.ERROR, R.TURN_FAILED)
    machine.transition(S.LISTENING, R.RECOVERED)
    assert [r[1] for r in record] == [S.ERROR, S.LISTENING]
