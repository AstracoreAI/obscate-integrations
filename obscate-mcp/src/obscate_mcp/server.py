"""Obscate MCP server: reversible PII redaction tools for AI agents.

Every tool is a thin call to the Obscate HTTP API (detection runs there, not
here). Configure with environment variables:

    OBSCATE_API_KEY    required for every tool except list_entities
    OBSCATE_BASE_URL   optional; default https://api.obscate.com (or your
                       self-hosted instance)

Privacy: this process never logs, prints or writes the text, the detected
values or the token map. Tool errors carry the HTTP status and the API's
fixed error message only.
"""
from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from ._client import ObscateClient, ObscateError, RestoreContext

Strategy = Literal["fpe", "tokenize", "synthetic", "mask", "hash", "partial"]
KeysMode = Literal["report", "strict", "redact"]

INSTRUCTIONS = """\
Obscate redacts personal data (emails, phone numbers, card numbers, Aadhaar,
PAN, names, places, ...) reversibly. Typical agent flow:
1. Before sending user data to another tool, model or third-party API, call
   `redact` (text) or `redact_json` (objects). Send the redacted output.
2. When the reply comes back, call `restore` / `restore_json` with the same
   `strategy` (and the `token_map` for tokenize/synthetic) to put the real
   values back before showing it to the user.
Default strategy `fpe` keeps formats (an email stays email-shaped) and needs no
token_map to restore. Structured IDs are detected near-perfectly; names and
places are best-effort, so review if recall on those matters.
"""

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                             openWorldHint=True)


def _fail(e: Exception) -> ToolError:
    # Safe by construction: ObscateError/ValueError messages never contain payload text.
    return ToolError(str(e))


def _public_entities(entities: list[dict]) -> list[dict]:
    """Labels, positions and replacements only. The caller already has the
    originals; we don't echo them back into the model context."""
    keep = ("label", "replacement", "path", "confidence")
    return [{k: e[k] for k in keep if k in e} for e in entities]


def build_server(client: ObscateClient | None = None) -> MCPServer:
    ob = client or ObscateClient(user_agent=f"obscate-mcp/{__version__}")
    mcp = MCPServer(name="obscate", title="Obscate PII redaction", version=__version__,
                    instructions=INSTRUCTIONS, website_url="https://obscate.com/docs/mcp")

    @mcp.tool(annotations=_READ_ONLY)
    async def redact(text: str, strategy: Strategy = "fpe", labels: list[str] | None = None,
                     min_confidence: float = 0.0) -> dict[str, Any]:
        """Redact PII in a text before it is sent anywhere else.

        strategy: `fpe` (default; format-preserving, restorable without a map;
        names/places become bracketed `[PERSON:...]` tokens), `tokenize`
        (`[EMAIL_1]`, needs the returned token_map to restore), `synthetic`
        (realistic fakes, needs token_map), or one-way `mask`/`hash`/`partial`.
        labels: optional filter such as ["EMAIL","PHONE"]; see list_entities.
        Costs 1 API unit per 100 KB.
        """
        try:
            r, _ = await ob.aredact(text, strategy, labels, min_confidence)
        except (ObscateError, ValueError) as e:
            raise _fail(e) from None
        out = {"redacted": r["redacted"], "strategy": strategy, "reversible": r.get("reversible"),
               "entities": _public_entities(r.get("entities", [])), "counts": r.get("counts", {})}
        if strategy in ("tokenize", "synthetic"):
            out["token_map"] = r.get("token_map", {})
            out["restore_with"] = "restore(text, strategy, token_map)"
        elif strategy == "fpe":
            out["restore_with"] = "restore(text, strategy='fpe') - no token_map needed"
        return out

    @mcp.tool(annotations=_READ_ONLY)
    async def restore(text: str, strategy: Literal["fpe", "tokenize", "synthetic"] = "fpe",
                      token_map: dict[str, str] | None = None, labels: list[str] | None = None,
                      min_confidence: float = 0.0) -> dict[str, Any]:
        """Put the original values back into text redacted by `redact`.

        Pass the same `strategy` (and `labels`/`min_confidence`) used to
        redact. tokenize/synthetic also need the `token_map` returned by
        `redact`. Free (no API units). Caveat for fpe: a new email/phone/ID
        that a model invented (never redacted) looks like an fpe token and will
        be decrypted into a different value; restore only text whose
        structured values came from your own redaction.
        """
        if strategy in ("tokenize", "synthetic") and token_map is None:
            raise ToolError(f"strategy {strategy!r} needs the token_map returned by redact")
        ctx = RestoreContext(strategy, dict(token_map or {}), labels=labels, min_confidence=min_confidence)
        try:
            return {"text": await ob.arestore(text, ctx)}
        except (ObscateError, ValueError) as e:
            raise _fail(e) from None

    @mcp.tool(annotations=_READ_ONLY)
    async def detect(text: str, labels: list[str] | None = None, min_confidence: float = 0.0,
                     include_values: bool = False) -> dict[str, Any]:
        """Classify what PII a text contains without changing it.

        Returns entity labels, character offsets and confidence, plus counts
        by label. The matched values are left out unless include_values=true
        (offsets let you find them in your own copy). Costs 1 API unit per 100 KB.
        """
        try:
            r = await ob.adetect(text, labels, min_confidence)
        except (ObscateError, ValueError) as e:
            raise _fail(e) from None
        ents = r.get("entities", [])
        by_label: dict[str, int] = {}
        for e in ents:
            by_label[e["label"]] = by_label.get(e["label"], 0) + 1
        keep = ("label", "start", "end", "confidence") + (("text",) if include_values else ())
        return {"entities": [{k: e[k] for k in keep if k in e} for e in ents],
                "counts": {"total": len(ents), "by_label": by_label}}

    @mcp.tool(annotations=_READ_ONLY)
    async def redact_json(json: Any, strategy: Strategy = "fpe", labels: list[str] | None = None,
                          min_confidence: float = 0.0, hints: bool = True,
                          field_map: dict[str, str | None] | None = None,
                          keys: KeysMode = "report") -> dict[str, Any]:
        """Redact PII inside a JSON object/array (e.g. a tool-call payload).

        Keys, nesting and non-sensitive values are unchanged so the receiving
        schema still works. hints: built-in field-name hints (name, email,
        phone, address, aadhaar, pan) on by default. field_map: per-path
        overrides, e.g. {"product.name": null, "sku": "IGNORE",
        "$.rows[*].label": "PERSON"}. keys: "report" (default, count PII in
        keys), "strict" (refuse if keys contain PII), "redact" (redact keys
        too). Keep `type_changes`, `keys_redacted` and (tokenize/synthetic)
        `token_map` for restore_json. Costs 1 API unit per 100 KB.
        """
        try:
            r, _ = await ob.aredact_json(json, strategy, labels, min_confidence, hints, field_map, keys)
        except (ObscateError, ValueError) as e:
            raise _fail(e) from None
        out = {"redacted": r["redacted"], "strategy": strategy,
               "type_changes": r.get("type_changes", []), "keys_redacted": r.get("keys_redacted", False),
               "pii_in_keys": r.get("pii_in_keys", 0),
               "entities": _public_entities(r.get("entities", [])), "counts": r.get("counts", {})}
        if strategy in ("tokenize", "synthetic"):
            out["token_map"] = r.get("token_map", {})
        return out

    @mcp.tool(annotations=_READ_ONLY)
    async def restore_json(json: Any, strategy: Literal["fpe", "tokenize", "synthetic"] = "fpe",
                           token_map: dict[str, str] | None = None,
                           type_changes: list[dict] | None = None, keys_redacted: bool = False,
                           hints: bool = True, field_map: dict[str, str | None] | None = None,
                           labels: list[str] | None = None,
                           min_confidence: float = 0.0) -> dict[str, Any]:
        """Restore a JSON value (any shape, e.g. a model's reply) redacted by
        `redact_json`. Pass back the same strategy, field_map and hints, plus
        `type_changes`, `keys_redacted` and (tokenize/synthetic) `token_map`
        from the redact_json result. Free (no API units)."""
        if strategy in ("tokenize", "synthetic") and token_map is None:
            raise ToolError(f"strategy {strategy!r} needs the token_map returned by redact_json")
        ctx = RestoreContext(strategy, dict(token_map or {}), list(type_changes or []), keys_redacted,
                             labels, min_confidence, hints, field_map)
        try:
            return {"json": await ob.arestore_json(json, ctx)}
        except (ObscateError, ValueError) as e:
            raise _fail(e) from None

    @mcp.tool(annotations=_READ_ONLY)
    def list_entities() -> dict[str, Any]:
        """List the entity labels and strategies the Obscate API supports. Free, no key needed."""
        try:
            return ob.entities()
        except ObscateError as e:
            raise _fail(e) from None

    return mcp


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(prog="obscate-mcp", description="Obscate PII redaction MCP server")
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p.add_argument("--version", action="version", version=f"obscate-mcp {__version__}")
    args = p.parse_args()
    build_server().run(transport=args.transport)
