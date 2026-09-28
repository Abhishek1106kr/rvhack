import { Button } from "@/components/ui/button";
import type { MicState } from "@/lib/arc/microphone";
import { SESSION_STATES } from "@/lib/arc/protocol";
import type { SessionView } from "@/lib/arc/session-view";
import type { Connection } from "@/lib/arc/use-arc-session";
import { cn } from "@/lib/utils";

import { STATE_STYLE } from "./state-colors";

const CONNECTION_LABEL: Record<Connection, string> = {
  connecting: "connecting…",
  open: "connected",
  closed: "disconnected · retrying",
};

const MIC_LABEL: Record<MicState, string> = {
  off: "off",
  starting: "starting…",
  live: "live",
  denied: "permission denied",
  unavailable: "no microphone",
  error: "failed to start",
};

function MicControl({
  available,
  state,
  level,
  userSpeaking,
  onStart,
  onStop,
}: {
  available: boolean;
  state: MicState;
  level: number;
  userSpeaking: boolean;
  onStart: () => void;
  onStop: () => void;
}) {
  if (!available) return <span className="text-muted-foreground">no VAD/STT adapter</span>;
  const live = state === "live";
  return (
    <span className="flex items-center gap-2">
      <Button
        size="xs"
        variant={live ? "destructive" : "outline"}
        onClick={live ? onStop : onStart}
        disabled={state === "starting"}
      >
        {live ? "stop mic" : "start mic"}
      </Button>
      <span className={cn(state === "denied" || state === "error" ? "text-red-700" : "")}>
        {MIC_LABEL[state]}
      </span>
      {live && (
        <>
          {/* Input RMS, scaled so normal speech fills most of the bar. */}
          <span className="h-1.5 w-16 overflow-hidden rounded bg-muted" title="input level">
            <span
              className="block h-full bg-emerald-600"
              style={{ width: `${Math.min(100, level * 400)}%` }}
            />
          </span>
          <span className={userSpeaking ? "text-emerald-700" : "text-muted-foreground"}>
            {userSpeaking ? "VAD: speech" : "VAD: silence"}
          </span>
        </>
      )}
    </span>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-muted-foreground">{label}</span>
      <span>{children}</span>
    </div>
  );
}

export function StatusBar({
  view,
  connection,
  audioUnlocked,
  playing,
  micState,
  micLevel,
  onUnlockAudio,
  onStartMic,
  onStopMic,
}: {
  view: SessionView;
  connection: Connection;
  audioUnlocked: boolean;
  playing: boolean;
  micState: MicState;
  micLevel: number;
  onUnlockAudio: () => void;
  onStartMic: () => void;
  onStopMic: () => void;
}) {
  const adapters = view.adapters;
  const micAvailable = Boolean(adapters?.vad && adapters?.stt);

  return (
    <div className="flex flex-col gap-2 border-b px-4 py-2 font-mono text-xs">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
        <span className="font-sans text-sm font-semibold">ARC control room</span>
        <Field label="link">
          <span
            className={cn(
              connection === "open" && "text-emerald-700",
              connection === "closed" && "text-red-700",
            )}
          >
            {CONNECTION_LABEL[connection]}
          </span>
        </Field>
        <Field label="session">{view.sessionId ?? "—"}</Field>
        <Field label="mic">
          <MicControl
            available={micAvailable}
            state={micState}
            level={micLevel}
            userSpeaking={view.userSpeaking}
            onStart={onStartMic}
            onStop={onStopMic}
          />
        </Field>
        <Field label="speaker">
          {audioUnlocked ? (
            playing ? "playing" : "ready"
          ) : (
            <Button size="xs" variant="outline" onClick={onUnlockAudio}>
              enable audio
            </Button>
          )}
        </Field>
        {adapters && (
          <Field label="adapters">
            vad={adapters.vad ?? "—"} stt={adapters.stt ?? "—"} llm={adapters.llm} tts=
            {adapters.tts}
          </Field>
        )}
        {view.sessionId && (
          <a
            className="ml-auto underline underline-offset-2"
            href={`/api/sessions/${view.sessionId}/trace`}
            target="_blank"
            rel="noreferrer"
          >
            trace.jsonl
          </a>
        )}
      </div>

      <ol className="flex flex-wrap items-center gap-1" aria-label="session state">
        {SESSION_STATES.map((state) => {
          const current = state === view.state;
          return (
            <li
              key={state}
              aria-current={current ? "step" : undefined}
              className={cn(
                "rounded border px-2 py-0.5",
                current ? STATE_STYLE[state].active : "border-transparent text-muted-foreground",
              )}
            >
              {state}
            </li>
          );
        })}
        {view.stateReason && (
          <li className="pl-2 text-muted-foreground">reason: {view.stateReason}</li>
        )}
      </ol>
    </div>
  );
}
