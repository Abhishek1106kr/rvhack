"""Assistant turn: sentences, playback ACKs, and the commit rule.

"Spoken" means the client ACKed that the sentence's last audio chunk finished playing.
The granularity is the sentence: a sentence cut off mid-playback counts as NOT spoken,
even if the user heard its first words. ARC never claims word-level certainty.
"""

import asyncio
import re
from dataclasses import dataclass
from enum import StrEnum

from app.events import TurnOutcome

# Split after . ! ? followed by whitespace, or on newlines. "Dr. Smith" will split;
# acceptable for TTS pacing, and never affects what is committed.
_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")
# Markdown the LLM may emit despite instructions; a TTS voice would read it out.
_EMPHASIS = re.compile(r"[*#`~]+")
_LIST_MARKER = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:])")


def speakable(text: str) -> str:
    """Text as it should be voiced (and committed): no markdown, single-spaced."""
    text = _LIST_MARKER.sub("", _EMPHASIS.sub("", text)).replace("_", " ")
    return _SPACE_BEFORE_PUNCT.sub(r"\1", " ".join(text.split()))


class SentenceSplitter:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        parts = _BOUNDARY.split(self._buffer)
        self._buffer = parts.pop()
        return [s for p in parts if (s := speakable(p))]

    def flush(self) -> list[str]:
        rest, self._buffer = speakable(self._buffer), ""
        return [rest] if rest else []


class SentenceStatus(StrEnum):
    PENDING = "pending"  # generated, waiting for TTS
    SENDING = "sending"  # TTS audio is being sent
    SENT = "sent"  # last chunk sent, not yet confirmed played
    ACKED = "acked"  # client confirmed it finished playing


@dataclass
class Sentence:
    id: int
    text: str
    status: SentenceStatus = SentenceStatus.PENDING
    # A runtime acknowledgement ("Let me check that.") spoken while the answer is not ready.
    filler: bool = False


@dataclass(frozen=True)
class TurnCommit:
    outcome: TurnOutcome
    spoken_text: str
    unspoken_text: str
    sentences_acked: int
    sentences_total: int


class AssistantTurn:
    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self.sentences: list[Sentence] = []
        self.audio_seconds_sent = 0.0
        self.playback_done = asyncio.Event()
        self._generation_done = False
        self._commit: TurnCommit | None = None

    @property
    def generation_done(self) -> bool:
        return self._generation_done

    @property
    def finalized(self) -> bool:
        return self._commit is not None

    def add(self, text: str, filler: bool = False) -> Sentence:
        sentence = Sentence(id=len(self.sentences) + 1, text=text, filler=filler)
        self.sentences.append(sentence)
        return sentence

    def mark_sending(self, sentence_id: int) -> None:
        self._get(sentence_id).status = SentenceStatus.SENDING

    def mark_sent(self, sentence_id: int, audio_seconds: float) -> None:
        self._get(sentence_id).status = SentenceStatus.SENT
        self.audio_seconds_sent += audio_seconds

    def ack(self, sentence_id: int) -> bool:
        """Record a playback ACK. False if the turn is finalized or the ACK is not applicable."""
        if self.finalized:
            return False
        sentence = self._find(sentence_id)
        if sentence is None or sentence.status is not SentenceStatus.SENT:
            return False
        sentence.status = SentenceStatus.ACKED
        self._update_playback_done()
        return True

    def apply_flushed(self, last_acked_sentence_id: int | None) -> None:
        """The client's flush reply: every sent sentence up to this id finished playing.

        Closes the race where an ACK is in flight when barge-in is detected.
        """
        if last_acked_sentence_id is None:
            return
        for sentence in self.sentences:
            if sentence.id <= last_acked_sentence_id and sentence.status is SentenceStatus.SENT:
                sentence.status = SentenceStatus.ACKED

    def finish_generation(self) -> None:
        self._generation_done = True
        self._update_playback_done()

    def has_unacked_audio(self) -> bool:
        return any(
            s.status in (SentenceStatus.SENDING, SentenceStatus.SENT) for s in self.sentences
        )

    def finalize(self, outcome: TurnOutcome) -> TurnCommit:
        if self._commit is not None:
            raise RuntimeError(f"turn {self.turn_id} already finalized")
        acked = [s for s in self.sentences if s.status is SentenceStatus.ACKED]
        rest = [s for s in self.sentences if s.status is not SentenceStatus.ACKED]
        self._commit = TurnCommit(
            outcome=outcome,
            spoken_text=" ".join(s.text for s in acked),
            unspoken_text=" ".join(s.text for s in rest),
            sentences_acked=len(acked),
            sentences_total=len(self.sentences),
        )
        return self._commit

    def _update_playback_done(self) -> None:
        if self._generation_done and all(s.status is SentenceStatus.ACKED for s in self.sentences):
            self.playback_done.set()

    def _find(self, sentence_id: int) -> Sentence | None:
        return next((s for s in self.sentences if s.id == sentence_id), None)

    def _get(self, sentence_id: int) -> Sentence:
        sentence = self._find(sentence_id)
        if sentence is None:
            raise KeyError(f"turn {self.turn_id} has no sentence {sentence_id}")
        return sentence
