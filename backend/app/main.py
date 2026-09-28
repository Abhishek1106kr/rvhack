"""ARC HTTP entrypoint. Run: `uv run uvicorn app.main:app --reload`."""

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import PlainTextResponse

from app.runtime import Runtime
from app.ws import serve_session


def create_app(runtime: Runtime | None = None) -> FastAPI:
    runtime = runtime or Runtime()
    app = FastAPI(title="ARC runtime")
    app.state.runtime = runtime

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/sessions")
    async def sessions() -> list[str]:
        return runtime.trace.sessions()

    @app.get("/sessions/{session_id}/trace", response_class=PlainTextResponse)
    async def trace(session_id: str) -> PlainTextResponse:
        if session_id not in runtime.trace.sessions():
            raise HTTPException(status_code=404, detail=f"no session {session_id!r}")
        return PlainTextResponse(
            runtime.trace.to_jsonl(session_id), media_type="application/x-ndjson"
        )

    @app.websocket("/ws/session")
    async def session(ws: WebSocket) -> None:
        await serve_session(ws, runtime)

    return app


app = create_app()
