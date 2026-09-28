import type { StageTimings } from "@/lib/arc/protocol";
import { latestTimings, type SessionView } from "@/lib/arc/session-view";
import { cn } from "@/lib/utils";

import { Empty, Panel } from "./panel";

const STAGES: { key: keyof StageTimings; label: string }[] = [
  { key: "endpoint_ms", label: "VAD" },
  { key: "stt_ms", label: "STT" },
  { key: "llm_ms", label: "LLM" },
  { key: "tool_ms", label: "TOOL" },
  { key: "tts_ms", label: "TTS" },
  { key: "total_ms", label: "TOTAL" },
];

const RECENT = 5;

function ms(value: number | null) {
  return value === null ? "—" : value.toFixed(0);
}

export function LatencyPanel({ view }: { view: SessionView }) {
  const rows = latestTimings(view).slice(-RECENT).reverse();
  return (
    <Panel title="Latency (ms)" meta="server-side · excludes network & playback">
      {rows.length === 0 ? (
        <Empty>Shown when a turn finishes.</Empty>
      ) : (
        <table className="w-full font-mono text-xs">
          <thead>
            <tr className="text-muted-foreground">
              <th className="py-0.5 text-left font-normal">turn</th>
              {STAGES.map((s) => (
                <th key={s.key} className="py-0.5 text-right font-normal">
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ turnId, timings }, i) => (
              <tr key={turnId} className={cn(i > 0 && "text-muted-foreground")}>
                <td className="py-0.5">{turnId}</td>
                {STAGES.map((s) => (
                  <td
                    key={s.key}
                    className={cn("py-0.5 text-right", s.key === "total_ms" && "font-semibold")}
                  >
                    {ms(timings[s.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground">— = stage did not run (e.g. typed input skips VAD/STT)</p>
    </Panel>
  );
}

export function ToolsPanel({ view }: { view: SessionView }) {
  const calls = view.toolCalls.slice(-RECENT).reverse();
  return (
    <Panel
      title="Tools"
      meta={view.tools.length ? `${view.tools.length} registered` : "none registered"}
    >
      {view.tools.length > 0 && (
        <p className="mb-2 font-mono text-xs text-muted-foreground">{view.tools.join(" · ")}</p>
      )}
      {calls.length === 0 ? (
        <Empty>
          {view.tools.length
            ? "No tool calls yet."
            : "No tools registered. Tools come from problem/ and appear here when the LLM calls them."}
        </Empty>
      ) : (
        <ul className="space-y-2 font-mono text-xs">
          {calls.map((call) => {
            const outcome = call.outcome;
            return (
              <li key={`${call.callId}-${call.startedMono}`} className="rounded border p-2">
                <div className="flex justify-between gap-2">
                  <span className="font-semibold">{call.tool}</span>
                  {!outcome && <span className="text-violet-700">running</span>}
                  {outcome?.event_type === "TOOL_CALL_FINISHED" && (
                    <span className="text-emerald-700">ok · {outcome.duration_ms.toFixed(0)} ms</span>
                  )}
                  {outcome?.event_type === "TOOL_CALL_FAILED" && (
                    <span className="text-red-700">{outcome.error_kind}</span>
                  )}
                </div>
                {call.arguments && (
                  <pre className="mt-1 break-all whitespace-pre-wrap text-muted-foreground">
                    {JSON.stringify(call.arguments)}
                  </pre>
                )}
                {outcome?.event_type === "TOOL_CALL_FINISHED" && (
                  <pre className="mt-1 break-all whitespace-pre-wrap">
                    → {JSON.stringify(outcome.result)}
                  </pre>
                )}
                {outcome?.event_type === "TOOL_CALL_FAILED" && (
                  <p className="mt-1 text-red-700">{outcome.message}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

export function ErrorsPanel({ view }: { view: SessionView }) {
  const errors = view.errors.slice().reverse();
  return (
    <Panel title="Errors" meta={errors.length ? `${errors.length}` : undefined}>
      {errors.length === 0 ? (
        <Empty>None.</Empty>
      ) : (
        <ul className="space-y-1.5 font-mono text-xs">
          {errors.map((e) => (
            <li key={e.seq}>
              <span className={e.recoverable ? "text-orange-700" : "text-red-700"}>
                [{e.stage}] {e.recoverable ? "recovered" : "fatal"}
              </span>{" "}
              {e.message}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
