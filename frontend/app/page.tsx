"use client";

import { Conversation } from "@/components/control-room/conversation";
import { ErrorsPanel, LatencyPanel, ToolsPanel } from "@/components/control-room/side-panels";
import { StatusBar } from "@/components/control-room/status-bar";
import { Timeline } from "@/components/control-room/timeline";
import { useArcSession } from "@/lib/arc/use-arc-session";

export default function ControlRoom() {
  const session = useArcSession();
  const { view, connection } = session;

  return (
    <div className="flex h-dvh flex-col">
      <StatusBar
        view={view}
        connection={connection}
        audioUnlocked={session.audioUnlocked}
        playing={session.playing}
        micState={session.micState}
        micLevel={session.micLevel}
        onUnlockAudio={session.unlockAudio}
        onStartMic={() => void session.startMic()}
        onStopMic={() => void session.stopMic()}
      />
      <main className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="min-h-[24rem]">
          <Conversation
            view={view}
            canSend={connection === "open"}
            onSend={(text) => void session.sendText(text)}
          />
        </div>
        <div className="flex min-h-0 flex-col gap-3">
          <LatencyPanel view={view} />
          <ToolsPanel view={view} />
          <ErrorsPanel view={view} />
        </div>
        <div className="min-h-[24rem]">
          <Timeline view={view} />
        </div>
      </main>
    </div>
  );
}
