# CLAUDE.md

You are working inside ARC.

ARC is not a chatbot.
ARC is a realtime voice-agent runtime built for a hackathon where
the final problem is not known until later.

Your job is to make the runtime capable of absorbing a new problem
without rewriting the runtime.

DO NOT turn this repository into a generic AI-agent framework.
DO NOT create abstractions because they "might be useful later".
DO NOT add infrastructure unless the current feature requires it.
DO NOT replace working code with a new framework without a measured reason.

---

## 1. THE ARCHITECTURAL BORDER

The repository has one hard boundary:

    backend/app/       reusable runtime
    problem/           hackathon-specific behavior

`backend/app` must never import from `problem`.

If code only makes sense because of the hackathon problem,
it belongs in `problem/`.

If removing the hackathon problem would make the code useless,
it does not belong in `backend/app`.

Examples:

    "execute a tool"                    -> backend/app/tools
    "tool that checks order status"     -> problem/tools

    "conversation state"                -> backend/app/agent
    "customer order state"              -> problem/domain

    "tool timeout recovery"             -> backend/app
    "what to tell a customer after
     an order timeout"                  -> problem

This boundary is more important than folder aesthetics.

---

## 2. CURRENT STACK — DO NOT CHANGE WITHOUT A REASON

Backend:

- Python 3.12+
- FastAPI
- asyncio
- Pydantic
- Pipecat for realtime media pipeline
- faster-whisper for STT
- Silero VAD
- Ollama for local LLM inference
- Piper for local TTS

Frontend:

- Next.js
- TypeScript
- Tailwind
- shadcn/ui
- WebSocket
- Web Audio API

Package management:

- uv for Python
- pnpm for frontend

Do not introduce:

- LangChain
- LangGraph
- CrewAI
- AutoGen
- another agent framework
- another orchestration framework

unless there is a concrete feature that cannot reasonably be implemented
without it.

"Industry standard" is not a justification.

---

## 3. BEFORE WRITING CODE

For every non-trivial task:

1. inspect the existing implementation;
2. identify the actual execution path;
3. identify the smallest change that solves the problem;
4. modify existing code before creating another abstraction;
5. run the relevant tests;
6. manually exercise the realtime path if it affects audio/session behavior.

Do not generate a new subsystem because a folder name looks cleaner.

Do not duplicate an existing concept under a new name.

Search the repository before introducing:

- event types
- state objects
- registries
- managers
- service classes
- configuration objects
- utility functions

There should be one canonical implementation.

---

## 4. REALTIME SESSION

A session is an explicit state machine.

Minimum states:

    IDLE
    LISTENING
    THINKING
    TOOL_EXECUTION
    SPEAKING
    INTERRUPTED
    ERROR

Transitions must be explicit.

Do not infer session state from strings in chat history.

Every transition should produce a structured event.

Example:

    SESSION_STATE_CHANGED
    {
        previous: "SPEAKING",
        current: "LISTENING",
        reason: "user_barge_in"
    }

---

## 5. BARGE-IN IS NOT OPTIONAL

This is one of the core demo behaviors.

If the assistant is speaking and the user starts speaking:

    VAD detects speech
        ↓
    cancel TTS
        ↓
    cancel active generation
        ↓
    discard unplayed audio
        ↓
    mark assistant turn interrupted
        ↓
    listen to user

Never wait for the assistant to finish speaking.

Never let stale TTS audio continue after interruption.

Never commit text that the user never heard as if it was spoken.

Test this.

---

## 6. EVENTS ARE THE SOURCE OF TRUTH

The runtime communicates internally through typed events.

At minimum:

    SESSION_STARTED
    USER_SPEECH_STARTED
    USER_SPEECH_ENDED
    TRANSCRIPT_PARTIAL
    TRANSCRIPT_FINAL
    AGENT_TURN_STARTED
    TOOL_CALL_STARTED
    TOOL_CALL_FINISHED
    TOOL_CALL_FAILED
    TTS_STARTED
    TTS_STOPPED
    USER_BARGE_IN
    SESSION_STATE_CHANGED
    ERROR

Events must contain:

    session_id
    timestamp
    event_type

and event-specific payload.

Do not use arbitrary dictionaries when a Pydantic event model is appropriate.

---

## 7. TOOL EXECUTION

Tools are registered objects with:

    name
    description
    input schema
    handler
    timeout
    risk level

Tool execution must:

1. validate input;
2. emit TOOL_CALL_STARTED;
3. execute with timeout;
4. emit TOOL_CALL_FINISHED or TOOL_CALL_FAILED;
5. return a structured result.

A failed tool must not crash the entire voice session.

Never silently retry a side-effecting tool.

---

## 8. THE LLM DOES NOT CONTROL THE RUNTIME

The model may request:

    speak
    call tool
    ask clarification
    finish turn

The model does NOT directly control:

- session state
- permissions
- tool execution
- cancellation
- persistence
- event emission

Those belong to the runtime.

LLM output is untrusted input to the runtime.

---

## 9. OBSERVABILITY

Every demo session must produce a trace.

A trace must make it possible to answer:

    What did the user say?
    What did STT produce?
    What did the agent infer?
    Which tool was called?
    How long did it take?
    What failed?
    What did the assistant actually say?
    Was the turn interrupted?

Record stage timings:

    VAD
    STT
    LLM
    TOOL
    TTS
    TOTAL

Do not add a metrics platform just to display six numbers.

An in-process event store is sufficient until the demo requires persistence.

---

## 10. CONTROL ROOM

The UI is a debugging instrument, not decoration.

During a live session it should expose:

    microphone state
    current session state
    live transcript
    assistant response
    active tool
    tool result
    latency
    errors
    event timeline

The UI should let a judge understand what the system is doing
without us explaining every internal component verbally.

Do not build:

- glowing AI orb
- fake neural-network animation
- gradient-heavy hero section
- meaningless KPI cards
- fake "AI confidence: 97%" numbers

If a visual does not communicate system state, remove it.

---

## 11. EVALUATION

ARC must be testable without a human speaking into a microphone.

Every important behavior should have a scenario.

Examples:

    user_interrupts_assistant
    user_corrects_information
    tool_times_out
    tool_returns_invalid_data
    user_gives_ambiguous_request
    user_changes_intent_mid_turn
    STT_misrecognizes_entity
    assistant_is_interrupted_twice

A scenario should define:

    input
    expected state transitions
    expected tool calls
    expected final outcome

Do not evaluate agents using only "LLM said something reasonable".

---

## 12. CHAOS MODE

Chaos mode intentionally breaks the runtime.

It can inject:

    delayed STT
    delayed LLM
    TTS interruption
    tool timeout
    malformed tool result
    ambiguous user input
    dropped event
    duplicate event

The purpose is not theatrics.

The purpose is proving that the runtime has explicit recovery behavior.

If chaos mode cannot identify the expected recovery path,
the feature is not finished.

---

## 13. PROBLEM ADAPTER

When the hackathon problem is revealed:

DO NOT modify the realtime runtime unless the problem genuinely
requires a runtime capability that does not exist.

Instead implement:

    problem/domain.py
    problem/tools/
    problem/workflows/
    problem/knowledge/
    problem/prompts/
    problem/evaluation/

The final agent should be assembled from these pieces.

The judge should see a domain-specific agent.

The codebase should still clearly show that the voice runtime
is reusable.

---

## 14. PRIORITY ORDER

When time is limited:

P0:

    realtime audio
    STT
    LLM
    TTS
    barge-in
    tool execution
    problem correctness

P1:

    trace
    latency instrumentation
    evaluation scenarios
    failure recovery
    control room polish

P2:

    chaos mode
    advanced memory
    extra integrations
    persistent analytics

Never sacrifice P0 for P2.

---

## 15. DEFINITION OF DONE

A feature is not done because the code compiles.

For realtime features:

    unit test
    integration test
    manual voice test

For tools:

    schema validation
    success path
    timeout/failure path
    trace events

For UI:

    real backend data
    loading state
    error state
    disconnected state

For problem features:

    at least one deterministic evaluation scenario

---

## 16. WHEN YOU ARE UNSURE

Prefer:

    existing code > new abstraction
    explicit state > implicit behavior
    deterministic code > prompt magic
    local model > unnecessary API dependency
    measured latency > claimed latency
    working demo > architectural purity
    simple implementation > framework

Do not ask "how would a production company build this?"

Ask:

    "What is the smallest reliable implementation that preserves
     ARC's architecture and can survive the live demo?"

---

## 17. THE HACKATHON RULE

The final demo must prove two things simultaneously:

1. ARC can solve the problem.
2. ARC was built as infrastructure, not as a one-off chatbot.

The second point is what makes the repository worth examining
after the demo.

Build accordingly.
Strict: Do not add yourself in the commit ; Do not be the co-author 
Strict : Do not code vagur ; Do not produce generic UI; Do not produce AI slop code 
