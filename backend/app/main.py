"""ARC HTTP entrypoint. Run: `uv run uvicorn app.main:app --reload`."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import PlainTextResponse

from app.runtime import Runtime
from app.ws import serve_session

log = logging.getLogger(__name__)


async def _warm_up(runtime: Runtime) -> None:
    assert runtime.warm_up is not None
    started = time.monotonic()
    try:
        await runtime.warm_up()
        log.info("warm-up finished in %.1fs", time.monotonic() - started)
    except Exception:
        # Not fatal: the first turn pays the cost instead, and its trace shows it.
        log.exception("warm-up failed")


def create_app(runtime: Runtime | None = None) -> FastAPI:
    runtime = runtime or Runtime()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(_warm_up(runtime)) if runtime.warm_up else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="ARC runtime", lifespan=lifespan)
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
