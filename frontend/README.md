# frontend/

Next.js control room: a live view of one ARC session — state machine, conversation with
per-sentence playback status, latency, tools, errors, event timeline.

The browser talks only to Next. `/api/*` (including the `/api/ws/session` WebSocket) is
proxied to the ARC backend (`ARC_BACKEND_URL`, default `http://127.0.0.1:8000`).

| Path | Role |
|---|---|
| `lib/arc/protocol.ts` | wire types, mirrors `backend/app/events.py` |
| `lib/arc/player.ts` | Web Audio playback; ACKs a sentence only after its audio ended |
| `lib/arc/session-view.ts` | folds events into view state |
| `lib/arc/use-arc-session.ts` | socket lifecycle, reconnect, flush handling |
| `components/control-room/` | panels |

Run from the repo root: `make dev` (backend + frontend) or `make dev-frontend`.
