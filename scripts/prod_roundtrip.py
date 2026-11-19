"""Authenticated round trip against the live API (or OBSCATE_BASE_URL).

    python scripts/prod_roundtrip.py

Reads OBSCATE_API_KEY (and optionally OBSCATE_API_KEY_2, a key from a
different account) from .env. Uses synthetic test data only. Prints check
names and PASS/FAIL, never keys. About 5 API units per account.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
TEXT = "Refund card 4242 4242 4242 4242 for Priya Sharma, priya.sharma@example.com"
results: list[bool] = []


def check(name: str, ok: bool) -> None:
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def load_env() -> None:
    f = ROOT / ".env"
    if f.is_file():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    load_env()
    base = os.environ.get("OBSCATE_BASE_URL", "https://api.obscate.com").rstrip("/")
    k1, k2 = os.environ.get("OBSCATE_API_KEY"), os.environ.get("OBSCATE_API_KEY_2")
    if not k1:
        print("OBSCATE_API_KEY is empty in .env")
        return 2
    print("target:", base)
    c = httpx.Client(base_url=base, timeout=30)

    def post(key, path, body):
        r = c.post(path, json=body, headers={"x-api-key": key})
        if r.status_code >= 400:
            raise RuntimeError(f"{path}: HTTP {r.status_code} {r.json().get('detail')}")
        return r.json()

    # fpe, map-free
    red = post(k1, "/v1/redact", {"text": TEXT, "strategy": "fpe"})
    check("fpe redact hides card and email",
          "4242 4242 4242 4242" not in red["redacted"] and "priya.sharma@example.com" not in red["redacted"])
    check("fpe token_map is empty", red["token_map"] == {})
    kv = red.get("key_version")
    check(f"redact returns key_version ({kv!r})", kv is not None)
    back = post(k1, "/v1/restore-fpe", {"text": red["redacted"], **({"key_version": kv} if kv is not None else {})})
    check("fpe restore round-trips exactly", back["text"] == TEXT)

    # tokenize
    tok = post(k1, "/v1/redact", {"text": TEXT, "strategy": "tokenize"})
    back = post(k1, "/v1/restore", {"text": tok["redacted"], "token_map": tok["token_map"]})
    check("tokenize restore round-trips exactly", back["text"] == TEXT)

    # JSON, fpe
    payload = {"customer": {"name": "Priya Sharma", "email": "priya.sharma@example.com"}, "qty": 3}
    rj = post(k1, "/v1/redact-json", {"json": payload, "strategy": "fpe"})
    body = {"json": rj["redacted"], "fpe": True, "type_changes": rj["type_changes"]}
    if rj.get("key_version") is not None:
        body["key_version"] = rj["key_version"]
    check("redact-json / restore-json round-trips", post(k1, "/v1/restore-json", body)["json"] == payload)

    # Tenant isolation: account 2 must not recover account 1's plaintext
    if k2:
        other = post(k2, "/v1/restore-fpe", {"text": red["redacted"], **({"key_version": kv} if kv is not None else {})})
        check("account 2 cannot restore account 1's fpe tokens",
              "4242 4242 4242 4242" not in other["text"] and "priya.sharma@example.com" not in other["text"])
        h1 = post(k1, "/v1/redact", {"text": "priya.sharma@example.com", "strategy": "hash"})["redacted"]
        h2 = post(k2, "/v1/redact", {"text": "priya.sharma@example.com", "strategy": "hash"})["redacted"]
        check("hashes differ between accounts", h1 != h2)
    else:
        print("  SKIP  tenant isolation (OBSCATE_API_KEY_2 not set)")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
