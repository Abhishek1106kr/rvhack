"""Deterministic intent routing for the store assistant.

Clear, high-stakes questions (compatibility, returns, specs) get their tool calls decided
here, before the LLM runs. The model then only phrases an answer from real results; it
cannot skip the lookup or invent a policy. Anything not recognised is left to the LLM,
which still has every tool available.
"""

import re
from collections.abc import Sequence

from app.adapters.base import Message, ToolCallRequest
from app.agent.orchestrator import Planner
from problem.knowledge import Catalog
from problem.tools import DetailField

_ARTICLE = r"(?:(?:my|an|a|the|this|that)\s+)?"
_COMPATIBILITY = [
    re.compile(
        r"\b(?:works?|compatible|supports?|use it|fits?)\s+(?:\w+\s+)?(?:with|for|on)\s+"
        + _ARTICLE
        + r"(?P<device>.+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bcharges?\s+" + _ARTICLE + r"(?P<device>.+)", re.IGNORECASE),
]
_DEVICE_TAIL = re.compile(r"\b(?:phone|mobile|please)\b|[?.!,]", re.IGNORECASE)

_RETURNS = re.compile(
    r"\b(?:return|refund|replace|replacement|warranty|guarantee|faulty|defective|broken|"
    r"stops? working|customer care|helpline|contact)\b",
    re.IGNORECASE,
)

_DETAIL_KEYWORDS: list[tuple[re.Pattern[str], list[DetailField]]] = [
    (re.compile(r"\b(?:cable|wire|cord)\b", re.I), ["specs", "box_contents"]),
    (re.compile(r"\b(?:box|comes with|included|inside)\b", re.I), ["box_contents"]),
    (
        re.compile(
            r"\b(?:watts?|wattage|power|fast|speed|amps?|volts?|voltage|ports?|sockets?|plug|"
            r"size|dimensions?|weight|colou?r|certifi\w*|bis)\b",
            re.I,
        ),
        ["specs"],
    ),
    (re.compile(r"\b(?:features?|gan|safety|safe|overheat\w*)\b", re.I), ["key_features"]),
    (
        re.compile(r"\b(?:made|origin|country|manufactur\w*|brand)\b", re.I),
        ["country_of_origin", "manufacturer"],
    ),
]


def extract_device(utterance: str) -> str | None:
    for pattern in _COMPATIBILITY:
        if match := pattern.search(utterance):
            device = " ".join(_DEVICE_TAIL.sub(" ", match["device"]).split())
            return device or None
    return None


def build_planner(catalog: Catalog) -> Planner:
    def product_for(utterance: str) -> str | None:
        if len(catalog.products) == 1:
            return catalog.products[0].id
        matches = catalog.search(utterance, limit=1)
        return matches[0].id if matches else None

    def plan(messages: Sequence[Message]) -> list[ToolCallRequest]:
        utterance = messages[-1].content if messages and messages[-1].role == "user" else ""
        product_id = product_for(utterance)
        if not utterance or product_id is None:
            return []

        calls: list[ToolCallRequest] = []

        def add(name: str, arguments: dict) -> None:
            calls.append(
                ToolCallRequest(call_id=f"plan_{len(calls) + 1}", name=name, arguments=arguments)
            )

        if device := extract_device(utterance):
            add("check_compatibility", {"product_id": product_id, "device": device})
        if _RETURNS.search(utterance):
            add("get_return_policy", {"product_id": product_id})
        fields: list[DetailField] = []
        for pattern, wanted in _DETAIL_KEYWORDS:
            if pattern.search(utterance):
                fields += [f for f in wanted if f not in fields]
        if fields:
            add("get_product_details", {"product_id": product_id, "fields": fields})
        return calls

    return plan
