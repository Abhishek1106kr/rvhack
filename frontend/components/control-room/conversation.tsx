"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SentenceView, SessionView, TurnView } from "@/lib/arc/session-view";

import { Empty, Panel } from "./panel";

const SENTENCE_MARK: Record<SentenceView["status"], { mark: string; title: string; className: string }> = {
  sending: { mark: "›", title: "audio being sent", className: "text-sky-700" },
  sent: { mark: "○", title: "sent, not yet confirmed played", className: "text-muted-foreground" },
  played: { mark: "●", title: "client confirmed it finished playing", className: "text-foreground" },
  cut: {
    mark: "✕",
    title: "not confirmed played: not spoken, not in history",
    className: "text-muted-foreground line-through",
  },
};

const OUTCOME_STYLE = {
  completed: "text-emerald-700",
  interrupted: "text-orange-700",
  failed: "text-red-700",
} as const;

function Turn({ turn }: { turn: TurnView }) {
  const finished = turn.finished;
  // Once the turn is committed, every sentence that was not confirmed played is final:
  // it was not spoken, whatever its last transport status was.
  const shown = turn.sentences.map((s) =>
    finished && s.status !== "played" ? { ...s, status: "cut" as const } : s,
  );
  const neverSynthesized = finished ? finished.sentences_total - turn.sentences.length : 0;
  return (
    <li className="border-b py-2 last:border-b-0">
      <div className="flex items-baseline gap-2">
        <span className="w-12 shrink-0 text-xs text-muted-foreground">user</span>
        <p className="min-w-0">
          {turn.userText ?? (turn.speaking ? <em className="text-muted-foreground">speaking…</em> : "—")}
          {turn.source && (
            <span className="ml-2 font-mono text-[10px] text-muted-foreground">
              {turn.source === "text" ? "typed" : "stt"}
            </span>
          )}
        </p>
      </div>

      {(turn.sentences.length > 0 || finished) && (
        <div className="mt-1 flex items-baseline gap-2">
          <span className="w-12 shrink-0 text-xs text-muted-foreground">arc</span>
          <div className="min-w-0 space-y-0.5">
            {shown.map((s) => {
              const style = SENTENCE_MARK[s.status];
              return (
                <p key={s.id} title={style.title} className={style.className}>
                  <span className="mr-1.5 inline-block w-3 font-mono">{style.mark}</span>
                  {s.text}
                </p>
              );
            })}
            {neverSynthesized > 0 && (
              <p className="text-xs text-muted-foreground">
                + {neverSynthesized} generated sentence{neverSynthesized > 1 ? "s" : ""} never
                synthesized
              </p>
            )}
          </div>
        </div>
      )}

      <div className="mt-1 pl-14 font-mono text-[11px] text-muted-foreground">
        {finished ? (
          <>
            <span className={OUTCOME_STYLE[finished.outcome]}>{finished.outcome}</span>
            {turn.interruptedIn && <> during {turn.interruptedIn}</>} · committed{" "}
            {finished.sentences_acked}/{finished.sentences_total} sentences
            {finished.timings.total_ms !== null && <> · first audio {finished.timings.total_ms} ms</>}
          </>
        ) : (
          <span>turn {turn.turnId} in progress</span>
        )}
      </div>
    </li>
  );
}

export function Conversation({
  view,
  canSend,
  onSend,
}: {
  view: SessionView;
  canSend: boolean;
  onSend: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const interruptible = ["THINKING", "TOOL_EXECUTION", "SPEAKING"].includes(view.state);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [view.turns]);

  return (
    <Panel
      title="Conversation"
      meta={<span className="font-mono">● played ○ unconfirmed ✕ not spoken</span>}
      className="h-full"
    >
      <div className="flex h-full flex-col">
        <div className="min-h-0 flex-1 overflow-auto text-sm">
          {view.turns.length === 0 ? (
            <Empty>No turns yet. Type what the user says below.</Empty>
          ) : (
            <ol>
              {view.turns.map((turn) => (
                <Turn key={turn.turnId} turn={turn} />
              ))}
            </ol>
          )}
          <div ref={bottomRef} />
        </div>
        <form
          className="mt-3 flex gap-2 border-t pt-3"
          onSubmit={(e) => {
            e.preventDefault();
            const text = draft.trim();
            if (!text) return;
            onSend(text);
            setDraft("");
          }}
        >
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={
              interruptible
                ? "Sending now interrupts the assistant (barge-in)"
                : "Type what the user says"
            }
            disabled={!canSend}
            aria-label="user utterance"
          />
          <Button type="submit" disabled={!canSend || !draft.trim()}>
            {interruptible ? "Interrupt" : "Send"}
          </Button>
        </form>
      </div>
    </Panel>
  );
}
