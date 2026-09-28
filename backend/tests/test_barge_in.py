"""Barge-in against the sentence-level playback ACK model.

    generated audio → client playback → sentence ACK → committed as spoken

Content without an ACK (or a flush reply covering it) is never committed as spoken.
A sentence cut off mid-playback counts as unspoken: granularity is the sentence.
"""

import asyncio

from app.adapters.base import TextDelta
from app.adapters.fake import SILENCE, SPEECH, Gate, ToneTTS, frames
from app.events import AgentTurnFinished, TtsStopped, TurnOutcome, UserBargeIn
from app.session.state import SessionState as S

OK = [TextDelta(text="Ok.")]


async def feed(h, *batches: list[bytes]) -> None:
    for batch in batches:
        for frame in batch:
            await h.session.handle_audio(frame)


def roles_and_content(h) -> list[tuple[str, str]]:
    return [(m.role, m.content) for m in h.session.conversation.messages]


async def test_barge_in_mid_response_commits_only_acked_sentences(make_session) -> None:
    hold = Gate()
    h = await make_session(
        [
            [TextDelta(text="One. Two. "), hold, TextDelta(text="Three.")],
            [TextDelta(text="Okay, stopping.")],
        ],
        stt_script=["wait, stop"],
    )
    await h.session.handle_text("tell me three things")
    turn_id = await h.until_sentence_sent(0, 2)
    await h.client.play(turn_id, 1)
    assert h.session.state is S.SPEAKING

    await feed(h, frames(SPEECH, 4))  # user starts talking over sentence 2
    await h.until(lambda: bool(h.events(AgentTurnFinished)))

    (barge_in,) = h.events(UserBargeIn)
    assert barge_in.turn_id == turn_id
    assert barge_in.interrupted_state is S.SPEAKING
    assert set(barge_in.cancelled) == {"generation", "playback"}
    assert h.llm.cancelled == 1

    (finished,) = h.events(AgentTurnFinished)
    assert finished.outcome is TurnOutcome.INTERRUPTED
    assert finished.spoken_text == "One."
    assert finished.unspoken_text == "Two."
    assert (finished.sentences_acked, finished.sentences_total) == (1, 2)

    # Stale audio: the client was told to flush, and nothing for the turn followed.
    assert h.client.flushes == [turn_id]
    hold.open()
    await asyncio.sleep(0.01)
    assert h.client.audio_after_flush(turn_id) == []
    assert not any(c.turn_id == turn_id and c.sentence_id == 3 for c in h.client.audio)

    assert h.states()[-3:] == [S.SPEAKING, S.INTERRUPTED, S.LISTENING]

    # The interrupting utterance becomes the next turn; history holds only what was heard.
    await feed(h, frames(SPEECH, 2), frames(SILENCE, 3))
    next_turn = await h.until_sentence_sent(1, 1)
    await h.client.play(next_turn, 1)
    await h.until(lambda: len(h.events(AgentTurnFinished)) == 2)
    assert roles_and_content(h) == [
        ("user", "tell me three things"),
        ("assistant", "One."),
        ("user", "wait, stop"),
        ("assistant", "Okay, stopping."),
    ]
    sent_to_llm = [m.content for m in h.llm.calls[1]]
    assert sent_to_llm == ["tell me three things", "One.", "wait, stop"]


async def test_ack_in_flight_is_recovered_from_flush_reply(make_session) -> None:
    h = await make_session([[TextDelta(text="One. Two. "), Gate(), TextDelta(text="Three.")], OK])
    await h.session.handle_text("go")
    turn_id = await h.until_sentence_sent(0, 2)
    await h.client.play(turn_id, 1)
    h.client.play_unreported(turn_id, 2)  # finished playing; ACK not yet delivered

    await h.session.handle_text("stop")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    (finished,) = h.events(AgentTurnFinished)
    assert finished.spoken_text == "One. Two."


async def test_interrupt_before_any_ack_commits_nothing(make_session) -> None:
    h = await make_session([[TextDelta(text="One. Two. "), Gate()], OK])
    await h.session.handle_text("go")
    await h.until_sentence_sent(0, 2)

    await h.session.handle_text("stop")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    (finished,) = h.events(AgentTurnFinished)
    assert finished.spoken_text == ""
    assert finished.unspoken_text == "One. Two."
    assert [m.role for m in h.session.conversation.messages] == ["user", "user"]


async def test_barge_in_during_tts_cancels_synthesis(make_session) -> None:
    tts_gate = asyncio.Event()
    h = await make_session(
        [[TextDelta(text="A long sentence that keeps going and going.")], OK],
        tts=ToneTTS(ms_per_word=100, gate=tts_gate),
    )
    await h.session.handle_text("go")
    await h.until(lambda: len(h.client.audio) == 1)  # first chunk sent, rest held

    await h.session.handle_text("stop")
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    assert h.tts.cancelled == 1
    (stopped,) = h.events(TtsStopped)
    assert stopped.reason == "cancelled"
    (barge_in,) = h.events(UserBargeIn)
    assert "tts" in barge_in.cancelled
    assert h.events(AgentTurnFinished)[0].spoken_text == ""


async def test_repeated_interruption(make_session) -> None:
    h = await make_session(
        [
            [TextDelta(text="A one. A two. "), Gate()],
            [TextDelta(text="B one. B two. "), Gate()],
            [TextDelta(text="C done.")],
        ]
    )
    await h.session.handle_text("first")
    turn_a = await h.until_sentence_sent(0, 2)
    await h.client.play(turn_a, 1)

    await h.session.handle_text("second")
    turn_b = await h.until_sentence_sent(1, 2)
    await h.client.play(turn_b, 1)
    await h.client.play(turn_b, 2)

    await h.session.handle_text("third")
    turn_c = await h.until_sentence_sent(2, 1)
    await h.client.play(turn_c, 1)
    await h.until(lambda: len(h.events(AgentTurnFinished)) == 3)
    await h.until_state(S.LISTENING)

    outcomes = [(e.outcome, e.spoken_text) for e in h.events(AgentTurnFinished)]
    assert outcomes == [
        (TurnOutcome.INTERRUPTED, "A one."),
        (TurnOutcome.INTERRUPTED, "B one. B two."),
        (TurnOutcome.COMPLETED, "C done."),
    ]
    assert roles_and_content(h) == [
        ("user", "first"),
        ("assistant", "A one."),
        ("user", "second"),
        ("assistant", "B one. B two."),
        ("user", "third"),
        ("assistant", "C done."),
    ]
    assert h.states().count(S.INTERRUPTED) == 2
    assert h.client.audio_after_flush(turn_a) == []
    assert h.client.audio_after_flush(turn_b) == []
