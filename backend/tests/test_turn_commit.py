import pytest

from app.agent.turn import AssistantTurn, SentenceSplitter, SentenceStatus
from app.events import TurnOutcome


def turn_with_sent(*texts: str) -> AssistantTurn:
    turn = AssistantTurn("t")
    for text in texts:
        sentence = turn.add(text)
        turn.mark_sending(sentence.id)
        turn.mark_sent(sentence.id, audio_seconds=1.0)
    return turn


def test_all_acked_commits_everything() -> None:
    turn = turn_with_sent("One.", "Two.")
    assert turn.ack(1) and turn.ack(2)
    commit = turn.finalize(TurnOutcome.COMPLETED)
    assert commit.spoken_text == "One. Two."
    assert commit.unspoken_text == ""
    assert (commit.sentences_acked, commit.sentences_total) == (2, 2)


def test_partial_ack_commits_only_acked_prefix() -> None:
    turn = turn_with_sent("One.", "Two.", "Three.")
    turn.ack(1)
    commit = turn.finalize(TurnOutcome.INTERRUPTED)
    assert commit.spoken_text == "One."
    assert commit.unspoken_text == "Two. Three."


def test_zero_acks_commits_nothing() -> None:
    turn = turn_with_sent("One.")
    commit = turn.finalize(TurnOutcome.INTERRUPTED)
    assert commit.spoken_text == ""
    assert commit.sentences_acked == 0


def test_sentence_still_sending_cannot_be_acked() -> None:
    turn = AssistantTurn("t")
    turn.mark_sending(turn.add("Half sent.").id)
    assert not turn.ack(1)


def test_duplicate_and_unknown_acks_rejected() -> None:
    turn = turn_with_sent("One.")
    assert turn.ack(1)
    assert not turn.ack(1)
    assert not turn.ack(99)


def test_ack_after_finalize_is_ignored() -> None:
    turn = turn_with_sent("One.", "Two.")
    turn.finalize(TurnOutcome.INTERRUPTED)
    assert not turn.ack(1)
    assert turn.sentences[0].status is SentenceStatus.SENT


def test_finalize_twice_is_a_bug() -> None:
    turn = turn_with_sent("One.")
    turn.finalize(TurnOutcome.COMPLETED)
    with pytest.raises(RuntimeError):
        turn.finalize(TurnOutcome.COMPLETED)


def test_flushed_reply_acks_up_to_last_played() -> None:
    turn = turn_with_sent("One.", "Two.", "Three.")
    turn.ack(1)
    turn.apply_flushed(2)  # ACK for 2 was still in flight when the flush happened
    commit = turn.finalize(TurnOutcome.INTERRUPTED)
    assert commit.spoken_text == "One. Two."


def test_flushed_reply_never_acks_unsent_audio() -> None:
    turn = turn_with_sent("One.")
    turn.mark_sending(turn.add("Two.").id)
    turn.apply_flushed(5)
    assert [s.status for s in turn.sentences] == [SentenceStatus.ACKED, SentenceStatus.SENDING]


def test_playback_done_requires_generation_done_and_all_acked() -> None:
    turn = turn_with_sent("One.")
    turn.ack(1)
    assert not turn.playback_done.is_set()
    turn.finish_generation()
    assert turn.playback_done.is_set()


def test_has_unacked_audio() -> None:
    turn = turn_with_sent("One.")
    assert turn.has_unacked_audio()
    turn.ack(1)
    assert not turn.has_unacked_audio()


@pytest.mark.parametrize(
    ("deltas", "expected"),
    [
        (["Hello there. How ", "are you?"], ["Hello there.", "How are you?"]),
        (["It costs 3.5 dollars. Ok"], ["It costs 3.5 dollars.", "Ok"]),
        (["Line one\nLine two"], ["Line one", "Line two"]),
        (["Wait! Really? Yes."], ["Wait!", "Really?", "Yes."]),
    ],
)
def test_sentence_splitter(deltas: list[str], expected: list[str]) -> None:
    splitter = SentenceSplitter()
    out: list[str] = []
    for delta in deltas:
        out += splitter.feed(delta)
    out += splitter.flush()
    assert out == expected
