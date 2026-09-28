"""Live evaluation: the real LLM (Ollama) and TTS (Piper) behind the real VoiceSession.

Checks decisions the scripted scenarios cannot: does the model call the right tool,
does the tool result match, does the answer avoid invented facts, how long does it take.

    make eval-live                         # uses ARC_LLM_MODEL
    make eval-live ARC_LLM_MODEL=qwen2.5:1.5b
"""

import asyncio
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.adapters.base import Adapters
from app.adapters.fake import FakeClient
from app.agent.orchestrator import VoiceSession
from app.bus import EventBus
from app.events import AgentTurnFinished, ToolCallFailed, ToolCallFinished, ToolCallStarted
from app.trace import TraceStore
from problem.app import LLM_MODEL, llm, runtime_config, tools, tts, warm_up

TURN_LIMIT_S = 90


@dataclass
class Case:
    utterance: str
    tool: str | None
    check_result: Callable[[Any], bool] | None = None
    must_not_say: str | None = None  # regex; e.g. an invented price
    must_say: str | None = None


def status(expected: str) -> Callable[[Any], bool]:
    return lambda result: result.get("status") == expected


CASES = [
    Case("does this charger work with my edge 50 pro", "check_compatibility", status("listed")),
    Case("will it charge a moto g eighty four", "check_compatibility", status("listed")),
    Case(
        "does it work with the edge 50",
        "check_compatibility",
        status("ambiguous"),
        must_say=r"\bwhich\b",  # it has to ask, not pick one
    ),
    Case("can I use it with an iphone 15", "check_compatibility", status("not_listed")),
    Case("how long is the cable that comes with it", "get_product_details"),
    Case("can I return it if it stops working", "get_return_policy"),
    Case("how much does it cost", None, must_not_say=r"(₹|rs\.?|rupees?)\s*\d|\d+\s*(₹|rupees?)"),
]


async def run_case(case: Case) -> tuple[bool, str, dict]:
    bus, trace = EventBus(), TraceStore()
    bus.subscribe(trace)
    turn_done = asyncio.Event()
    bus.subscribe(lambda e: turn_done.set() if isinstance(e, AgentTurnFinished) else None)
    client = FakeClient(auto_play=True)
    session = VoiceSession(
        session_id="eval",
        bus=bus,
        adapters=Adapters(llm=llm, tts=tts),
        transport=client,
        tools=tools,
        config=runtime_config,
    )
    client.session = session
    await session.start()
    started = time.monotonic()
    await session.handle_text(case.utterance)
    async with asyncio.timeout(TURN_LIMIT_S):
        await turn_done.wait()
    elapsed = time.monotonic() - started
    await session.close()

    events = trace.events("eval")
    called = [e.tool for e in events if isinstance(e, ToolCallStarted | ToolCallFailed)]
    results = [e.result for e in events if isinstance(e, ToolCallFinished)]
    finished = next(e for e in events if isinstance(e, AgentTurnFinished))
    spoken = finished.spoken_text

    problems = []
    if case.tool and case.tool not in called:
        problems.append(f"expected {case.tool}, called {called or 'nothing'}")
    if case.check_result and not any(case.check_result(r) for r in results):
        problems.append(f"tool result check failed: {results}")
    if case.must_not_say and re.search(case.must_not_say, spoken, re.IGNORECASE):
        problems.append("answer contains forbidden content")
    if case.must_say and not re.search(case.must_say, spoken, re.IGNORECASE):
        problems.append(f"answer must match {case.must_say!r}")
    if not spoken:
        problems.append("nothing was spoken")
    metrics = {
        "answer_ms": finished.timings.total_ms,
        "first_audio_ms": finished.timings.first_audio_ms,
        "turn_s": round(elapsed, 1),
        "tools": ",".join(called) or "-",
    }
    return (
        not problems,
        spoken if not problems else "; ".join(problems) + f" | said: {spoken}",
        metrics,
    )


async def main() -> int:
    print(f"model: {LLM_MODEL}")
    await warm_up()
    passed = 0
    for case in CASES:
        ok, detail, m = await run_case(case)
        passed += ok
        mark = "PASS" if ok else "FAIL"
        print(
            f"{mark} first audio {m['first_audio_ms'] or '-':>7} ms · answer "
            f"{m['answer_ms'] or '-':>7} ms · turn {m['turn_s']:>4}s [{m['tools']}] "
            f"{case.utterance!r}\n       {detail}"
        )
    print(f"\n{passed}/{len(CASES)} passed")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
