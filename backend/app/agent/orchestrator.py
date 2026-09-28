"""VoiceSession: the realtime loop for one connected client.

Owns session state, the turn lifecycle, cancellation, barge-in, and commit semantics.
Adapters produce data; this class decides what happens.

Concurrency model
-----------------
- One task per user turn (`_turn_task`): STT → LLM rounds (+ tools) → TTS → wait for playback.
  Inside it, generation and speech run as two sibling tasks joined by a sentence queue.
- Barge-in runs as its own task (`_interrupt_task`) so the caller feeding client messages is
  never blocked; the client's flush reply arrives through that same caller.
- A new turn waits for any in-progress interruption before it starts.
"""

import asyncio
import functools
import logging
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.adapters.base import (
    SAMPLE_RATE,
    Adapters,
    AudioOut,
    Message,
    TextDelta,
    ToolCallRequest,
    Transport,
    VADSignal,
)
from app.agent.conversation import Conversation
from app.agent.turn import AssistantTurn, Sentence, SentenceSplitter, SentenceStatus
from app.bus import EventBus
from app.events import (
    AdapterNames,
    AgentTurnFinished,
    AgentTurnStarted,
    Error,
    ErrorStage,
    Event,
    PlaybackAcked,
    SessionStarted,
    SessionStateChanged,
    TranscriptFinal,
    TtsStarted,
    TtsStopped,
    TurnOutcome,
    UserBargeIn,
    UserSpeechEnded,
    UserSpeechStarted,
)
from app.session.state import SessionState, SessionStateMachine, TransitionReason
from app.tools.executor import ToolExecutor, ToolResult
from app.tools.registry import ToolRegistry, ToolRisk
from app.trace import stage_timings

log = logging.getLogger(__name__)

S = SessionState
R = TransitionReason

_INTERRUPTIBLE = frozenset({S.THINKING, S.TOOL_EXECUTION, S.SPEAKING})

# Deterministic tool routing supplied by the problem layer. Given the conversation (last
# message = the user's current utterance), returns tool calls the runtime runs *before* the
# LLM, so answers are grounded even when a small model would skip or botch the tool call.
Planner = Callable[[Sequence[Message]], list[ToolCallRequest]]


@dataclass(frozen=True)
class SessionConfig:
    system_prompt: str | None = None
    # How long to wait for the client to confirm a flush before committing ACKs received so far.
    flush_timeout_s: float = 0.5
    # Added to the audio duration sent when waiting for playback ACKs.
    playback_grace_s: float = 2.0
    # How long a cancelled task may take to stop before it is reported.
    cancel_timeout_s: float = 1.0
    # LLM output is untrusted: bound how many tool rounds one turn may take.
    max_tool_rounds: int = 4
    # Audio kept from before the VAD fires, so the first syllable reaches STT (10 × 32 ms).
    preroll_frames: int = 10
    planner: Planner | None = None
    # Spoken if no answer sentence is ready this long after the turn starts. Real speech:
    # it is ACKed and committed like any sentence, and flagged in TTS_STARTED/timings.
    filler_phrases: tuple[str, ...] = ()
    filler_after_s: float = 1.0


class _StageFailure(Exception):
    def __init__(self, stage: ErrorStage, cause: Exception) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.stage = stage


def _new_turn_id() -> str:
    return uuid.uuid4().hex[:8]


class VoiceSession:
    def __init__(
        self,
        *,
        session_id: str,
        bus: EventBus,
        adapters: Adapters,
        transport: Transport,
        tools: ToolRegistry,
        config: SessionConfig | None = None,
    ) -> None:
        self.session_id = session_id
        self.config = config or SessionConfig()
        self.conversation = Conversation(self.config.system_prompt)
        self._bus = bus
        self._adapters = adapters
        self._transport = transport
        self._tools = tools
        self._machine = SessionStateMachine(self._on_transition)
        self._executor = ToolExecutor(tools, self._emit)

        self._turn: AssistantTurn | None = None  # assistant turn not yet finalized
        self._turn_task: asyncio.Task[None] | None = None
        self._turn_task_id: str | None = None
        self._interrupt_task: asyncio.Task[None] | None = None
        self._flushed: dict[str, asyncio.Future[int | None]] = {}
        self._turn_events: dict[str, list[Event]] = {}
        self._background_tools: set[asyncio.Task[ToolResult]] = set()
        self._active_tool: str | None = None
        self._fillers_spoken = 0

        # Inbound speech
        self._in_speech = False
        self._utterance = bytearray()
        self._speech_turn_id: str | None = None
        # (turn_id, audio) of a turn still in STT; merged if the user resumes speaking.
        self._transcribing: tuple[str, bytes] | None = None
        self._audio_rejected = False
        self._preroll: deque[bytes] = deque(maxlen=self.config.preroll_frames)

    # ── public API ────────────────────────────────────────────────────────────────────────

    @property
    def state(self) -> SessionState:
        return self._machine.state

    async def start(self) -> None:
        self._emit(
            SessionStarted(
                session_id=self.session_id,
                adapters=AdapterNames(
                    vad=self._adapters.vad.name if self._adapters.vad else None,
                    stt=self._adapters.stt.name if self._adapters.stt else None,
                    llm=self._adapters.llm.name,
                    tts=self._adapters.tts.name,
                ),
                tools=self._tools.names(),
            )
        )
        self._machine.transition(S.LISTENING, R.SESSION_STARTED)

    async def close(self) -> None:
        tasks = [t for t in (self._interrupt_task, self._turn_task) if t and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._turn is not None and not self._turn.finalized:
            # Client is gone: no flush possible; commit what was confirmed played.
            self._finish_turn(self._turn, TurnOutcome.INTERRUPTED)
        if self.state is not S.IDLE:
            self._machine.transition(S.IDLE, R.SESSION_ENDED)

    async def handle_audio(self, frame: bytes) -> None:
        vad, stt = self._adapters.vad, self._adapters.stt
        if vad is None or stt is None:
            if not self._audio_rejected:
                self._audio_rejected = True
                self._error("audio", "audio received but no VAD/STT adapter is configured", True)
            return

        signal = vad.process(frame)
        if signal is VADSignal.SPEECH_START:
            self._on_speech_start()
        if self._in_speech:
            self._utterance += frame
        else:
            self._preroll.append(frame)
        if signal is VADSignal.SPEECH_END and self._in_speech:
            self._on_speech_end(vad.silence_ms)

    async def handle_text(self, text: str) -> None:
        """Typed input: a user utterance that skips VAD/STT. Interrupts like speech does."""
        text = text.strip()
        if not text:
            return
        turn_id = self._open_turn_id()
        # Typed input replaces any turn that has not started responding (speech included).
        if (carried := self._supersede_pending_turn()) is not None:
            self._turn_events.pop(carried[0], None)
        if self.state in _INTERRUPTIBLE:
            self._begin_interrupt()
        self._start_turn(turn_id, text=text)

    async def handle_ack(self, turn_id: str, sentence_id: int) -> None:
        turn = self._turn
        accepted = turn is not None and turn.turn_id == turn_id and turn.ack(sentence_id)
        self._emit(
            PlaybackAcked(
                session_id=self.session_id,
                turn_id=turn_id,
                sentence_id=sentence_id,
                accepted=accepted,
            )
        )

    async def handle_flushed(self, turn_id: str, last_acked_sentence_id: int | None) -> None:
        future = self._flushed.get(turn_id)
        if future is not None and not future.done():
            future.set_result(last_acked_sentence_id)

    # ── events and state ──────────────────────────────────────────────────────────────────

    def _emit(self, event: Event) -> Event:
        published = self._bus.publish(event)
        if event.turn_id in self._turn_events:
            self._turn_events[event.turn_id].append(published)
        return published

    def _error(
        self, stage: ErrorStage, message: str, recoverable: bool, turn_id: str | None = None
    ) -> None:
        self._emit(
            Error(
                session_id=self.session_id,
                turn_id=turn_id,
                stage=stage,
                message=message,
                recoverable=recoverable,
            )
        )

    def _on_transition(self, previous: S, current: S, reason: R) -> None:
        self._emit(
            SessionStateChanged(
                session_id=self.session_id,
                turn_id=self._turn.turn_id if self._turn else None,
                previous=previous,
                current=current,
                reason=reason,
            )
        )

    def _open_turn_id(self) -> str:
        turn_id = _new_turn_id()
        self._turn_events[turn_id] = []
        return turn_id

    # ── inbound speech ────────────────────────────────────────────────────────────────────

    def _on_speech_start(self) -> None:
        self._in_speech = True
        self._utterance.clear()
        if (carried := self._supersede_pending_turn()) is not None:
            # The user paused long enough to end an utterance, then kept talking:
            # drop the pending transcription and treat it all as one utterance.
            turn_id, audio = carried
            self._utterance += audio
        else:
            turn_id = self._open_turn_id()
        self._utterance += b"".join(self._preroll)
        self._preroll.clear()
        self._speech_turn_id = turn_id
        self._emit(UserSpeechStarted(session_id=self.session_id, turn_id=turn_id))
        if self.state in _INTERRUPTIBLE:
            self._begin_interrupt()

    def _on_speech_end(self, silence_ms: float) -> None:
        assert self._speech_turn_id is not None
        self._in_speech = False
        audio = bytes(self._utterance)
        self._utterance.clear()
        turn_id, self._speech_turn_id = self._speech_turn_id, None
        self._emit(
            UserSpeechEnded(
                session_id=self.session_id,
                turn_id=turn_id,
                duration_ms=round(len(audio) / 2 / SAMPLE_RATE * 1000, 1),
                endpoint_ms=silence_ms,
            )
        )
        self._start_turn(turn_id, audio=audio)

    def _supersede_pending_turn(self) -> tuple[str, bytes] | None:
        """Cancel the turn task if it has not started responding yet.

        Returns (turn_id, audio) when that turn was still transcribing speech, so the
        caller can fold the audio into the new utterance.
        """
        task = self._turn_task
        responding = self._turn is not None and self._turn.turn_id == self._turn_task_id
        if task is None or task.done() or responding:
            return None
        task.cancel()
        carried, self._transcribing = self._transcribing, None
        if carried is None:
            self._turn_events.pop(self._turn_task_id or "", None)
        return carried

    # ── barge-in ──────────────────────────────────────────────────────────────────────────

    def _begin_interrupt(self) -> None:
        turn, task = self._turn, self._turn_task
        assert turn is not None and task is not None, "interruptible state without a turn"
        cancelled: list[Literal["generation", "tts", "tool", "playback"]] = []
        if not turn.generation_done:
            cancelled.append("generation")
        if any(
            s.status in (SentenceStatus.PENDING, SentenceStatus.SENDING) for s in turn.sentences
        ):
            cancelled.append("tts")
        if self.state is S.TOOL_EXECUTION:
            spec = self._tools.get(self._active_tool or "")
            if spec is None or spec.risk is not ToolRisk.SIDE_EFFECT:
                cancelled.append("tool")
        if turn.has_unacked_audio():
            cancelled.append("playback")
        self._emit(
            UserBargeIn(
                session_id=self.session_id,
                turn_id=turn.turn_id,
                interrupted_state=self.state,
                cancelled=cancelled,
            )
        )
        self._machine.transition(S.INTERRUPTED, R.USER_BARGE_IN)
        self._interrupt_task = asyncio.create_task(
            self._run_interrupt(turn, task), name=f"interrupt-{turn.turn_id}"
        )

    async def _run_interrupt(self, turn: AssistantTurn, task: asyncio.Task[None]) -> None:
        # Cancelling the turn task stops the LLM stream and TTS: no audio for this turn
        # is sent after this await returns.
        task.cancel()
        await self._await_stopped(task, turn.turn_id)
        await self._close_turn(turn, TurnOutcome.INTERRUPTED)
        self._machine.transition(S.LISTENING, R.INTERRUPTION_HANDLED)

    async def _await_stopped(self, task: asyncio.Task[None], turn_id: str) -> None:
        done, _ = await asyncio.wait({task}, timeout=self.config.cancel_timeout_s)
        if not done:
            self._error(
                "cancellation",
                f"turn task did not stop within {self.config.cancel_timeout_s}s",
                True,
                turn_id,
            )

    # ── turn lifecycle ────────────────────────────────────────────────────────────────────

    def _start_turn(
        self, turn_id: str, *, audio: bytes | None = None, text: str | None = None
    ) -> None:
        if audio is not None:
            self._transcribing = (turn_id, audio)
        # One turn at a time: wait for the previous turn and any interruption to finish.
        predecessors = {
            t for t in (self._turn_task, self._interrupt_task) if t is not None and not t.done()
        }
        task = asyncio.create_task(
            self._run_turn(turn_id, audio, text, predecessors), name=f"turn-{turn_id}"
        )
        task.add_done_callback(self._on_turn_task_done)
        self._turn_task, self._turn_task_id = task, turn_id

    async def _run_turn(
        self,
        turn_id: str,
        audio: bytes | None,
        text: str | None,
        predecessors: set[asyncio.Task[None]],
    ) -> None:
        if predecessors:
            # asyncio.wait never cancels what it waits on: cancelling this turn
            # cannot abort the previous turn's cleanup.
            await asyncio.wait(predecessors)

        if audio is not None:
            stt = self._adapters.stt
            assert stt is not None
            started = time.monotonic()
            try:
                user_text = (await stt.transcribe(audio)).strip()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._transcribing = None
                self._error("stt", f"{type(exc).__name__}: {exc}", True, turn_id)
                self._turn_events.pop(turn_id, None)
                return
            self._transcribing = None
            self._emit(
                TranscriptFinal(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    text=user_text,
                    source="stt",
                    stt_ms=round((time.monotonic() - started) * 1000, 1),
                )
            )
        else:
            assert text is not None
            user_text = text
            self._emit(
                TranscriptFinal(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    text=text,
                    source="text",
                    stt_ms=None,
                )
            )

        if not user_text:
            self._turn_events.pop(turn_id, None)
            return
        if self._background_tools:
            # A side-effecting tool from an interrupted turn is still running. The LLM must
            # see its outcome before answering, or it may deny an action that happened.
            # Bounded by each tool's own timeout.
            await asyncio.wait(set(self._background_tools))
        await self._respond(turn_id, user_text)

    async def _respond(self, turn_id: str, user_text: str) -> None:
        turn = AssistantTurn(turn_id)
        self._turn = turn
        self.conversation.add_user(user_text)
        self._emit(
            AgentTurnStarted(session_id=self.session_id, turn_id=turn_id, user_text=user_text)
        )
        self._machine.transition(S.THINKING, R.TRANSCRIPT_FINAL)

        try:
            await self._generate_and_speak(turn)
        except asyncio.CancelledError:
            raise  # barge-in: _run_interrupt finalizes the turn
        except _StageFailure as exc:
            await self._fail_turn(turn, exc.stage, str(exc))
            return

        if not turn.sentences:
            self._machine.transition(S.LISTENING, R.EMPTY_RESPONSE)
            self._finish_turn(turn, TurnOutcome.COMPLETED)
            return

        if self.state is S.THINKING:
            self._machine.transition(S.SPEAKING, R.AWAITING_PLAYBACK)
        timeout = turn.audio_seconds_sent + self.config.playback_grace_s
        try:
            await asyncio.wait_for(turn.playback_done.wait(), timeout=timeout)
        except TimeoutError:
            self._error(
                "transport",
                f"playback not confirmed within {timeout:.1f}s; "
                "committing acknowledged sentences only",
                True,
                turn_id,
            )
        await self._close_turn(turn, TurnOutcome.COMPLETED)
        self._machine.transition(S.LISTENING, R.PLAYBACK_COMPLETE)

    async def _fail_turn(self, turn: AssistantTurn, stage: ErrorStage, message: str) -> None:
        self._error(stage, message, True, turn.turn_id)
        self._machine.transition(S.ERROR, R.TURN_FAILED)
        await self._close_turn(turn, TurnOutcome.FAILED)
        self._machine.transition(S.LISTENING, R.RECOVERED)

    async def _close_turn(self, turn: AssistantTurn, outcome: TurnOutcome) -> None:
        """Stop client playback if audio is outstanding, then commit."""
        if turn.has_unacked_audio():
            future: asyncio.Future[int | None] = asyncio.get_running_loop().create_future()
            self._flushed[turn.turn_id] = future
            try:
                await self._transport.send_flush(turn.turn_id)
                turn.apply_flushed(
                    await asyncio.wait_for(future, timeout=self.config.flush_timeout_s)
                )
            except TimeoutError:
                self._error(
                    "transport",
                    f"client did not confirm flush within {self.config.flush_timeout_s}s; "
                    "committing acknowledged sentences only",
                    True,
                    turn.turn_id,
                )
            except Exception as exc:
                self._error(
                    "transport", f"flush failed: {type(exc).__name__}: {exc}", True, turn.turn_id
                )
            finally:
                self._flushed.pop(turn.turn_id, None)
        self._finish_turn(turn, outcome)

    def _finish_turn(self, turn: AssistantTurn, outcome: TurnOutcome) -> None:
        commit = turn.finalize(outcome)
        self.conversation.add_assistant(commit.spoken_text)
        self._emit(
            AgentTurnFinished(
                session_id=self.session_id,
                turn_id=turn.turn_id,
                outcome=commit.outcome,
                spoken_text=commit.spoken_text,
                unspoken_text=commit.unspoken_text,
                sentences_acked=commit.sentences_acked,
                sentences_total=commit.sentences_total,
                timings=stage_timings(self._turn_events.get(turn.turn_id, [])),
            )
        )
        self._turn_events.pop(turn.turn_id, None)
        if self._turn is turn:
            self._turn = None

    def _on_turn_task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or task.exception() is None:
            return
        # A bug in the runtime, not an adapter failure. Surface it and keep the session alive.
        exc = task.exception()
        log.error("turn task crashed", exc_info=exc)
        turn = self._turn
        self._error(
            "runtime",
            f"turn crashed: {type(exc).__name__}: {exc}",
            False,
            turn.turn_id if turn else None,
        )
        if turn is not None and not turn.finalized:
            self._finish_turn(turn, TurnOutcome.FAILED)
        if self.state not in (S.LISTENING, S.IDLE, S.ERROR):
            self._machine.transition(S.ERROR, R.TURN_FAILED)
        if self.state is S.ERROR:
            self._machine.transition(S.LISTENING, R.RECOVERED)

    # ── generation and speech ─────────────────────────────────────────────────────────────

    async def _generate_and_speak(self, turn: AssistantTurn) -> None:
        queue: asyncio.Queue[Sentence | None] = asyncio.Queue()
        generator = asyncio.create_task(self._generate(turn, queue), name=f"llm-{turn.turn_id}")
        speaker = asyncio.create_task(self._speak(turn, queue), name=f"tts-{turn.turn_id}")
        try:
            done, _ = await asyncio.wait({generator, speaker}, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if (exc := task.exception()) is not None:
                    raise exc
            await speaker
        finally:
            for task in (generator, speaker):
                task.cancel()
            await asyncio.gather(generator, speaker, return_exceptions=True)
        turn.finish_generation()

    async def _generate(self, turn: AssistantTurn, queue: asyncio.Queue[Sentence | None]) -> None:
        filler = self._schedule_filler(turn, queue)
        try:
            await self._generate_answer(turn, queue, filler)
        finally:
            if filler is not None:
                filler.cancel()
        await queue.put(None)

    def _schedule_filler(
        self, turn: AssistantTurn, queue: asyncio.Queue[Sentence | None]
    ) -> asyncio.Task[None] | None:
        phrases = self.config.filler_phrases
        if not phrases:
            return None

        async def speak_filler() -> None:
            await asyncio.sleep(self.config.filler_after_s)
            phrase = phrases[self._fillers_spoken % len(phrases)]
            self._fillers_spoken += 1
            await queue.put(turn.add(phrase, filler=True))

        return asyncio.create_task(speak_filler(), name=f"filler-{turn.turn_id}")

    async def _generate_answer(
        self,
        turn: AssistantTurn,
        queue: asyncio.Queue[Sentence | None],
        filler: asyncio.Task[None] | None,
    ) -> None:
        async def say(text: str) -> None:
            if filler is not None:
                filler.cancel()  # the answer is ready: no acknowledgement needed any more
            await queue.put(turn.add(text))

        for call in self._plan(turn):
            await self._run_tool(turn, call, requested_by="planner")
        splitter = SentenceSplitter()
        for round_index in range(self.config.max_tool_rounds + 1):
            calls: list[ToolCallRequest] = []
            try:
                async for chunk in self._adapters.llm.stream(
                    self.conversation.messages, self._tools.schemas()
                ):
                    if isinstance(chunk, TextDelta):
                        for text in splitter.feed(chunk.text):
                            await say(text)
                    else:
                        calls.append(chunk)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise _StageFailure("llm", exc) from exc
            for text in splitter.flush():
                await say(text)

            if not calls:
                break
            if round_index == self.config.max_tool_rounds:
                self._error(
                    "llm",
                    f"tool-call limit reached ({self.config.max_tool_rounds} rounds); ending turn",
                    True,
                    turn.turn_id,
                )
                break
            for call in calls:
                await self._run_tool(turn, call, requested_by="llm")

    def _plan(self, turn: AssistantTurn) -> list[ToolCallRequest]:
        if self.config.planner is None:
            return []
        try:
            return self.config.planner(self.conversation.messages)
        except Exception as exc:
            # A planner bug must not cost the turn: the LLM can still call tools itself.
            self._error("planner", f"{type(exc).__name__}: {exc}", True, turn.turn_id)
            return []

    async def _run_tool(
        self,
        turn: AssistantTurn,
        call: ToolCallRequest,
        requested_by: Literal["llm", "planner"],
    ) -> None:
        if self.state in (S.THINKING, S.SPEAKING):
            self._machine.transition(S.TOOL_EXECUTION, R.TOOL_REQUESTED)
        task = asyncio.create_task(
            self._executor.execute(
                call, session_id=self.session_id, turn_id=turn.turn_id, requested_by=requested_by
            ),
            name=f"tool-{call.name}-{call.call_id}",
        )
        task.add_done_callback(functools.partial(self._record_tool_result, call))
        spec = self._tools.get(call.name)
        self._active_tool = call.name
        if spec is not None and spec.risk is ToolRisk.SIDE_EFFECT:
            # Never cancel a side-effecting tool mid-flight: on barge-in it keeps running
            # and its result is still recorded.
            self._background_tools.add(task)
            task.add_done_callback(self._background_tools.discard)
            await asyncio.shield(task)
        else:
            await task
        if self.state is S.TOOL_EXECUTION:
            self._machine.transition(S.THINKING, R.TOOL_RESULT)

    def _record_tool_result(self, call: ToolCallRequest, task: asyncio.Task[ToolResult]) -> None:
        if task.cancelled():
            return  # TOOL_CALL_FAILED(cancelled) already emitted; nothing happened to commit
        if (exc := task.exception()) is not None:
            self._error("tool", f"executor crashed: {type(exc).__name__}: {exc}", False)
            return
        self.conversation.add_tool_exchange(call, task.result())

    async def _speak(self, turn: AssistantTurn, queue: asyncio.Queue[Sentence | None]) -> None:
        while (sentence := await queue.get()) is not None:
            await self._speak_sentence(turn, sentence)

    async def _speak_sentence(self, turn: AssistantTurn, sentence: Sentence) -> None:
        tts = self._adapters.tts
        synth_started = time.monotonic()
        turn.mark_sending(sentence.id)
        index = 0
        seconds = 0.0
        reason: Literal["completed", "cancelled"] = "cancelled"

        async def send(data: bytes, is_last: bool) -> None:
            nonlocal index
            if index == 0:
                self._emit(
                    TtsStarted(
                        session_id=self.session_id,
                        turn_id=turn.turn_id,
                        sentence_id=sentence.id,
                        text=sentence.text,
                        synth_ms=round((time.monotonic() - synth_started) * 1000, 1),
                        filler=sentence.filler,
                    )
                )
                if self.state is S.THINKING:
                    self._machine.transition(S.SPEAKING, R.AUDIO_STARTED)
            try:
                await self._transport.send_audio(
                    AudioOut(
                        turn_id=turn.turn_id,
                        sentence_id=sentence.id,
                        index=index,
                        data=data,
                        is_last=is_last,
                        sample_rate=tts.sample_rate,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise _StageFailure("transport", exc) from exc
            index += 1

        try:
            try:
                async for chunk in tts.synthesize(sentence.text):
                    seconds += len(chunk) / 2 / tts.sample_rate
                    await send(chunk, is_last=False)
            except (asyncio.CancelledError, _StageFailure):
                raise
            except Exception as exc:
                raise _StageFailure("tts", exc) from exc
            # Empty terminal chunk: tells the client the sentence is complete.
            await send(b"", is_last=True)
            reason = "completed"
        finally:
            if index > 0:
                self._emit(
                    TtsStopped(
                        session_id=self.session_id,
                        turn_id=turn.turn_id,
                        sentence_id=sentence.id,
                        reason=reason,
                    )
                )
        turn.mark_sent(sentence.id, seconds)
