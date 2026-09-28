import type { SessionState } from "@/lib/arc/protocol";

// One color per session state, used everywhere a state is shown.
export const STATE_STYLE: Record<SessionState, { dot: string; active: string }> = {
  IDLE: { dot: "bg-neutral-400", active: "bg-neutral-200 text-neutral-900 border-neutral-400" },
  LISTENING: { dot: "bg-emerald-500", active: "bg-emerald-100 text-emerald-900 border-emerald-500" },
  THINKING: { dot: "bg-amber-500", active: "bg-amber-100 text-amber-900 border-amber-500" },
  TOOL_EXECUTION: { dot: "bg-violet-500", active: "bg-violet-100 text-violet-900 border-violet-500" },
  SPEAKING: { dot: "bg-sky-500", active: "bg-sky-100 text-sky-900 border-sky-500" },
  INTERRUPTED: { dot: "bg-orange-500", active: "bg-orange-100 text-orange-900 border-orange-500" },
  ERROR: { dot: "bg-red-500", active: "bg-red-100 text-red-900 border-red-500" },
};
