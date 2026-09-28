import type { ArcEvent, EventOf, SessionState, StageTimings } from "./protocol";

// Folds the backend event stream into what the control room shows.
// Everything here is derived from events; nothing is guessed client-side.

export type SentenceView = {
  id: number;
  text: string;
  // sending: audio going out · sent: fully sent, not yet played · played: client ACKed
  // cut: TTS was cancelled mid-sentence
  status: "sending" | "sent" | "played" | "cut";
  filler: boolean; // runtime acknowledgement spoken while the answer was not ready
};

export type TurnView = {
  turnId: string;
  userText: string | null;
  source: "stt" | "text" | null;
  speaking: boolean; // user speech in progress for this turn
  sentences: SentenceView[];
  interruptedIn: SessionState | null;
  finished: EventOf<"AGENT_TURN_FINISHED"> | null;
};

export type ToolCallView = {
  callId: string;
  tool: string;
  arguments: Record<string, unknown> | null;
  requestedBy: "llm" | "planner" | null;
  startedMono: number | null;
  outcome: EventOf<"TOOL_CALL_FINISHED"> | EventOf<"TOOL_CALL_FAILED"> | null;
};

export type SessionView = {
  sessionId: string | null;
  startedMono: number | null;
  adapters: EventOf<"SESSION_STARTED">["adapters"] | null;
  tools: string[];
  state: SessionState;
  stateReason: string | null;
  userSpeaking: boolean;
  turns: TurnView[];
  toolCalls: ToolCallView[];
  errors: EventOf<"ERROR">[];
  events: ArcEvent[];
};

const EVENT_LIMIT = 400;

export const emptyView: SessionView = {
  sessionId: null,
  startedMono: null,
  adapters: null,
  tools: [],
  state: "IDLE",
  stateReason: null,
  userSpeaking: false,
  turns: [],
  toolCalls: [],
  errors: [],
  events: [],
};

function updateTurn(
  turns: TurnView[],
  turnId: string | null,
  update: (turn: TurnView) => TurnView,
): TurnView[] {
  if (!turnId) return turns;
  const index = turns.findIndex((t) => t.turnId === turnId);
  if (index === -1) {
    const fresh: TurnView = {
      turnId,
      userText: null,
      source: null,
      speaking: false,
      sentences: [],
      interruptedIn: null,
      finished: null,
    };
    return [...turns, update(fresh)];
  }
  return turns.map((t, i) => (i === index ? update(t) : t));
}

function setSentence(
  turn: TurnView,
  id: number,
  update: (s: SentenceView | undefined) => SentenceView,
): TurnView {
  const exists = turn.sentences.some((s) => s.id === id);
  return {
    ...turn,
    sentences: exists
      ? turn.sentences.map((s) => (s.id === id ? update(s) : s))
      : [...turn.sentences, update(undefined)],
  };
}

function updateToolCall(
  calls: ToolCallView[],
  callId: string,
  tool: string,
  update: (call: ToolCallView) => ToolCallView,
): ToolCallView[] {
  const index = calls.findIndex((c) => c.callId === callId && c.outcome === null);
  if (index === -1) {
    const fresh: ToolCallView = {
      callId,
      tool,
      arguments: null,
      requestedBy: null,
      startedMono: null,
      outcome: null,
    };
    return [...calls, update(fresh)];
  }
  return calls.map((c, i) => (i === index ? update(c) : c));
}

export function applyEvent(view: SessionView, event: ArcEvent): SessionView {
  const next: SessionView = {
    ...view,
    events: [...view.events.slice(-(EVENT_LIMIT - 1)), event],
  };
  const turnId = event.turn_id;

  switch (event.event_type) {
    case "SESSION_STARTED":
      return {
        ...next,
        sessionId: event.session_id,
        startedMono: event.mono,
        adapters: event.adapters,
        tools: event.tools,
      };
    case "SESSION_STATE_CHANGED":
      return { ...next, state: event.current, stateReason: event.reason };
    case "USER_SPEECH_STARTED":
      return {
        ...next,
        userSpeaking: true,
        turns: updateTurn(view.turns, turnId, (t) => ({ ...t, speaking: true })),
      };
    case "USER_SPEECH_ENDED":
      return {
        ...next,
        userSpeaking: false,
        turns: updateTurn(view.turns, turnId, (t) => ({ ...t, speaking: false })),
      };
    case "TRANSCRIPT_PARTIAL":
      return { ...next, turns: updateTurn(view.turns, turnId, (t) => ({ ...t, userText: event.text })) };
    case "TRANSCRIPT_FINAL":
      return {
        ...next,
        turns: updateTurn(view.turns, turnId, (t) => ({
          ...t,
          userText: event.text,
          source: event.source,
        })),
      };
    case "TTS_STARTED":
      return {
        ...next,
        turns: updateTurn(view.turns, turnId, (t) =>
          setSentence(t, event.sentence_id, () => ({
            id: event.sentence_id,
            text: event.text,
            status: "sending",
            filler: event.filler,
          })),
        ),
      };
    case "TTS_STOPPED":
      return {
        ...next,
        turns: updateTurn(view.turns, turnId, (t) =>
          setSentence(t, event.sentence_id, (s) => ({
            id: event.sentence_id,
            text: s?.text ?? "",
            status: event.reason === "completed" ? "sent" : "cut",
            filler: s?.filler ?? false,
          })),
        ),
      };
    case "PLAYBACK_ACKED":
      if (!event.accepted) return next;
      return {
        ...next,
        turns: updateTurn(view.turns, turnId, (t) =>
          setSentence(t, event.sentence_id, (s) => ({
            id: event.sentence_id,
            text: s?.text ?? "",
            status: "played",
            filler: s?.filler ?? false,
          })),
        ),
      };
    case "USER_BARGE_IN":
      return {
        ...next,
        turns: updateTurn(view.turns, turnId, (t) => ({
          ...t,
          interruptedIn: event.interrupted_state,
        })),
      };
    case "AGENT_TURN_FINISHED":
      return {
        ...next,
        turns: updateTurn(view.turns, turnId, (t) => ({ ...t, finished: event })),
      };
    case "TOOL_CALL_STARTED":
      return {
        ...next,
        toolCalls: updateToolCall(view.toolCalls, event.call_id, event.tool, (c) => ({
          ...c,
          arguments: event.arguments,
          requestedBy: event.requested_by,
          startedMono: event.mono,
        })),
      };
    case "TOOL_CALL_FINISHED":
    case "TOOL_CALL_FAILED":
      return {
        ...next,
        toolCalls: updateToolCall(view.toolCalls, event.call_id, event.tool, (c) => ({
          ...c,
          outcome: event,
        })),
      };
    case "ERROR":
      return { ...next, errors: [...view.errors, event] };
    default:
      return next;
  }
}

export function latestTimings(view: SessionView): { turnId: string; timings: StageTimings }[] {
  return view.turns
    .filter((t) => t.finished)
    .map((t) => ({ turnId: t.turnId, timings: t.finished!.timings }));
}
