"""In-process stand-in for the Obscate API, for unit tests only.

Vendored into each package's tests/ by scripts/sync_client.py. It is NOT the
real detector: it only knows emails, Indian-style +91 phone numbers and a
fixed list of names, which is enough to exercise the integrations' plumbing
(request shapes, restore paths, error handling, privacy). Real detection
quality is tested in obscate-api and in the live end-to-end runs.

fpe here is a toy reversible encoding ("[EMAIL:hex]"), not FF1. It does model
key versions: tokens embed the version that made them, redact responses
return ``key_version``, and restoring under any other version yields a wrong
value (as real FF1 would), so tests can prove the version is carried through.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

NAMES = ("Ravi Kumar", "Priya Sharma")
_PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("PHONE", re.compile(r"\+91[ -]?\d{5}[ -]?\d{5}")),
    ("PERSON", re.compile("|".join(re.escape(n) for n in NAMES))),
]
_FPE_TOKEN = re.compile(r"\[(EMAIL|PHONE|PERSON):([0-9a-f]+)\]")


def _spans(text: str):
    out = []
    for label, pat in _PATTERNS:
        out += [(m.start(), m.end(), label, m.group(0)) for m in pat.finditer(text)]
    return sorted(out)


class FakeObscate:
    def __init__(self, api_key: str = "ob_test_key", fail_with: int | None = None,
                 key_version: int = 1):
        self.api_key = api_key
        self.fail_with = fail_with
        self.key_version = key_version            # bump it to simulate a key rotation
        self.calls: list[tuple[str, Any]] = []   # (path, parsed body) — test-side only

    # -- engine -----------------------------------------------------------------
    def _redact(self, text: str, strategy: str, st: dict) -> tuple[str, list[dict]]:
        out, cur, ents = [], 0, []
        for s, e, label, val in _spans(text):
            if strategy == "fpe":
                rep = f"[{label}:{f'{self.key_version}|{val}'.encode().hex()}]"
            elif strategy in ("tokenize", "synthetic"):
                if val not in st["seen"]:
                    st["n"][label] = st["n"].get(label, 0) + 1
                    st["seen"][val] = f"[{label}_{st['n'][label]}]"
                rep = st["seen"][val]
                st["map"][rep] = val
            elif strategy == "mask":
                rep = "*" * len(val)
            else:
                rep = f"<{label}>"
            out += [text[cur:s], rep]
            cur = e
            ents.append({"label": label, "text": val, "replacement": rep, "start": s, "end": e,
                         "confidence": 0.99})
        out.append(text[cur:])
        return "".join(out), ents

    def _restore_fpe(self, text: str, version: int | None = None) -> str:
        v = str(self.key_version if version is None else version)
        def dec(m):
            made, _, val = bytes.fromhex(m.group(2)).decode().partition("|")
            return val if made == v else "WRONG-KEY-VERSION"
        return _FPE_TOKEN.sub(dec, text)

    @staticmethod
    def _restore_map(text: str, token_map: dict) -> str:
        # Toy left-to-right scan; tokens in this fake never overlap.
        out, i = [], 0
        while i < len(text):
            for tok, val in token_map.items():
                if text.startswith(tok, i):
                    out.append(val)
                    i += len(tok)
                    break
            else:
                out.append(text[i])
                i += 1
        return "".join(out)

    def _walk(self, obj, fn):
        if isinstance(obj, str):
            return fn(obj)
        if isinstance(obj, list):
            return [self._walk(v, fn) for v in obj]
        if isinstance(obj, dict):
            return {k: self._walk(v, fn) for k, v in obj.items()}
        return obj

    # -- http -------------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/v1/entities":
            return httpx.Response(200, json={"regex": ["EMAIL", "PHONE"], "ner": ["PERSON"],
                                             "strategies": ["tokenize", "mask", "hash", "partial",
                                                            "synthetic", "fpe"]})
        body = json.loads(request.content or b"{}")
        self.calls.append((path, body))
        if request.headers.get("x-api-key") != self.api_key:
            return httpx.Response(401, json={"detail": "invalid or missing x-api-key"})
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"detail": "simulated failure"},
                                  headers={"retry-after": "7", "x-request-id": "rid123"})
        st = {"n": {}, "seen": {}, "map": {}}
        if path == "/v1/redact":
            red, ents = self._redact(body["text"], body["strategy"], st)
            return httpx.Response(200, json={"redacted": red, "strategy": body["strategy"],
                                             "reversible": body["strategy"] in ("fpe", "tokenize", "synthetic"),
                                             "token_map": st["map"], "entities": ents,
                                             "counts": {"total": len(ents)},
                                             "key_version": self.key_version})
        if path == "/v1/detect":
            ents = [{"label": l, "text": v, "start": s, "end": e, "confidence": 0.99}
                    for s, e, l, v in _spans(body["text"])]
            return httpx.Response(200, json={"entities": ents, "counts": {"total": len(ents)}})
        if path == "/v1/restore":
            return httpx.Response(200, json={"text": self._restore_map(body["text"], body["token_map"])})
        if path == "/v1/restore-fpe":
            return httpx.Response(200, json={"text": self._restore_fpe(body["text"],
                                                                       body.get("key_version"))})
        if path == "/v1/redact-json":
            ents: list = []

            def fn(s):
                r, e = self._redact(s, body["strategy"], st)
                ents.extend(e)
                return r
            red = self._walk(body["json"], fn)
            return httpx.Response(200, json={"redacted": red, "strategy": body["strategy"],
                                             "token_map": st["map"], "entities": ents,
                                             "type_changes": [], "pii_in_keys": 0, "keys_redacted": False,
                                             "counts": {"total": len(ents)},
                                             "key_version": self.key_version})
        if path == "/v1/restore-json":
            if body.get("fpe"):
                out = self._walk(body["json"], lambda s: self._restore_fpe(s, body.get("key_version")))
            else:
                out = self._walk(body["json"], lambda s: self._restore_map(s, body["token_map"]))
            return httpx.Response(200, json={"json": out})
        return httpx.Response(404, json={"detail": "Not Found"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def sent_text(self) -> str:
        """Everything the integration sent to the API, as one string (for assertions)."""
        return json.dumps([b for _, b in self.calls])
