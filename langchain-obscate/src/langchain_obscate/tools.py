"""LangChain tools for agents that call Obscate explicitly, for example to
redact a record before handing it to another tool or API.

The LLM running the agent sees the tool's input, so these tools protect what
goes *downstream* of the agent. To keep PII away from the LLM itself, use
``ObscateMiddleware`` or ``ObscateRedaction``.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

from ._client import ObscateClient

__all__ = ["ObscateRedactTool", "ObscateDetectTool"]


class _RedactArgs(BaseModel):
    text: str = Field(description="Text to redact")


class _DetectArgs(BaseModel):
    text: str = Field(description="Text to classify for PII")


class ObscateRedactTool(BaseTool):
    """Redacts PII with the fpe strategy (formats kept; no token map needed to
    restore). Returns the redacted text only."""
    name: str = "obscate_redact"
    description: str = ("Redact personal data (emails, phones, cards, IDs, names, places) in a text "
                        "before sending it to another tool or API. Returns the redacted text.")
    args_schema: type[BaseModel] = _RedactArgs
    labels: Optional[list[str]] = None
    _client: ObscateClient = PrivateAttr()

    def __init__(self, client: ObscateClient | None = None, **kw: Any):
        super().__init__(**kw)
        self._client = client or ObscateClient(user_agent="langchain-obscate/0.1.1")

    def _run(self, text: str, run_manager: Any = None) -> str:
        r, _ = self._client.redact(text, "fpe", self.labels)
        return r["redacted"]

    async def _arun(self, text: str, run_manager: Any = None) -> str:
        r, _ = await self._client.aredact(text, "fpe", self.labels)
        return r["redacted"]


class ObscateDetectTool(BaseTool):
    """Classifies PII in a text. Returns labels and counts, never the values."""
    name: str = "obscate_detect"
    description: str = ("Check what kinds of personal data a text contains. Returns entity labels "
                        "and counts (not the values).")
    args_schema: type[BaseModel] = _DetectArgs
    _client: ObscateClient = PrivateAttr()

    def __init__(self, client: ObscateClient | None = None, **kw: Any):
        super().__init__(**kw)
        self._client = client or ObscateClient(user_agent="langchain-obscate/0.1.1")

    @staticmethod
    def _summ(r: dict) -> str:
        by: dict[str, int] = {}
        for e in r.get("entities", []):
            by[e["label"]] = by.get(e["label"], 0) + 1
        return json.dumps({"total": len(r.get("entities", [])), "by_label": by})

    def _run(self, text: str, run_manager: Any = None) -> str:
        return self._summ(self._client.detect(text))

    async def _arun(self, text: str, run_manager: Any = None) -> str:
        return self._summ(await self._client.adetect(text))
