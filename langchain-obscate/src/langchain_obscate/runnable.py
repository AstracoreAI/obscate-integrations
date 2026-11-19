"""Wrap any LangChain Runnable (chat model, LLM, chain) so PII is redacted on
the way in and restored on the way out.

    safe_llm = ObscateRedaction(ChatAnthropic(model="claude-haiku-5-5"))
    safe_llm.invoke("Email ravi@example.com about the refund")

Input handling:
  * str, list of messages, PromptValue: message text (and tool-call args) is redacted
  * dict (prompt variables for a chain): every string value is redacted
Output handling: AIMessage, str, dict and list are restored. Other types pass through.

Streaming is buffered: ``stream()`` yields one restored chunk at the end,
because a token can be split across streamed chunks and cannot be restored
until it is complete.
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, convert_to_messages
from langchain_core.prompt_values import ChatPromptValue, PromptValue
from langchain_core.runnables import Runnable, RunnableConfig

from ._client import ObscateClient
from .redactor import ObscateRedactor

__all__ = ["ObscateRedaction", "with_obscate"]


def _is_message_list(x: Any) -> bool:
    return isinstance(x, list) and bool(x) and all(
        isinstance(m, (BaseMessage, tuple, dict)) for m in x)


class ObscateRedaction(Runnable):
    """``ObscateRedaction(runnable, strategy="fpe", ...)``: a Runnable that
    redacts its input with Obscate, calls ``runnable``, and restores the output."""

    def __init__(self, runnable: Runnable, strategy: str = "fpe", labels: list[str] | None = None,
                 min_confidence: float = 0.0, api_key: str | None = None, base_url: str | None = None,
                 redact_system: bool = True, redactor: ObscateRedactor | None = None,
                 client: ObscateClient | None = None):
        self.runnable = runnable
        self.redactor = redactor or ObscateRedactor(strategy, labels, min_confidence, api_key, base_url,
                                                    redact_system, client)

    def __repr__(self) -> str:
        return f"ObscateRedaction({self.runnable!r}, strategy={self.redactor.strategy!r})"

    @property
    def InputType(self) -> Any:  # noqa: N802 - LangChain API
        return self.runnable.InputType

    @property
    def OutputType(self) -> Any:  # noqa: N802
        return self.runnable.OutputType

    def _split(self, input: Any):
        if isinstance(input, PromptValue):
            return "messages", input.to_messages()
        if _is_message_list(input):
            return "messages", convert_to_messages(input)
        return "value", input

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        kind, val = self._split(input)
        if kind == "messages":
            red, ctx = self.redactor.redact_messages(val)
            red_in: Any = ChatPromptValue(messages=red) if isinstance(input, PromptValue) else red
        else:
            red_in, ctx = self.redactor.redact_value(val)
        out = self.runnable.invoke(red_in, config, **kwargs)
        return self.redactor.restore_value(out, ctx)

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        kind, val = self._split(input)
        if kind == "messages":
            red, ctx = await self.redactor.aredact_messages(val)
            red_in: Any = ChatPromptValue(messages=red) if isinstance(input, PromptValue) else red
        else:
            red_in, ctx = await self.redactor.aredact_value(val)
        out = await self.runnable.ainvoke(red_in, config, **kwargs)
        return await self.redactor.arestore_value(out, ctx)


def with_obscate(runnable: Runnable, **kwargs: Any) -> ObscateRedaction:
    """Shorthand: ``chain = with_obscate(prompt | llm | StrOutputParser())``."""
    return ObscateRedaction(runnable, **kwargs)
