"""Minimal client for the Obscate HTTP API, shared by the Obscate integrations.

This file is vendored byte-for-byte into each package (obscate-mcp,
langchain-obscate, llama-index-postprocessor-obscate) so every package installs
on its own. Edit ``shared/_client.py`` and run ``python scripts/sync_client.py``;
a test fails if the copies drift.

Privacy contract (tested in every package):
  * Nothing here logs, prints or writes to disk the text you send, the values
    detected, or the restore map.
  * ``ObscateError`` carries the HTTP status and the server's ``detail`` only.
    The API never echoes request bodies in errors, so neither do we.
  * ``RestoreContext`` keeps the token map in memory only and hides it from
    ``repr()`` so it does not end up in tracebacks or debug logs by accident.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

DEFAULT_BASE_URL = "https://api.obscate.com"
STRATEGIES = ("fpe", "tokenize", "synthetic", "mask", "hash", "partial")
REVERSIBLE = ("fpe", "tokenize", "synthetic")
SIGNUP_URL = "https://obscate.com"

__all__ = ["ObscateClient", "ObscateError", "RestoreContext", "DEFAULT_BASE_URL",
           "STRATEGIES", "REVERSIBLE"]


class ObscateError(RuntimeError):
    """An Obscate API call failed. Holds the status code and the server's
    ``detail`` message — never any of the text you sent."""

    def __init__(self, message: str, status: int | None = None, request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.request_id = request_id


@dataclass
class RestoreContext:
    """What is needed to undo one redaction. In-memory only; never logged.

    For ``fpe`` the context holds no secrets at all (restore needs only your
    API key). For ``tokenize``/``synthetic`` it holds the token map, which
    contains the original values — keep it in memory and let it go when done.
    """
    strategy: str
    token_map: dict[str, str] = field(default_factory=dict, repr=False)
    type_changes: list[dict] = field(default_factory=list, repr=False)
    keys_redacted: bool = False
    labels: list[str] | None = None
    min_confidence: float = 0.0
    hints: bool = True
    field_map: dict[str, str | None] | None = field(default=None, repr=False)

    @property
    def reversible(self) -> bool:
        return self.strategy in REVERSIBLE

    def merge(self, other: "RestoreContext") -> "RestoreContext":
        """Combine two contexts from the same strategy (token maps are unioned)."""
        if other.strategy != self.strategy:
            raise ValueError("cannot merge restore contexts from different strategies")
        merged = dict(self.token_map)
        merged.update(other.token_map)
        return RestoreContext(self.strategy, merged, [], False, self.labels,
                              self.min_confidence, self.hints, self.field_map)


def _check_strategy(strategy: str) -> None:
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {list(STRATEGIES)}")


class ObscateClient:
    """Thin synchronous + asynchronous wrapper over the Obscate REST API.

    ``api_key`` defaults to ``$OBSCATE_API_KEY``; ``base_url`` defaults to
    ``$OBSCATE_BASE_URL`` or https://api.obscate.com (point it at a
    self-hosted instance if you run one).
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 timeout: float = 30.0, user_agent: str = "obscate-python-integration",
                 transport: httpx.BaseTransport | None = None,
                 async_transport: httpx.AsyncBaseTransport | None = None):
        self._api_key = api_key if api_key is not None else os.environ.get("OBSCATE_API_KEY", "")
        self.base_url = (base_url or os.environ.get("OBSCATE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._headers = {"user-agent": user_agent, "accept": "application/json"}
        self._transport = transport
        self._async_transport = async_transport
        self._http: httpx.Client | None = None
        self._ahttp: httpx.AsyncClient | None = None

    def __repr__(self) -> str:  # never show the key
        return f"ObscateClient(base_url={self.base_url!r}, api_key={'set' if self._api_key else 'missing'})"

    # -- plumbing ---------------------------------------------------------------
    def _auth_headers(self) -> dict[str, str]:
        if not self._api_key:
            raise ObscateError(
                "OBSCATE_API_KEY is not set. Get a free key (1,000 requests/month) at "
                f"{SIGNUP_URL} and export OBSCATE_API_KEY, or pass api_key=...")
        return {**self._headers, "x-api-key": self._api_key}

    @staticmethod
    def _handle(resp: httpx.Response) -> Any:
        if resp.status_code < 400:
            return resp.json()
        try:
            detail = resp.json().get("detail")
        except Exception:
            detail = None
        if not isinstance(detail, str):
            # 422 validation errors are a list of {loc,msg,type}; keep field paths + messages only.
            if isinstance(detail, list):
                detail = "; ".join(f"{'.'.join(str(p) for p in d.get('loc', []))}: {d.get('msg')}"
                                   for d in detail if isinstance(d, dict))
            else:
                detail = resp.reason_phrase
        hint = {401: " (check OBSCATE_API_KEY)",
                402: " (monthly quota reached; see https://obscate.com/pricing)",
                429: f" (retry after {resp.headers.get('retry-after', '?')}s)",
                503: " (detection failed closed: nothing was redacted)"}.get(resp.status_code, "")
        raise ObscateError(f"Obscate API error {resp.status_code}: {detail}{hint}",
                           status=resp.status_code, request_id=resp.headers.get("x-request-id"))

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(base_url=self.base_url, timeout=self.timeout,
                                      transport=self._transport)
        return self._http

    def _aclient(self) -> httpx.AsyncClient:
        if self._ahttp is None:
            self._ahttp = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout,
                                            transport=self._async_transport)
        return self._ahttp

    def _post(self, path: str, body: dict) -> Any:
        try:
            resp = self._client().post(path, json=body, headers=self._auth_headers())
        except httpx.HTTPError as e:  # network errors carry the URL, not the body
            raise ObscateError(f"could not reach Obscate at {self.base_url}: {type(e).__name__}") from None
        return self._handle(resp)

    async def _apost(self, path: str, body: dict) -> Any:
        try:
            resp = await self._aclient().post(path, json=body, headers=self._auth_headers())
        except httpx.HTTPError as e:
            raise ObscateError(f"could not reach Obscate at {self.base_url}: {type(e).__name__}") from None
        return self._handle(resp)

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    async def aclose(self) -> None:
        if self._ahttp is not None:
            await self._ahttp.aclose()
            self._ahttp = None

    # -- request bodies (shared by sync + async) ----------------------------------
    @staticmethod
    def _redact_body(text, strategy, labels, min_confidence):
        _check_strategy(strategy)
        return {"text": text, "strategy": strategy, "labels": labels, "min_confidence": min_confidence}

    @staticmethod
    def _redact_json_body(payload, strategy, labels, min_confidence, hints, field_map, keys):
        _check_strategy(strategy)
        if keys not in ("report", "strict", "redact"):
            raise ValueError("keys must be 'report', 'strict' or 'redact'")
        return {"json": payload, "strategy": strategy, "labels": labels,
                "min_confidence": min_confidence, "hints": hints, "field_map": field_map, "keys": keys}

    @staticmethod
    def _restore_route(text: str, ctx: RestoreContext) -> tuple[str, dict]:
        if ctx.strategy == "fpe":
            return "/v1/restore-fpe", {"text": text, "labels": ctx.labels,
                                       "min_confidence": ctx.min_confidence}
        if ctx.strategy in ("tokenize", "synthetic"):
            return "/v1/restore", {"text": text, "token_map": ctx.token_map}
        raise ValueError(f"strategy {ctx.strategy!r} is one-way and cannot be restored; "
                         f"use one of {list(REVERSIBLE)}")

    @staticmethod
    def _restore_json_body(payload: Any, ctx: RestoreContext) -> dict:
        if not ctx.reversible:
            raise ValueError(f"strategy {ctx.strategy!r} is one-way and cannot be restored")
        return {"json": payload, "token_map": ctx.token_map, "fpe": ctx.strategy == "fpe",
                "type_changes": ctx.type_changes or None, "keys_redacted": ctx.keys_redacted,
                "labels": ctx.labels, "min_confidence": ctx.min_confidence,
                "hints": ctx.hints, "field_map": ctx.field_map}

    @staticmethod
    def _ctx(r: dict, strategy, labels, min_confidence, hints=True, field_map=None) -> RestoreContext:
        return RestoreContext(strategy, dict(r.get("token_map") or {}), list(r.get("type_changes") or []),
                              bool(r.get("keys_redacted")), labels, min_confidence, hints, field_map)

    # -- sync API ---------------------------------------------------------------
    def redact(self, text: str, strategy: str = "fpe", labels: list[str] | None = None,
               min_confidence: float = 0.0) -> tuple[dict, RestoreContext]:
        """POST /v1/redact. Returns the raw response and a context for restore()."""
        r = self._post("/v1/redact", self._redact_body(text, strategy, labels, min_confidence))
        return r, self._ctx(r, strategy, labels, min_confidence)

    def restore(self, text: str, ctx: RestoreContext) -> str:
        """POST /v1/restore (token map) or /v1/restore-fpe (map-free). Free."""
        path, body = self._restore_route(text, ctx)
        return self._post(path, body)["text"]

    def detect(self, text: str, labels: list[str] | None = None, min_confidence: float = 0.0) -> dict:
        """POST /v1/detect. Metered like redact."""
        return self._post("/v1/detect", {"text": text, "labels": labels, "min_confidence": min_confidence})

    def redact_json(self, payload: Any, strategy: str = "fpe", labels: list[str] | None = None,
                    min_confidence: float = 0.0, hints: bool = True,
                    field_map: dict[str, str | None] | None = None,
                    keys: str = "report") -> tuple[dict, RestoreContext]:
        """POST /v1/redact-json. One request (one unit up to 100 KB) for the whole payload."""
        r = self._post("/v1/redact-json", self._redact_json_body(payload, strategy, labels, min_confidence,
                                                                  hints, field_map, keys))
        return r, self._ctx(r, strategy, labels, min_confidence, hints, field_map)

    def restore_json(self, payload: Any, ctx: RestoreContext) -> Any:
        """POST /v1/restore-json. Free."""
        return self._post("/v1/restore-json", self._restore_json_body(payload, ctx))["json"]

    def redact_texts(self, texts: Iterable[str], strategy: str = "fpe", labels: list[str] | None = None,
                     min_confidence: float = 0.0) -> tuple[list[str], RestoreContext]:
        """Redact several strings in ONE request with consistent tokens across them.

        Uses /v1/redact-json on a JSON array, so ``[PERSON_1]`` means the same
        person in every string and the whole batch costs one unit (≤ 100 KB).
        """
        texts = list(texts)
        if not texts:
            return [], RestoreContext(strategy, labels=labels, min_confidence=min_confidence)
        r, ctx = self.redact_json(texts, strategy, labels, min_confidence, hints=False)
        return list(r["redacted"]), ctx

    def entities(self) -> dict:
        """GET /v1/entities (public, no key needed)."""
        try:
            resp = self._client().get("/v1/entities", headers=self._headers)
        except httpx.HTTPError as e:
            raise ObscateError(f"could not reach Obscate at {self.base_url}: {type(e).__name__}") from None
        return self._handle(resp)

    # -- async API --------------------------------------------------------------
    async def aredact(self, text: str, strategy: str = "fpe", labels: list[str] | None = None,
                      min_confidence: float = 0.0) -> tuple[dict, RestoreContext]:
        r = await self._apost("/v1/redact", self._redact_body(text, strategy, labels, min_confidence))
        return r, self._ctx(r, strategy, labels, min_confidence)

    async def arestore(self, text: str, ctx: RestoreContext) -> str:
        path, body = self._restore_route(text, ctx)
        return (await self._apost(path, body))["text"]

    async def adetect(self, text: str, labels: list[str] | None = None, min_confidence: float = 0.0) -> dict:
        return await self._apost("/v1/detect", {"text": text, "labels": labels, "min_confidence": min_confidence})

    async def aredact_json(self, payload: Any, strategy: str = "fpe", labels: list[str] | None = None,
                           min_confidence: float = 0.0, hints: bool = True,
                           field_map: dict[str, str | None] | None = None,
                           keys: str = "report") -> tuple[dict, RestoreContext]:
        r = await self._apost("/v1/redact-json", self._redact_json_body(payload, strategy, labels,
                                                                        min_confidence, hints, field_map, keys))
        return r, self._ctx(r, strategy, labels, min_confidence, hints, field_map)

    async def arestore_json(self, payload: Any, ctx: RestoreContext) -> Any:
        return (await self._apost("/v1/restore-json", self._restore_json_body(payload, ctx)))["json"]

    async def aredact_texts(self, texts: Iterable[str], strategy: str = "fpe",
                            labels: list[str] | None = None,
                            min_confidence: float = 0.0) -> tuple[list[str], RestoreContext]:
        texts = list(texts)
        if not texts:
            return [], RestoreContext(strategy, labels=labels, min_confidence=min_confidence)
        r, ctx = await self.aredact_json(texts, strategy, labels, min_confidence, hints=False)
        return list(r["redacted"]), ctx
