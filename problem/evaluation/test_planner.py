import pytest

from app.adapters.base import Message
from problem.knowledge import Catalog
from problem.workflows import build_planner, extract_device

CHARGER = "moto-68w-turbopower-charger"


def plan(utterance: str) -> list[tuple[str, dict]]:
    calls = build_planner(Catalog.load())([Message(role="user", content=utterance)])
    return [(c.name, c.arguments) for c in calls]


@pytest.mark.parametrize(
    ("utterance", "device"),
    [
        ("does this charger work with my edge 50 pro", "edge 50 pro"),
        ("will it charge a moto g eighty four?", "moto g eighty four"),
        ("does it work with the edge 50", "edge 50"),
        ("can I use it with an iphone 15", "iphone 15"),
        ("is it compatible with razr 40 phone", "razr 40"),
        ("how long is the cable", None),
        ("is this a good charger", None),
    ],
)
def test_extract_device(utterance: str, device: str | None) -> None:
    assert extract_device(utterance) == device


def test_compatibility_question_is_routed() -> None:
    assert plan("does this charger work with my edge 50 pro") == [
        ("check_compatibility", {"product_id": CHARGER, "device": "edge 50 pro"})
    ]


def test_returns_question_is_routed() -> None:
    assert plan("can I return it if it stops working") == [
        ("get_return_policy", {"product_id": CHARGER})
    ]


def test_cable_question_fetches_specs_and_box() -> None:
    assert plan("how long is the cable that comes with it") == [
        ("get_product_details", {"product_id": CHARGER, "fields": ["specs", "box_contents"]})
    ]


@pytest.mark.parametrize("utterance", ["how much does it cost", "hello", "thanks, bye"])
def test_unrecognised_questions_are_left_to_the_llm(utterance: str) -> None:
    assert plan(utterance) == []


def test_only_the_latest_user_message_is_planned() -> None:
    planner = build_planner(Catalog.load())
    history = [
        Message(role="user", content="can I return it"),
        Message(role="assistant", content="Yes, within 7 days."),
        Message(role="user", content="thanks"),
    ]
    assert planner(history) == []
