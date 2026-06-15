"""Shared LLM types — kept separate from client.py to avoid circular imports."""
from typing import Any
from dataclasses import dataclass, field


class ToolCall:
    """Represents a tool call requested by the LLM."""
    def __init__(self, name: str, arguments: dict[str, Any], id: str = ""):
        self.name = name
        self.arguments = arguments
        self.id = id


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)
    model: str = ""
    raw: Any = None
