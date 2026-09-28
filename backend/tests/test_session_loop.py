"""The full session loop on deterministic adapters: input → STT → LLM → TTS → ACK → commit."""

from collections.abc import AsyncIterator

from app.adapters.base import TextDelta
from app.adapters.fake import SILENCE, SPEECH, frames
from app.agent.orchestrator import SessionConfig
from app.events import (
    AgentTurnFinished,
    AgentTurnStarted,
    Error,
    SessionStarted,
    TranscriptFinal,
    TurnOutcome,
)
from app.session.state import SessionState as S

STATE_AND_SESSION = {"SESSION_STARTED", "SESSION_STATE_CHANGED"}


async def speak(h, speech_frames: int = 5) -> None:
    for frame in frames(SPEECH, speech_frames) + frames(SILENCE, 3):
        await h.session.handle_audio(frame)


async def complete_turn(h, turn_index: int, sentences: int) -> str:
    turn_id = await h.until_sentence_sent(turn_index, sentences)
    for sentence_id in range(1, sentences + 1):
        await h.client.play(turn_id, sentence_id)
    await h.until(lambda: len(h.events(AgentTurnFinished)) > turn_index)
    await h.until_state(S.LISTENING)
    return turn_id


async def test_session_start_announces_adapters(make_session) -> None:
    h = await make_session()
    (started,) = h.events(SessionStarted)
    assert started.adapters.llm == "scripted-llm"
    assert started.adapters.stt == "scripted-stt"
    assert h.states() == [S.LISTENING]


async def test_text_turn(make_session) -> None:
    h = await make_session(
        [[TextDelta(text="Hello "), TextDelta(text="there. How can "), TextDelta(text="I help?")]],
        with_audio=False,
    )
    await h.session.handle_text("  hi  ")
    await complete_turn(h, 0, sentences=2)

    assert h.states() == [S.LISTENING, S.THINKING, S.SPEAKING, S.LISTENING]
    (finished,) = h.events(AgentTurnFinished)
    assert finished.outcome is TurnOutcome.COMPLETED
    assert finished.spoken_text == "Hello there. How can I help?"
    assert finished.timings.total_ms is not None
    assert finished.timings.stt_ms is None
    assert [(m.role, m.content) for m in h.session.conversation.messages] == [
        ("user", "hi"),
        ("assistant", "Hello there. How can I help?"),
    ]
    assert h.client.flushes == []


async def test_speech_turn(make_session) -> None:
    h = await make_session([[TextDelta(text="Sure.")]], stt_script=["book a table"])
    await speak(h, speech_frames=5)
    await complete_turn(h, 0, sentences=1)

    kinds = [e.event_type for e in h.all_events()]
    order = ["USER_SPEECH_STARTED", "USER_SPEECH_ENDED", "TRANSCRIPT_FINAL", "AGENT_TURN_STARTED"]
    assert [k for k in kinds if k in order] == order
    assert len(h.stt.calls[0]) == 8 * 1024  # 5 speech + 3 silence frames up to the endpoint

    (transcript,) = h.events(TranscriptFinal)
    assert transcript.source == "stt" and transcript.text == "book a table"
    timings = h.events(AgentTurnFinished)[0].timings
    assert timings.endpoint_ms == 96.0
    assert timings.stt_ms is not None and timings.total_ms is not None
    # Every event of the turn carries the same turn_id.
    turn_id = h.events(AgentTurnStarted)[0].turn_id
    turn_events = [e for e in h.all_events() if e.event_type not in STATE_AND_SESSION]
    assert {e.turn_id for e in turn_events} == {turn_id}


async def test_empty_transcript_starts_no_turn(make_session) -> None:
    h = await make_session(stt_script=["   "])
    await speak(h)
    await h.until(lambda: bool(h.events(TranscriptFinal)))
    await h.until(lambda: h.session._turn_task.done())
    assert h.events(AgentTurnStarted) == []
    assert h.states() == [S.LISTENING]


async def test_stt_failure_keeps_session_listening(make_session) -> None:
    h = await make_session(
        [[TextDelta(text="Got it.")]], stt_script=[OSError("decoder crashed"), "retry"]
    )
    await speak(h)
    await h.until(lambda: bool(h.events(Error)))
    (error,) = h.events(Error)
    assert error.stage == "stt" and error.recoverable
    assert h.session.state is S.LISTENING

    await speak(h)
    await complete_turn(h, 0, sentences=1)
    assert h.events(AgentTurnFinished)[0].spoken_text == "Got it."


async def test_llm_failure_mid_turn_recovers(make_session) -> None:
    h = await make_session(
        [
            [TextDelta(text="Starting. "), RuntimeError("model crashed")],
            [TextDelta(text="Back again.")],
        ]
    )
    await h.session.handle_text("go")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    await h.until_state(S.LISTENING)

    (error,) = h.events(Error)
    assert error.stage == "llm" and "model crashed" in error.message
    assert h.states()[-2:] == [S.ERROR, S.LISTENING]
    assert h.events(AgentTurnFinished)[0].outcome is TurnOutcome.FAILED

    await h.session.handle_text("again")
    await complete_turn(h, 1, sentences=1)
    assert h.events(AgentTurnFinished)[1].spoken_text == "Back again."


class BrokenTTS:
    name = "broken-tts"
    sample_rate = 16_000

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        raise OSError("voice model missing")
        yield b""  # pragma: no cover - makes this an async generator


async def test_tts_failure_recovers(make_session) -> None:
    h = await make_session([[TextDelta(text="Hello.")]], tts=BrokenTTS())
    await h.session.handle_text("go")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    await h.until_state(S.LISTENING)
    (error,) = h.events(Error)
    assert error.stage == "tts" and "voice model missing" in error.message


async def test_unconfirmed_playback_does_not_hang(make_session) -> None:
    h = await make_session(
        [[TextDelta(text="Hi.")]],
        config=SessionConfig(playback_grace_s=0.05),
        with_audio=False,
    )
    await h.session.handle_text("go")  # client never ACKs: disconnected UI, broken audio
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    await h.until_state(S.LISTENING)
    assert h.events(Error)[0].stage == "transport"
    finished = h.events(AgentTurnFinished)[0]
    assert finished.spoken_text == "" and finished.unspoken_text == "Hi."
    assert len(h.client.flushes) == 1


async def test_audio_without_vad_stt_is_rejected_once(make_session) -> None:
    h = await make_session(with_audio=False)
    await speak(h)
    (error,) = h.events(Error)
    assert error.stage == "audio"
    assert h.states() == [S.LISTENING]


async def test_close_finalizes_active_turn(make_session) -> None:
    h = await make_session([[TextDelta(text="One. Two.")]])
    await h.session.handle_text("go")
    turn_id = await h.until_sentence_sent(0, 1)
    await h.client.play(turn_id, 1)
    await h.session.close()
    (finished,) = h.events(AgentTurnFinished)
    assert finished.outcome is TurnOutcome.INTERRUPTED
    assert finished.spoken_text == "One."
    assert h.session.state is S.IDLE
