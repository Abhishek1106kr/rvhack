"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { SentencePlayer } from "./player";
import type { ClientMessage, ServerMessage } from "./protocol";
import { applyEvent, emptyView, type SessionView } from "./session-view";

export type Connection = "connecting" | "open" | "closed";

type Action = { type: "event"; message: ServerMessage & { type: "event" } } | { type: "reset" };

function reducer(view: SessionView, action: Action): SessionView {
  if (action.type === "reset") return emptyView;
  return applyEvent(view, action.message.event);
}

const RETRY_MS = 2000;

function sessionUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  // Next rewrites /api/* to the backend, WebSocket upgrades included.
  return `${scheme}://${window.location.host}/api/ws/session`;
}

export function useArcSession() {
  const [view, dispatch] = useReducer(reducer, emptyView);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [audioUnlocked, setAudioUnlocked] = useState(false);
  const [playing, setPlaying] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const playerRef = useRef<SentencePlayer | null>(null);

  const send = useCallback((message: ClientMessage) => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  }, []);

  useEffect(() => {
    playerRef.current = new SentencePlayer((turnId, sentenceId) =>
      send({ type: "ack", turn_id: turnId, sentence_id: sentenceId }),
    );
  }, [send]);

  useEffect(() => {
    let disposed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      dispatch({ type: "reset" });
      setConnection("connecting");
      const socket = new WebSocket(sessionUrl());
      socketRef.current = socket;

      socket.onopen = () => setConnection("open");
      socket.onmessage = (ev: MessageEvent<string>) => {
        const message = JSON.parse(ev.data) as ServerMessage;
        const player = playerRef.current;
        if (message.type === "event") {
          dispatch({ type: "event", message });
        } else if (message.type === "audio") {
          player?.enqueue(message);
          setPlaying(player?.playing ?? false);
        } else {
          const last = player?.flush(message.turn_id) ?? null;
          setPlaying(false);
          send({ type: "flushed", turn_id: message.turn_id, last_acked_sentence_id: last });
        }
      };
      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null;
        if (disposed) return;
        setConnection("closed");
        retry = setTimeout(connect, RETRY_MS);
      };
    };

    connect();
    return () => {
      disposed = true;
      clearTimeout(retry);
      socketRef.current?.close();
    };
  }, [send]);

  // Playback state changes without a message arriving (audio simply ends).
  useEffect(() => {
    const id = setInterval(() => setPlaying(playerRef.current?.playing ?? false), 150);
    return () => clearInterval(id);
  }, []);

  const unlockAudio = useCallback(async () => {
    await playerRef.current?.unlock();
    setAudioUnlocked(playerRef.current?.unlocked ?? false);
  }, []);

  const sendText = useCallback(
    async (text: string) => {
      await unlockAudio(); // typing is a user gesture: use it to enable playback
      send({ type: "text", text });
    },
    [send, unlockAudio],
  );

  return { view, connection, audioUnlocked, playing, unlockAudio, sendText };
}
