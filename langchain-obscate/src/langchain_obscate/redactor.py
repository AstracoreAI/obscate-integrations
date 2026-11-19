"""Core: redact LangChain messages/values via the Obscate API and restore model output.

One model call costs ONE Obscate request (one unit per 100 KB): all message
text of the call goes in a single /v1/redact-json payload, so tokens are
consistent across messages. Restores are free.
"""
from __future__ import annotations

from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from ._client import REVERSIBLE, STRATEGIES, ObscateClient, RestoreContext

__all__ = ["ObscateRedactor"]


def _has_text(obj: Any) -> bool:
    if isinstance(obj, str):
        return bool(obj.strip())
    if isinstance(obj, dict):
        return any(_has_text(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_has_text(v) for v in obj)
    return False


def _content_parts(content: Any) -> Any:
    """Message content -> redactable parts. Text stays text; tool_use blocks
    contribute their input dict; images and other blocks are left alone (None)."""
    if isinstance(content, str):
        return content
    parts: list[Any] = []
    for b in content or []:
        if isinstance(b, str):
            parts.append(b)
        elif isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
            parts.append(b["text"])
        elif isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
            parts.append(b["input"])
        else:
            parts.append(None)
    return parts


def _apply_parts(content: Any, parts: Any) -> Any:
    if isinstance(content, str):
        return parts
    out = []
    for b, p in zip(content, parts):
        if isinstance(b, str):
            out.append(p)
        elif isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
            out.append({**b, "text": p})
        elif isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
            out.append({**b, "input": p})
        else:
            out.append(b)
    return out


def _entry(m: BaseMessage) -> dict:
    e: dict[str, Any] = {"t": _content_parts(m.content)}
    if isinstance(m, AIMessage) and m.tool_calls:
        e["a"] = [tc["args"] for tc in m.tool_calls]
    return e


def _rebuild(m: BaseMessage, e: dict) -> BaseMessage:
    update: dict[str, Any] = {"content": _apply_parts(m.content, e["t"])}
    if isinstance(m, AIMessage) and m.tool_calls:
        update["tool_calls"] = [{**tc, "args": a} for tc, a in zip(m.tool_calls, e["a"])]
        # provider-native copies of the tool calls (e.g. OpenAI's) would bypass the redaction
        if "tool_calls" in m.additional_kwargs or "function_call" in m.additional_kwargs:
            update["additional_kwargs"] = {k: v for k, v in m.additional_kwargs.items()
                                           if k not in ("tool_calls", "function_call")}
    return m.model_copy(update=update)


class ObscateRedactor:
    """Redacts what goes to the LLM and restores what comes back.

    strategy: ``fpe`` (default) keeps formats and needs no token map. Names,
    places and other values that cannot keep their format become bracketed,
    authenticated tokens such as ``[PERSON:…]``. That is the bracketed-token
    fallback. ``tokenize`` and ``synthetic`` keep the token map in memory for
    one call. ``mask``, ``hash`` and ``partial`` are one-way, so output is not
    restored.
    """

    def __init__(self, strategy: str = "fpe", labels: list[str] | None = None,
                 min_confidence: float = 0.0, api_key: str | None = None,
                 base_url: str | None = None, redact_system: bool = True,
                 client: ObscateClient | None = None):
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {list(STRATEGIES)}")
        self.strategy = strategy
        self.labels = labels
        self.min_confidence = min_confidence
        self.redact_system = redact_system
        self.client = client or ObscateClient(api_key=api_key, base_url=base_url,
                                              user_agent="langchain-obscate/0.1.0")

    def __repr__(self) -> str:
        return f"ObscateRedactor(strategy={self.strategy!r}, client={self.client!r})"

    def _empty(self) -> RestoreContext:
        return RestoreContext(self.strategy, labels=self.labels, min_confidence=self.min_confidence)

    def _rj_args(self) -> dict:
        return {"strategy": self.strategy, "labels": self.labels, "min_confidence": self.min_confidence}

    def _select(self, messages: Sequence[BaseMessage]) -> list[int]:
        return [i for i, m in enumerate(messages)
                if self.redact_system or not isinstance(m, SystemMessage)]

    # -- messages ----------------------------------------------------------------
    def redact_messages(self, messages: Sequence[BaseMessage]) -> tuple[list[BaseMessage], RestoreContext]:
        idx = self._select(messages)
        payload = [_entry(messages[i]) for i in idx]
        if not _has_text(payload):
            return list(messages), self._empty()
        r, ctx = self.client.redact_json(payload, **self._rj_args())
        out = list(messages)
        for i, e in zip(idx, r["redacted"]):
            out[i] = _rebuild(messages[i], e)
        return out, ctx

    async def aredact_messages(self, messages: Sequence[BaseMessage]) -> tuple[list[BaseMessage], RestoreContext]:
        idx = self._select(messages)
        payload = [_entry(messages[i]) for i in idx]
        if not _has_text(payload):
            return list(messages), self._empty()
        r, ctx = await self.client.aredact_json(payload, **self._rj_args())
        out = list(messages)
        for i, e in zip(idx, r["redacted"]):
            out[i] = _rebuild(messages[i], e)
        return out, ctx

    def restore_message(self, message: BaseMessage, ctx: RestoreContext) -> BaseMessage:
        if not ctx.reversible:
            return message
        e = _entry(message)
        if not _has_text(e):
            return message
        return _rebuild(message, self.client.restore_json(e, self._reply_ctx(ctx)))

    async def arestore_message(self, message: BaseMessage, ctx: RestoreContext) -> BaseMessage:
        if not ctx.reversible:
            return message
        e = _entry(message)
        if not _has_text(e):
            return message
        return _rebuild(message, await self.client.arestore_json(e, self._reply_ctx(ctx)))

    @staticmethod
    def _reply_ctx(ctx: RestoreContext) -> RestoreContext:
        # type_changes/keys_redacted describe the *input* payload's paths, not the reply's.
        return RestoreContext(ctx.strategy, ctx.token_map, [], False, ctx.labels,
                              ctx.min_confidence, ctx.hints, ctx.field_map, ctx.key_version)

    # -- plain values (str / dict / list) -----------------------------------------
    def redact_value(self, value: Any) -> tuple[Any, RestoreContext]:
        if not _has_text(value):
            return value, self._empty()
        if isinstance(value, str):
            r, ctx = self.client.redact(value, **self._rj_args())
            return r["redacted"], ctx
        r, ctx = self.client.redact_json(value, **self._rj_args())
        return r["redacted"], ctx

    async def aredact_value(self, value: Any) -> tuple[Any, RestoreContext]:
        if not _has_text(value):
            return value, self._empty()
        if isinstance(value, str):
            r, ctx = await self.client.aredact(value, **self._rj_args())
            return r["redacted"], ctx
        r, ctx = await self.client.aredact_json(value, **self._rj_args())
        return r["redacted"], ctx

    def restore_value(self, value: Any, ctx: RestoreContext) -> Any:
        if isinstance(value, BaseMessage):
            return self.restore_message(value, ctx)
        if not ctx.reversible or not _has_text(value):
            return value
        if isinstance(value, str):
            return self.client.restore(value, ctx)
        if isinstance(value, (dict, list)):
            return self.client.restore_json(value, self._reply_ctx(ctx))
        return value

    async def arestore_value(self, value: Any, ctx: RestoreContext) -> Any:
        if isinstance(value, BaseMessage):
            return await self.arestore_message(value, ctx)
        if not ctx.reversible or not _has_text(value):
            return value
        if isinstance(value, str):
            return await self.client.arestore(value, ctx)
        if isinstance(value, (dict, list)):
            return await self.client.arestore_json(value, self._reply_ctx(ctx))
        return value


REVERSIBLE_STRATEGIES = REVERSIBLE
