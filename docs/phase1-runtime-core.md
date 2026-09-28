# Phase 1 — Runtime Core Specification

Scope: the ARC runtime core, with deterministic fake adapters and tests.
Runs with **no** GPU, microphone, sound device, Ollama, Whisper weights, or Piper weights.

Out of scope for Phase 1: real model adapters, `problem/`, chaos, evals runner.
Built beyond the original scope: the WebSocket transport (`app/ws.py`) and the control room
(`frontend/`), driven by the dev adapters `EchoLLM` + `ToneTTS` (no models, no mic).

Run: `cd backend && uv run pytest` · Lint: `uv run ruff check .`

---

## 0. Decisions already made

| Decision | Consequence |
|---|---|
| ARC owns the loop in plain `asyncio`; Pipecat is **not** the orchestrator | State, turns, cancellation, barge-in, events, tools, trace are ARC code |
| One interface per adapter; fakes implement the **same** Protocol as real adapters | There is no separate "fake architecture". Tests drive the real orchestrator. |
| Playback truth lives in the browser | "Spoken" = sentence ACKed by the client. **Sentence granularity, not word.** |
| Only runtime dependency in Phase 1 is `pydantic` | Add FastAPI etc. only when the transport is built (Phase 3) |

---

## 1. Module layout

```
backend/app/
  events.py          Event models + EventType. The single source of event shapes.
  bus.py             EventBus: publish/subscribe, assigns seq numbers.
  trace.py           TraceStore (bus subscriber) + stage-timing derivation.
  session/
    state.py         SessionState, allowed-transition table, SessionStateMachine.
  agent/
    turn.py          AssistantTurn: sentences, ACKs, commit rule.
    conversation.py  Conversation history — committed content only.
    orchestrator.py  Session orchestrator: the realtime loop, barge-in, cancellation.
  tools/
    registry.py      ToolSpec, ToolRegistry.
    executor.py      ToolExecutor: validate → STARTED → run w/ timeout → FINISHED/FAILED.
  adapters/
    base.py          Protocols: VAD, STT, LLM, TTS, Transport + their data types.
    fake.py          Deterministic implementations of every Protocol in base.py.
backend/tests/       One file per concern (see §9).
```

Rule: `backend/app` never imports `problem`, `evals`, or `chaos`.
Fakes live in `app/adapters/fake.py` (not `tests/`) because `evals/` and `chaos/`
will reuse them later.

---

## 2. Events (`events.py`)

Every event is a Pydantic model with:

| Field | Type | Notes |
|---|---|---|
| `event_type` | `Literal[...]` | Discriminator |
| `session_id` | `str` | |
| `timestamp` | `float` | Wall clock, seconds (for humans/UI) |
| `mono` | `float` | `time.monotonic()` — **all latency math uses this** |
| `seq` | `int` | Assigned by the bus, strictly increasing per session. Enables dedupe/order checks (chaos later). |
| `turn_id` | `str \| None` | Correlates everything in one user→assistant exchange |

Use a discriminated union (`Annotated[Union[...], Field(discriminator="event_type")]`)
so `TypeAdapter(Event).validate_json(...)` round-trips any event. No `dict` payloads.

| Event | Payload |
|---|---|
| `SESSION_STARTED` | `adapters` (vad/stt/llm/tts names), `tools` |
| `SESSION_STATE_CHANGED` | `previous`, `current`, `reason` |
| `USER_SPEECH_STARTED` | — |
| `USER_SPEECH_ENDED` | `duration_ms` |
| `TRANSCRIPT_PARTIAL` | `text` |
| `TRANSCRIPT_FINAL` | `text`, `source` (`stt` \| `text`), `stt_ms` |
| `AGENT_TURN_STARTED` | `user_text` |
| `AGENT_TURN_FINISHED` | `outcome` (`completed` \| `interrupted` \| `failed`), `spoken_text`, `unspoken_text`, `sentences_acked`, `sentences_total`, `timings` (§3) |
| `TOOL_CALL_STARTED` | `call_id`, `tool`, `arguments` (validated) |
| `TOOL_CALL_FINISHED` | `call_id`, `tool`, `result`, `duration_ms` |
| `TOOL_CALL_FAILED` | `call_id`, `tool`, `error_kind`, `message`, `duration_ms` |
| `TTS_STARTED` | `sentence_id`, `text`, `synth_ms` (sentence ready → first chunk sent) |
| `TTS_STOPPED` | `sentence_id`, `reason` (`completed` \| `cancelled`) |
| `PLAYBACK_ACKED` | `sentence_id` |
| `USER_BARGE_IN` | `interrupted_state`, `cancelled`: `generation` / `tts` / `tool` (read-only only) / `playback` (client audio stopped) |
| `ERROR` | `stage`, `message`, `recoverable: bool` |

`PLAYBACK_ACKED` is an addition to the CLAUDE.md minimum: without it the trace
cannot answer "what did the assistant actually say?".

---

## 3. Event bus + trace (`bus.py`, `trace.py`)

- `EventBus.publish(event)`: stamps `seq`, fans out to subscribers. In-process only.
- A subscriber raising must **not** break publishing or the session; the bus
  catches it and (if possible) emits `ERROR(stage="bus")`.
- `TraceStore` subscribes and keeps an ordered list per `session_id`.
  `trace(session_id) -> list[Event]`, `to_jsonl()` for dumping.

Stage timings, derived from the trace per `turn_id` (all from `mono`):

| Stage | From → To |
|---|---|
| VAD / endpoint | last speech frame → `USER_SPEECH_ENDED` (adapter reports silence window used) |
| STT | `USER_SPEECH_ENDED` → `TRANSCRIPT_FINAL` |
| LLM | `AGENT_TURN_STARTED` → first sentence ready, i.e. `first TTS_STARTED.mono − synth_ms`, minus TOOL (first-sentence latency, not total) |
| TOOL | sum of tool `duration_ms` in the turn |
| TTS | `synth_ms` of the first `TTS_STARTED` |
| TOTAL | `USER_SPEECH_ENDED` → first `TTS_STARTED` |

Honest limit: TOTAL is **server-side** — it excludes network and browser
buffering. Client-side first-audio time is a Phase 3 addition.

No Prometheus/OTel/Redis. A list in memory.

---

## 4. Session state (`session/state.py`)

States: `IDLE, LISTENING, THINKING, TOOL_EXECUTION, SPEAKING, INTERRUPTED, ERROR`.

Allowed transitions (anything else raises `InvalidTransition` — a bug, not a runtime condition):

| From | To | Reason examples |
|---|---|---|
| IDLE | LISTENING | `session_started` |
| LISTENING | THINKING | `transcript_final` (empty transcript → no transition, stay LISTENING) |
| THINKING | SPEAKING | `audio_started`, `awaiting_playback` |
| THINKING | TOOL_EXECUTION | `tool_requested` |
| THINKING | INTERRUPTED | `user_barge_in` |
| THINKING | LISTENING | `empty_response` (LLM produced nothing speakable) |
| TOOL_EXECUTION | THINKING | `tool_result` |
| TOOL_EXECUTION | INTERRUPTED | `user_barge_in` |
| SPEAKING | TOOL_EXECUTION | `tool_requested` (LLM streamed text, then asked for a tool) |
| SPEAKING | LISTENING | `playback_complete` |
| SPEAKING | INTERRUPTED | `user_barge_in` |
| INTERRUPTED | LISTENING | `interruption_handled` |
| *any* | ERROR | `unrecoverable_error` |
| ERROR | LISTENING | `recovered` |
| *any* | IDLE | `session_ended` |

- Every successful transition publishes `SESSION_STATE_CHANGED`.
- `INTERRUPTED` is real, not skipped: it is held while cancellation + flush
  finish (§6), then → `LISTENING`. The UI must be able to see it.
- State lives only in the state machine. Never inferred from history.

---

## 5. Adapter interfaces (`adapters/base.py`)

`typing.Protocol`s. Real (Phase 2) and fake adapters implement exactly these.

```
AudioFrame        bytes PCM16 mono 16 kHz, fixed frame size (e.g. 32 ms / 512 samples — Silero's size)

VAD.process(frame) -> VADSignal | None        SPEECH_START / SPEECH_END (None = no change)
STT.transcribe(audio: bytes) -> Transcript    async; text + stt_ms. Cancellable.
LLM.stream(messages, tools) -> AsyncIterator[LLMChunk]
      LLMChunk = TextDelta(text) | ToolCallRequest(call_id, name, arguments: dict) | LLMDone
TTS.synthesize(text) -> AsyncIterator[bytes]  audio chunks for ONE sentence. Cancellable.
Transport (server side of the client connection):
      send_audio(turn_id, sentence_id, chunk_index, data, is_last)
      send_flush(turn_id)                       client must stop playback + drop queue
      send_event(event)                         for the control room
      inbound: AsyncIterator[ClientMessage]
      ClientMessage = AudioIn(frame) | PlaybackAck(turn_id, sentence_id)
                    | Flushed(turn_id, last_acked_sentence_id) | TextIn(text)
```

`LLM` output is **untrusted input**: `ToolCallRequest.arguments` is a raw dict that the
executor validates; unknown tool names are a normal failure, not an exception.

`TextIn` = text-mode fallback (typed input skips VAD/STT). Cheap now, saves the demo later.

### Fakes (`adapters/fake.py`) — deterministic, no sleeps-for-correctness

| Fake | Behaviour |
|---|---|
| `FakeVAD` | Frames are tagged; e.g. first byte `0x01` = speech, `0x00` = silence. Emits START on first speech frame, END after N silence frames. |
| `FakeSTT` | Returns scripted texts in order; optional `delay` and `fail_on` index. |
| `FakeLLM` | Scripted per turn: list of `LLMChunk`s, optional per-chunk `asyncio.Event` gates so a test can hold generation mid-stream. Records received messages (lets tests assert history). Records cancellation. |
| `FakeTTS` | Yields K deterministic chunks per sentence; optional gate per chunk. Records cancellation. |
| `FakeClient` / `FakeTransport` | In-memory queues. Records every outbound audio chunk. Playback is **test-controlled**: `await client.play(sentence_id)` sends `PlaybackAck`; on `send_flush` it replies `Flushed(last_acked)`. |

Gates (`asyncio.Event`) make tests deterministic: the test decides exactly when
the LLM/TTS/playback advances. `asyncio.sleep` is allowed only for testing
timeouts, with small values (≤ 0.2 s).

---

## 6. Turn lifecycle, sentence ACKs, commit semantics (`agent/turn.py`)

The central correctness rule: **history contains only what the user heard.**

```
LLM text deltas ──► sentence splitter ──► Sentence(id, text)
                                              │
                                     TTS.synthesize(text)
                                              │
                           Transport.send_audio(..., is_last)   (TTS_STARTED / TTS_STOPPED)
                                              │
                                    browser plays sentence
                                              │
                          PlaybackAck(turn_id, sentence_id)   ─► PLAYBACK_ACKED
                                              │
                                    sentence.acked = True
```

`AssistantTurn` holds sentences with status `generated → sending → sent → acked`.

**Commit rule** (applied exactly once, when the turn finalizes):

- `spoken_text` = concatenation of sentences with `acked = True`, in order.
- Only `spoken_text` is appended to `Conversation` as the assistant message.
- `unspoken_text` = everything else; recorded in `AGENT_TURN_FINISHED` for the
  trace, **never** in conversation history.
- Normal completion: turn finalizes when all sent sentences are ACKed
  (→ `SPEAKING → LISTENING`, reason `playback_complete`).
- Interrupted turn with zero acked sentences commits **no** assistant message.

**Granularity — stated honestly:** a sentence that was *partially* played at
barge-in is treated as **not spoken**. The user may have heard its first words;
ARC does not claim word-level certainty. This errs toward "the assistant may
repeat itself" rather than "the assistant believes it said something it didn't".

**The ACK race and how it is closed:** the client may finish sentence 2 and send
its ACK at the same moment the server detects barge-in. So on interruption the
server does not finalize immediately:

1. server sends `send_flush(turn_id)`;
2. client stops playback, drops its queue, replies `Flushed(turn_id, last_acked_sentence_id)`;
3. server finalizes using the **union** of ACKs received and `last_acked_sentence_id`;
4. if `Flushed` does not arrive within `flush_timeout` (default 0.5 s), finalize with
   ACKs received so far and emit `ERROR(stage="transport", recoverable=True)`.

ACKs arriving for an already-finalized turn are ignored (and traced).

Tool results: tool call + result messages are committed to history when the tool
finishes (the runtime executed them regardless of what was spoken). If a turn is
interrupted after a side-effecting tool ran, history must still show the tool ran.

---

## 7. Barge-in + cancellation (`agent/orchestrator.py`)

Trigger: `VAD` emits `SPEECH_START` while state ∈ {`THINKING`, `TOOL_EXECUTION`, `SPEAKING`}.

Order (each step observable in the trace):

1. publish `USER_BARGE_IN`; transition → `INTERRUPTED`;
2. cancel the LLM stream task;
3. cancel the TTS task (no further `send_audio` for this turn — **assert this in tests**);
4. `send_flush(turn_id)` and await `Flushed` (§6);
5. `TTS_STOPPED(reason="cancelled")` for any in-flight sentence;
6. finalize turn with the commit rule → `AGENT_TURN_FINISHED(outcome="interrupted")`;
7. transition `INTERRUPTED → LISTENING`, reason `interruption_handled`;
8. the new utterance is processed as a normal turn (its audio frames were
   already being buffered from step 0 — do not lose the start of the user's speech).

Tools during barge-in:

- `risk = read_only` → cancel the tool task.
- `risk = side_effect` → **do not cancel** (cancelling may leave external state
  half-written). Let it finish in the background, record its result/failure in
  the trace + history, discard the rest of the assistant turn.
- The next turn waits for in-flight side-effecting tools before calling the LLM
  (bounded by each tool's timeout), so the model never answers without knowing
  whether the action happened.

Implementation requirement: one `asyncio.Task` per active turn, owned by the
orchestrator. Cancellation = `task.cancel()` + await with a bound. No flags polled
inside loops as the primary mechanism. Every `CancelledError` path must leave the
state machine in a valid state — a test must fail (pytest-timeout = 5 s) rather
than hang if it doesn't.

---

## 8. Tool runtime (`tools/registry.py`, `tools/executor.py`)

```
ToolSpec
  name: str
  description: str
  input_model: type[BaseModel]            JSON schema for the LLM comes from this
  output_model: type[BaseModel] | None    if set, handler output is validated
  handler: async (input_model) -> Any
  timeout_s: float
  risk: ToolRisk  = read_only | side_effect
```

`ToolRegistry`: `register(spec)` (duplicate name → error at startup),
`get(name)`, `schemas()` for the LLM. The registry is populated by whoever
assembles the session (later: `problem/`). The orchestrator never hardcodes tools.

`ToolExecutor.execute(call) -> ToolResult` — never raises for tool problems:

| Step | On failure |
|---|---|
| look up tool | `TOOL_CALL_FAILED(error_kind="unknown_tool")`, no STARTED |
| validate args with `input_model` | `TOOL_CALL_FAILED(error_kind="invalid_arguments")`, no STARTED |
| publish `TOOL_CALL_STARTED` | |
| `asyncio.wait_for(handler, timeout_s)` | timeout → `error_kind="timeout"`; for `side_effect` tools: `"timeout_outcome_unknown"` |
| handler raises | `error_kind="exception"` |
| validate output (if `output_model`) | `error_kind="invalid_output"` |
| success | `TOOL_CALL_FINISHED` |

`ToolResult`: `ok: bool`, `output | error{kind, message}`. It goes back to the LLM
as a tool message; the LLM decides what to *say*, the runtime already decided what *happened*.

**No retries in the executor.** A retry of a read-only tool, if ever added, is an
explicit orchestrator decision that appears in the trace. Side-effecting tools are never retried.

---

## 9. Required tests (`backend/tests/`)

All must pass with only `uv sync` — no models, no network.

| File | Must prove |
|---|---|
| `test_events.py` | every event type round-trips JSON through the discriminated union; required base fields present; unknown `event_type` rejected |
| `test_state.py` | every allowed transition works and emits `SESSION_STATE_CHANGED` with previous/current/reason; a disallowed transition raises; any → ERROR → LISTENING |
| `test_bus_trace.py` | seq strictly increasing; trace preserves order per session; sessions isolated; a crashing subscriber does not stop delivery; stage timings computed from a synthetic event list |
| `test_tools.py` | register + duplicate rejection; schema export; success → STARTED, FINISHED; invalid args → FAILED only; unknown tool → FAILED; timeout → FAILED `timeout`; side-effect timeout → `timeout_outcome_unknown`; handler exception → FAILED; invalid output → FAILED; no retry (handler call count = 1) |
| `test_turn_commit.py` | pure unit tests of `AssistantTurn`: all acked → full commit; partial ack → prefix only; zero ack → nothing committed; late ACK after finalize ignored; `Flushed.last_acked` union |
| `test_session_loop.py` | full orchestrator with fakes: speech → transcript → LLM → TTS → ACKs → `LISTENING`; exact state sequence asserted; text-mode input path |
| `test_barge_in.py` | **the core test** — see below |
| `test_tool_turn.py` | LLM requests a tool → `TOOL_EXECUTION` → result fed back to LLM → spoken answer; tool failure → session continues to `LISTENING`, next turn works |
| `test_cancellation.py` | barge-in during `THINKING` cancels LLM; during read-only tool cancels it; during side-effect tool does **not** cancel it and its result is recorded; flush timeout path emits ERROR and still reaches `LISTENING` |

### `test_barge_in.py` — required cases

Setup: FakeLLM scripted with 3 sentences; FakeTTS; FakeClient with manual playback.

1. **Mid-response interruption.** Play (ACK) sentence 1. While sentence 2 is sent
   but not ACKed, feed speech frames.
   Assert: `USER_BARGE_IN`; states `SPEAKING → INTERRUPTED → LISTENING`;
   LLM and TTS recorded cancellation; **no `send_audio` for the turn after the
   flush**; `AGENT_TURN_FINISHED.spoken_text == sentence 1`,
   `unspoken_text` contains 2 and 3; the next LLM call's messages contain sentence 1
   and **not** sentence 2 or 3.
2. **ACK race.** Sentence 2's ACK arrives only via `Flushed(last_acked=2)`.
   Assert sentences 1–2 committed.
3. **Interrupt before any ACK.** No assistant message committed at all.
4. **Repeated interruption.** Interrupt turn A, then interrupt turn B;
   both turns finalize correctly, history contains only acked text from each,
   session ends in `LISTENING`.
5. **Next utterance processed.** After interruption, the interrupting utterance's
   transcript becomes the next turn's user message.

---

## 10. Phase 1 definition of done

- `uv run pytest` green, `uv run ruff check .` clean.
- No test depends on wall-clock timing except tool/flush timeout tests.
- `grep -r "problem" backend/app` finds no imports.
- A dumped trace (`to_jsonl`) of the barge-in test answers: what the user said,
  what STT produced, what the LLM generated, what was ACKed as spoken, that the
  turn was interrupted, and stage timings.

## 11. Known limitations (carry forward, don't hide)

- Spoken-content certainty is **sentence-level**.
- TOTAL latency is server-side until the client reports first-audio time.
- faster-whisper is not streaming; `TRANSCRIPT_PARTIAL` will be throttled
  re-transcription (Phase 2), possibly disabled on CPU.
- Echo: without AEC/headset, TTS audio can trigger VAD → false barge-in. Browser
  `echoCancellation: true` + headset for the demo.
- Barge-in during a side-effecting tool lets the tool finish; the user may hear
  nothing about its result until they ask.
