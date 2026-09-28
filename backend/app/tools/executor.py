"""Runs tool calls requested by the LLM. The runtime, not the model, decides what happens."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from app.adapters.base import ToolCallRequest
from app.events import Event, ToolCallFailed, ToolCallFinished, ToolCallStarted, ToolErrorKind
from app.tools.registry import ToolRegistry, ToolRisk, ToolSpec

Emit = Callable[[Event], Event]


class ToolError(BaseModel):
    kind: ToolErrorKind
    message: str


class ToolResult(BaseModel):
    call_id: str
    tool: str
    ok: bool
    output: Any = None
    error: ToolError | None = None

    def for_llm(self) -> str:
        """The tool message content the LLM sees on its next round."""
        if self.ok:
            return json.dumps({"ok": True, "output": self.output})
        assert self.error is not None
        return json.dumps({"ok": False, "error": self.error.kind, "message": self.error.message})


def _summarize(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
    )


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, emit: Emit) -> None:
        self._registry = registry
        self._emit = emit

    async def execute(
        self, call: ToolCallRequest, *, session_id: str, turn_id: str | None
    ) -> ToolResult:
        """validate → TOOL_CALL_STARTED → run with timeout → FINISHED / FAILED.

        Tool problems never raise; they come back as a failed ToolResult.
        Only asyncio.CancelledError propagates (after TOOL_CALL_FAILED is emitted).
        No retries here: retrying is a decision the orchestrator would have to make visibly.
        """

        def fail(kind: ToolErrorKind, message: str, duration_ms: float | None) -> ToolResult:
            self._emit(
                ToolCallFailed(
                    session_id=session_id,
                    turn_id=turn_id,
                    call_id=call.call_id,
                    tool=call.name,
                    error_kind=kind,
                    message=message,
                    duration_ms=duration_ms,
                )
            )
            return ToolResult(
                call_id=call.call_id,
                tool=call.name,
                ok=False,
                error=ToolError(kind=kind, message=message),
            )

        spec = self._registry.get(call.name)
        if spec is None:
            return fail(ToolErrorKind.UNKNOWN_TOOL, f"no tool named {call.name!r}", None)

        try:
            arguments = spec.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            return fail(ToolErrorKind.INVALID_ARGUMENTS, _summarize(exc), None)

        self._emit(
            ToolCallStarted(
                session_id=session_id,
                turn_id=turn_id,
                call_id=call.call_id,
                tool=call.name,
                arguments=arguments.model_dump(mode="json"),
            )
        )
        started = time.monotonic()

        def elapsed_ms() -> float:
            return round((time.monotonic() - started) * 1000, 1)

        try:
            raw = await asyncio.wait_for(spec.handler(arguments), timeout=spec.timeout_s)
        except TimeoutError:
            if spec.risk is ToolRisk.SIDE_EFFECT:
                return fail(
                    ToolErrorKind.TIMEOUT_OUTCOME_UNKNOWN,
                    f"no result within {spec.timeout_s}s; the action may or may not have happened",
                    elapsed_ms(),
                )
            return fail(ToolErrorKind.TIMEOUT, f"no result within {spec.timeout_s}s", elapsed_ms())
        except asyncio.CancelledError:
            fail(ToolErrorKind.CANCELLED, "cancelled by the runtime", elapsed_ms())
            raise
        except Exception as exc:
            return fail(ToolErrorKind.EXCEPTION, f"{type(exc).__name__}: {exc}", elapsed_ms())

        try:
            output = _serialize(spec, raw)
        except Exception as exc:
            return fail(ToolErrorKind.INVALID_OUTPUT, f"{type(exc).__name__}: {exc}", elapsed_ms())

        duration_ms = elapsed_ms()
        self._emit(
            ToolCallFinished(
                session_id=session_id,
                turn_id=turn_id,
                call_id=call.call_id,
                tool=call.name,
                result=output,
                duration_ms=duration_ms,
            )
        )
        return ToolResult(call_id=call.call_id, tool=call.name, ok=True, output=output)


def _serialize(spec: ToolSpec, raw: Any) -> Any:
    if spec.output_model is not None:
        return spec.output_model.model_validate(raw).model_dump(mode="json")
    return to_jsonable_python(raw)
