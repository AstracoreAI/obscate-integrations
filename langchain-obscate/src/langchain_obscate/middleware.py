"""Agent middleware for ``langchain.agents.create_agent``.

    from langchain.agents import create_agent
    from langchain_obscate import ObscateMiddleware

    agent = create_agent("anthropic:claude-haiku-5-5", tools=[...],
                         middleware=[ObscateMiddleware()])

Before every model call, the user's messages, tool results, earlier replies
and (by default) the system prompt are redacted in one Obscate request. The
model's reply, including tool-call arguments, is restored before it reaches
your tools and your user. So your tools get real values, and the LLM provider
only ever sees redacted ones.

Why middleware and not a callback: LangChain callbacks are observers. They
cannot change what is sent to the model. Middleware wraps the model call, so
it can rewrite it.

Fails closed: if Obscate is unreachable or returns an error, the model is not
called and ``ObscateError`` is raised.

Note: ``stream_mode="messages"`` shows raw model tokens, which are redacted.
The messages saved in agent state are restored.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from ._client import ObscateClient
from .redactor import ObscateRedactor

__all__ = ["ObscateMiddleware"]


class ObscateMiddleware(AgentMiddleware):
    def __init__(self, strategy: str = "fpe", labels: list[str] | None = None,
                 min_confidence: float = 0.0, api_key: str | None = None, base_url: str | None = None,
                 redact_system: bool = True, redactor: ObscateRedactor | None = None,
                 client: ObscateClient | None = None):
        super().__init__()
        self.redactor = redactor or ObscateRedactor(strategy, labels, min_confidence, api_key, base_url,
                                                    redact_system, client)

    @property
    def name(self) -> str:
        return "ObscateMiddleware"

    def _prepare(self, request: ModelRequest, red: list, ) -> ModelRequest:
        sys_msg = None
        if request.system_message is not None and self.redactor.redact_system:
            sys_msg, red = red[0], red[1:]
        overrides: dict[str, Any] = {"messages": red}
        if sys_msg is not None:
            overrides["system_message"] = sys_msg
        return request.override(**overrides)

    def _all(self, request: ModelRequest) -> list:
        msgs = list(request.messages)
        if request.system_message is not None and self.redactor.redact_system:
            msgs = [request.system_message] + msgs
        return msgs

    def wrap_model_call(self, request: ModelRequest,
                        handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        red, ctx = self.redactor.redact_messages(self._all(request))
        response = handler(self._prepare(request, red))
        response.result = [self.redactor.restore_message(m, ctx) for m in response.result]
        return response

    async def awrap_model_call(self, request: ModelRequest,
                               handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelResponse:
        red, ctx = await self.redactor.aredact_messages(self._all(request))
        response = await handler(self._prepare(request, red))
        response.result = [await self.redactor.arestore_message(m, ctx) for m in response.result]
        return response
