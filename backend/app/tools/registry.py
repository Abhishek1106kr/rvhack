"""Tool definitions and the registry the session is assembled with."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolRisk(StrEnum):
    READ_ONLY = "read_only"
    # Changes external state. Never retried, never cancelled mid-flight.
    SIDE_EFFECT = "side_effect"


_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Any], Awaitable[Any]]
    timeout_s: float
    risk: ToolRisk
    # When set, handler output is validated against it; otherwise it must be JSON-serializable.
    output_model: type[BaseModel] | None = None

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError(f"invalid tool name {self.name!r}")
        if self.timeout_s <= 0:
            raise ValueError(f"tool {self.name!r}: timeout_s must be positive")


class ToolSchema(BaseModel):
    """What the LLM is told about a tool."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return list(self._specs)

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name=spec.name,
                description=spec.description,
                parameters=spec.input_model.model_json_schema(),
            )
            for spec in self._specs.values()
        ]
