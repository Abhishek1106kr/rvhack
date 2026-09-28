"use client";

import { useEffect, useRef } from "react";

import type { ArcEvent } from "@/lib/arc/protocol";
import type { SessionView } from "@/lib/arc/session-view";
import { cn } from "@/lib/utils";

import { Empty, Panel } from "./panel";
import { STATE_STYLE } from "./state-colors";

function summary(e: ArcEvent): string {
  switch (e.event_type) {
    case "SESSION_STARTED":
      return `llm=${e.adapters.llm} tts=${e.adapters.tts}`;
    case "SESSION_STATE_CHANGED":
      return `${e.previous} → ${e.current} (${e.reason})`;
    case "USER_SPEECH_ENDED":
      return `${e.duration_ms.toFixed(0)} ms speech`;
    case "TRANSCRIPT_PARTIAL":
    case "TRANSCRIPT_FINAL":
      return `"${e.text}"`;
    case "AGENT_TURN_STARTED":
      return `"${e.user_text}"`;
    case "AGENT_TURN_FINISHED":
      return `${e.outcome} · ${e.sentences_acked}/${e.sentences_total} committed`;
    case "TOOL_CALL_STARTED":
      return `${e.tool}(${JSON.stringify(e.arguments)})`;
    case "TOOL_CALL_FINISHED":
      return `${e.tool} ok ${e.duration_ms.toFixed(0)} ms`;
    case "TOOL_CALL_FAILED":
      return `${e.tool} ${e.error_kind}: ${e.message}`;
    case "TTS_STARTED":
      return `#${e.sentence_id} "${e.text}"`;
    case "TTS_STOPPED":
      return `#${e.sentence_id} ${e.reason}`;
    case "PLAYBACK_ACKED":
      return `#${e.sentence_id}${e.accepted ? "" : " (ignored: turn already closed)"}`;
    case "USER_BARGE_IN":
      return `during ${e.interrupted_state}; cancelled ${e.cancelled.join(", ") || "nothing"}`;
    case "ERROR":
      return `[${e.stage}] ${e.message}`;
    default:
      return "";
  }
}

function typeClass(e: ArcEvent): string {
  if (e.event_type === "ERROR") return "text-red-700";
  if (e.event_type === "USER_BARGE_IN") return "text-orange-700";
  if (e.event_type === "TOOL_CALL_FAILED") return "text-red-700";
  if (e.event_type.startsWith("TOOL_")) return "text-violet-700";
  return "";
}

export function Timeline({ view }: { view: SessionView }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const start = view.startedMono;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Follow new events only when already at the bottom, so reading history isn't disrupted.
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [view.events]);

  return (
    <Panel title="Event timeline" meta={`${view.events.length} events`} className="h-full">
      <div ref={scrollRef} className="h-full overflow-auto">
        {view.events.length === 0 ? (
          <Empty>Waiting for SESSION_STARTED.</Empty>
        ) : (
          <table className="w-full font-mono text-[11px] leading-tight">
            <tbody>
              {view.events.map((e) => (
                <tr key={e.seq} className="align-top">
                  <td className="pr-2 text-right text-muted-foreground tabular-nums">{e.seq}</td>
                  <td className="pr-2 text-right text-muted-foreground tabular-nums">
                    {start === null ? "" : `+${((e.mono - start) * 1000).toFixed(0)}`}
                  </td>
                  <td className={cn("pr-2 whitespace-nowrap", typeClass(e))}>
                    {e.event_type === "SESSION_STATE_CHANGED" && (
                      <span
                        className={cn(
                          "mr-1 inline-block size-1.5 rounded-full align-middle",
                          STATE_STYLE[e.current].dot,
                        )}
                      />
                    )}
                    {e.event_type}
                  </td>
                  <td className="break-all text-muted-foreground">{summary(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}
