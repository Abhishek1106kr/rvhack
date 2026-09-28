"""Deterministic scenarios for the product assistant.

Each scenario fixes the input and the LLM's decisions (scripted), then checks what the
runtime and the store tools actually did: state transitions, tool calls, tool results,
and what was committed as spoken. No model is needed; results never vary.
"""

from app.adapters.base import TextDelta, ToolCallRequest
from app.events import AgentTurnFinished, ToolCallFailed, ToolCallFinished, TurnOutcome
from app.session.state import SessionState as S
from problem.knowledge import Catalog
from problem.tools import build_tools

CHARGER = "moto-68w-turbopower-charger"


def compatibility(device: str, product_id: str = CHARGER) -> ToolCallRequest:
    return ToolCallRequest(
        call_id="c1",
        name="check_compatibility",
        arguments={"product_id": product_id, "device": device},
    )


async def ask(make_session, question: str, tool_call: ToolCallRequest, answer: str):
    h = await make_session(
        [[tool_call], [TextDelta(text=answer)]], tools=build_tools(Catalog.load())
    )
    await h.session.handle_text(question)
    turn_id = await h.until_sentence_sent(0, 1)
    await h.client.play(turn_id, 1)
    await h.until(lambda: bool(h.events(AgentTurnFinished)))
    await h.until_state(S.LISTENING)
    return h


async def test_listed_device(make_session) -> None:
    h = await ask(
        make_session,
        "does this charger work with my edge 50 pro",
        compatibility("Edge 50 Pro"),
        "Yes, the Edge 50 Pro is on the official list.",
    )
    (finished,) = h.events(ToolCallFinished)
    assert finished.result["status"] == "listed"
    assert finished.result["matches"] == ["Motorola Edge 50 Pro"]
    assert h.states() == [
        S.LISTENING,
        S.THINKING,
        S.TOOL_EXECUTION,
        S.THINKING,
        S.SPEAKING,
        S.LISTENING,
    ]


async def test_stt_misrecognized_entity_still_matches(make_session) -> None:
    # Whisper wrote the model number as words.
    h = await ask(
        make_session,
        "will it charge a moto g eighty four",
        compatibility("moto g eighty four"),
        "Yes, the Moto G84 is supported.",
    )
    assert h.events(ToolCallFinished)[0].result["matches"] == ["Moto G84 5G"]


async def test_ambiguous_device_gives_options_for_clarification(make_session) -> None:
    h = await ask(
        make_session,
        "does it work with the edge 50",
        compatibility("Edge 50"),
        "Which Edge 50 do you have: Pro, Ultra or Fusion?",
    )
    result = h.events(ToolCallFinished)[0].result
    assert result["status"] == "ambiguous"
    assert result["matches"] == ["Motorola Edge 50 Pro", "Edge 50 Ultra", "Edge 50 Fusion"]


async def test_unlisted_device(make_session) -> None:
    h = await ask(
        make_session,
        "can i use it with an iphone 15",
        compatibility("iPhone 15"),
        "The iPhone 15 is not on the official list.",
    )
    result = h.events(ToolCallFinished)[0].result
    assert result["status"] == "not_listed" and result["matches"] == []


async def test_wrong_product_id_fails_tool_not_session(make_session) -> None:
    h = await ask(
        make_session,
        "does the samsung charger work with my phone",
        compatibility("Edge 50 Pro", product_id="samsung-25w"),
        "I only have the Motorola 68 watt charger.",
    )
    (failed,) = h.events(ToolCallFailed)
    assert "no product 'samsung-25w'" in failed.message
    tool_message = h.llm.calls[1][-1]
    assert tool_message.role == "tool" and '"ok": false' in tool_message.content
    assert h.events(AgentTurnFinished)[0].outcome is TurnOutcome.COMPLETED


async def test_return_policy_answer_is_grounded(make_session) -> None:
    h = await ask(
        make_session,
        "can i return it if it stops working",
        ToolCallRequest(call_id="c1", name="get_return_policy", arguments={"product_id": CHARGER}),
        "You can get a replacement within 7 days if it is faulty.",
    )
    result = h.events(ToolCallFinished)[0].result
    assert result["return_policy"].startswith("Only replacement")
    assert result["customer_care"]["service_centre_email"] == "support@unigenlifestyle.com"


async def test_shopper_interrupts_long_answer(make_session) -> None:
    h = await make_session(
        [
            [
                ToolCallRequest(
                    call_id="c1",
                    name="get_product_details",
                    arguments={"product_id": CHARGER, "fields": ["key_features"]},
                )
            ],
            [TextDelta(text="It has 68 watt fast charging. It supports USB-C power delivery. ")],
            [TextDelta(text="The cable is 1 metre long.")],
        ],
        tools=build_tools(Catalog.load()),
    )
    await h.session.handle_text("tell me about the charger")
    turn_id = await h.until_sentence_sent(0, 2)
    await h.client.play(turn_id, 1)
    await h.session.handle_text("how long is the cable")  # barge-in before sentence 2 played
    await h.until(lambda: len(h.events(AgentTurnFinished)) == 1)

    interrupted = h.events(AgentTurnFinished)[0]
    assert interrupted.outcome is TurnOutcome.INTERRUPTED
    assert interrupted.spoken_text == "It has 68 watt fast charging."
    assert "power delivery" not in " ".join(m.content for m in h.session.conversation.messages)
