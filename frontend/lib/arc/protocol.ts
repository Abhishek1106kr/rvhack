// Wire types for /ws/session. Mirrors backend/app/events.py and backend/app/ws.py —
// the backend is the source of truth; keep these in sync when events change.

export type SessionState =
  | "IDLE"
  | "LISTENING"
  | "THINKING"
  | "TOOL_EXECUTION"
  | "SPEAKING"
  | "INTERRUPTED"
  | "ERROR";

export const SESSION_STATES: SessionState[] = [
  "IDLE",
  "LISTENING",
  "THINKING",
  "TOOL_EXECUTION",
  "SPEAKING",
  "INTERRUPTED",
  "ERROR",
];

export type StageTimings = {
  endpoint_ms: number | null;
  stt_ms: number | null;
  llm_ms: number | null;
  tool_ms: number | null;
  tts_ms: number | null;
  total_ms: number | null;
};

type Base = {
  session_id: string;
  turn_id: string | null;
  seq: number;
  timestamp: number;
  mono: number;
};

export type ArcEvent = Base &
  (
    | {
        event_type: "SESSION_STARTED";
        adapters: { vad: string | null; stt: string | null; llm: string; tts: string };
        tools: string[];
      }
    | {
        event_type: "SESSION_STATE_CHANGED";
        previous: SessionState;
        current: SessionState;
        reason: string;
      }
    | { event_type: "USER_SPEECH_STARTED" }
    | { event_type: "USER_SPEECH_ENDED"; duration_ms: number; endpoint_ms: number }
    | { event_type: "TRANSCRIPT_PARTIAL"; text: string }
    | {
        event_type: "TRANSCRIPT_FINAL";
        text: string;
        source: "stt" | "text";
        stt_ms: number | null;
      }
    | { event_type: "AGENT_TURN_STARTED"; user_text: string }
    | {
        event_type: "AGENT_TURN_FINISHED";
        outcome: "completed" | "interrupted" | "failed";
        spoken_text: string;
        unspoken_text: string;
        sentences_acked: number;
        sentences_total: number;
        timings: StageTimings;
      }
    | {
        event_type: "TOOL_CALL_STARTED";
        call_id: string;
        tool: string;
        arguments: Record<string, unknown>;
      }
    | {
        event_type: "TOOL_CALL_FINISHED";
        call_id: string;
        tool: string;
        result: unknown;
        duration_ms: number;
      }
    | {
        event_type: "TOOL_CALL_FAILED";
        call_id: string;
        tool: string;
        error_kind: string;
        message: string;
        duration_ms: number | null;
      }
    | { event_type: "TTS_STARTED"; sentence_id: number; text: string; synth_ms: number }
    | { event_type: "TTS_STOPPED"; sentence_id: number; reason: "completed" | "cancelled" }
    | { event_type: "PLAYBACK_ACKED"; sentence_id: number; accepted: boolean }
    | {
        event_type: "USER_BARGE_IN";
        interrupted_state: SessionState;
        cancelled: ("generation" | "tts" | "tool" | "playback")[];
      }
    | { event_type: "ERROR"; stage: string; message: string; recoverable: boolean }
  );

export type EventOf<T extends ArcEvent["event_type"]> = Extract<ArcEvent, { event_type: T }>;

export type AudioMessage = {
  type: "audio";
  turn_id: string;
  sentence_id: number;
  index: number;
  is_last: boolean;
  sample_rate: number;
  data: string; // base64 PCM16 mono
};

export type ServerMessage =
  | { type: "event"; event: ArcEvent }
  | AudioMessage
  | { type: "flush"; turn_id: string };

export type ClientMessage =
  | { type: "text"; text: string }
  | { type: "ack"; turn_id: string; sentence_id: number }
  | { type: "flushed"; turn_id: string; last_acked_sentence_id: number | null };
