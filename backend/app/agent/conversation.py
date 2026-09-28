"""Conversation history. Contains only committed content: what the user said,
what tools actually did, and assistant text the client confirmed was played."""

from app.adapters.base import Message, ToolCallRequest
from app.tools.executor import ToolResult


class Conversation:
    def __init__(self, system_prompt: str | None = None) -> None:
        self._messages: list[Message] = []
        if system_prompt:
            self._messages.append(Message(role="system", content=system_prompt))

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def add_user(self, text: str) -> None:
        self._messages.append(Message(role="user", content=text))

    def add_assistant(self, spoken_text: str) -> None:
        if spoken_text:
            self._messages.append(Message(role="assistant", content=spoken_text))

    def add_tool_exchange(self, call: ToolCallRequest, result: ToolResult) -> None:
        # Committed when the tool finishes, even if the turn is later interrupted:
        # the runtime executed it, so history must say so.
        self._messages.append(Message(role="assistant", tool_calls=[call]))
        self._messages.append(
            Message(
                role="tool", content=result.for_llm(), tool_call_id=call.call_id, name=call.name
            )
        )
