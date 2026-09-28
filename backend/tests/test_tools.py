import asyncio
import json

import pytest
from pydantic import BaseModel

from app.adapters.base import ToolCallRequest
from app.events import ToolCallFailed, ToolCallFinished, ToolCallStarted, ToolErrorKind
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolRisk, ToolSpec


class Query(BaseModel):
    city: str
    days: int = 1


class Forecast(BaseModel):
    city: str
    high_c: float


def spec(handler, *, name="forecast", timeout_s=0.1, risk=ToolRisk.READ_ONLY, output_model=None):
    return ToolSpec(
        name=name,
        description="Weather forecast for a city.",
        input_model=Query,
        handler=handler,
        timeout_s=timeout_s,
        risk=risk,
        output_model=output_model,
    )


async def ok_handler(args: Query) -> dict:
    return {"city": args.city, "high_c": 31.5}


def make_executor(*specs: ToolSpec):
    emitted = []

    def emit(event):
        emitted.append(event)
        return event

    return ToolExecutor(ToolRegistry(list(specs)), emit), emitted


def call(arguments: dict, name: str = "forecast") -> ToolCallRequest:
    return ToolCallRequest(call_id="c1", name=name, arguments=arguments)


async def run(executor: ToolExecutor, request: ToolCallRequest):
    return await executor.execute(request, session_id="s", turn_id="t")


def kinds(emitted) -> list[str]:
    return [e.event_type for e in emitted]


# ── registry ──────────────────────────────────────────────────────────────────────────────


def test_duplicate_registration_rejected() -> None:
    registry = ToolRegistry([spec(ok_handler)])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec(ok_handler))


@pytest.mark.parametrize("name", ["", "has space", "1starts_with_digit", "x" * 65])
def test_invalid_tool_name_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        spec(ok_handler, name=name)


def test_non_positive_timeout_rejected() -> None:
    with pytest.raises(ValueError):
        spec(ok_handler, timeout_s=0)


def test_schema_export_uses_input_model() -> None:
    (schema,) = ToolRegistry([spec(ok_handler)]).schemas()
    assert schema.name == "forecast"
    assert schema.parameters["required"] == ["city"]
    assert set(schema.parameters["properties"]) == {"city", "days"}


# ── executor ──────────────────────────────────────────────────────────────────────────────


async def test_success_emits_started_then_finished() -> None:
    executor, emitted = make_executor(spec(ok_handler, output_model=Forecast))
    result = await run(executor, call({"city": "Pune"}))
    assert result.ok and result.output == {"city": "Pune", "high_c": 31.5}
    assert kinds(emitted) == ["TOOL_CALL_STARTED", "TOOL_CALL_FINISHED"]
    started, finished = emitted
    assert isinstance(started, ToolCallStarted) and started.arguments == {"city": "Pune", "days": 1}
    assert isinstance(finished, ToolCallFinished) and finished.duration_ms >= 0


async def test_invalid_arguments_fail_without_starting() -> None:
    calls = []

    async def handler(args):
        calls.append(args)

    executor, emitted = make_executor(spec(handler))
    result = await run(executor, call({"days": "soon"}))
    assert not result.ok and result.error.kind is ToolErrorKind.INVALID_ARGUMENTS
    assert "city" in result.error.message and "days" in result.error.message
    assert kinds(emitted) == ["TOOL_CALL_FAILED"]
    assert emitted[0].duration_ms is None
    assert calls == []


async def test_unknown_tool_fails_without_starting() -> None:
    executor, emitted = make_executor(spec(ok_handler))
    result = await run(executor, call({}, name="delete_everything"))
    assert result.error.kind is ToolErrorKind.UNKNOWN_TOOL
    assert kinds(emitted) == ["TOOL_CALL_FAILED"]


async def test_timeout_read_only() -> None:
    async def slow(args):
        await asyncio.sleep(10)

    executor, emitted = make_executor(spec(slow, timeout_s=0.05))
    result = await run(executor, call({"city": "Pune"}))
    assert result.error.kind is ToolErrorKind.TIMEOUT
    assert kinds(emitted) == ["TOOL_CALL_STARTED", "TOOL_CALL_FAILED"]
    assert emitted[1].duration_ms >= 50


async def test_timeout_side_effect_reports_unknown_outcome() -> None:
    async def slow(args):
        await asyncio.sleep(10)

    executor, emitted = make_executor(spec(slow, timeout_s=0.05, risk=ToolRisk.SIDE_EFFECT))
    result = await run(executor, call({"city": "Pune"}))
    assert result.error.kind is ToolErrorKind.TIMEOUT_OUTCOME_UNKNOWN
    assert "may or may not" in result.error.message


async def test_handler_exception_is_a_failed_result() -> None:
    async def broken(args):
        raise ConnectionError("upstream down")

    executor, emitted = make_executor(spec(broken))
    result = await run(executor, call({"city": "Pune"}))
    assert result.error.kind is ToolErrorKind.EXCEPTION
    assert result.error.message == "ConnectionError: upstream down"
    assert isinstance(emitted[-1], ToolCallFailed)


async def test_output_not_matching_output_model_is_invalid_output() -> None:
    async def malformed(args):
        return {"city": args.city, "high_c": "hot"}

    executor, _ = make_executor(spec(malformed, output_model=Forecast))
    result = await run(executor, call({"city": "Pune"}))
    assert result.error.kind is ToolErrorKind.INVALID_OUTPUT


async def test_non_serializable_output_is_invalid_output() -> None:
    async def returns_object(args):
        return object()

    executor, _ = make_executor(spec(returns_object))
    result = await run(executor, call({"city": "Pune"}))
    assert result.error.kind is ToolErrorKind.INVALID_OUTPUT


@pytest.mark.parametrize("risk", list(ToolRisk))
async def test_failures_are_never_retried(risk: ToolRisk) -> None:
    attempts = 0

    async def flaky(args):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("flaky")

    executor, _ = make_executor(spec(flaky, risk=risk))
    await run(executor, call({"city": "Pune"}))
    assert attempts == 1


async def test_cancellation_is_reported_and_propagates() -> None:
    started = asyncio.Event()

    async def slow(args):
        started.set()
        await asyncio.sleep(10)

    executor, emitted = make_executor(spec(slow, timeout_s=5))
    task = asyncio.create_task(run(executor, call({"city": "Pune"})))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert emitted[-1].error_kind is ToolErrorKind.CANCELLED


async def test_result_for_llm_is_json() -> None:
    executor, _ = make_executor(spec(ok_handler))
    ok = await run(executor, call({"city": "Pune"}))
    bad = await run(executor, call({}))
    assert json.loads(ok.for_llm()) == {"ok": True, "output": {"city": "Pune", "high_c": 31.5}}
    assert json.loads(bad.for_llm())["error"] == "invalid_arguments"
