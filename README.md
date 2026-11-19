# Obscate integrations

These packages add reversible PII redaction to an agent or LLM app in one
step. Each one is a thin wrapper over the [Obscate](https://obscate.com) HTTP
API. Detection runs in the API (hosted, or self-hosted, which is in pilot),
never in these packages. Docs: [obscate.com/docs](https://obscate.com/docs/).

| package | one step | README |
|---|---|---|
| `obscate-mcp` | `uvx obscate-mcp` in any MCP client (Claude Desktop, Claude Code, Cursor) | [obscate-mcp/](obscate-mcp/README.md) |
| `langchain-obscate` | `with_obscate(llm_or_chain)` · `create_agent(..., middleware=[ObscateMiddleware()])` | [langchain-obscate/](langchain-obscate/README.md) |
| `llama-index-postprocessor-obscate` | `ObscateQueryEngine.wrap(index.as_query_engine())` · `ObscatePostprocessor()` | [llama-index-postprocessor-obscate/](llama-index-postprocessor-obscate/README.md) |

All three need an `OBSCATE_API_KEY`. The free tier (1,000 requests a month,
60 a minute, no card) is enough to try them. Default strategy is `fpe`:
formats are preserved (valid-looking card numbers, email-shaped addresses),
values that can't keep their format fall back to bracketed `[LABEL:…]`
tokens, and restoring needs no token map.

**Honest expectations.** Structured IDs (email, phone, cards, Aadhaar, PAN,
GSTIN, IBAN, SSN, IP, AWS keys) are near-perfect because they use patterns
and checksums. Names and places use NER and are best-effort; the LlamaIndex
example shows a name being missed.

**Privacy.** No package logs, prints or stores raw text, detected values or
restore maps. Each package has a test that captures every logger at DEBUG,
plus stdout, stderr and the working directory, on success and failure paths.

## Layout

```
shared/_client.py        HTTP client, vendored into each package (scripts/sync_client.py)
shared/fake_api.py       in-process fake API for unit tests (toy detection, not the real engine)
scripts/live_e2e.py      real end-to-end against a running Obscate API (live or local)
obscate-mcp/             MCP server + server.json for the official MCP registry
langchain-obscate/       Runnable wrapper, agent middleware, tools, examples/quickstart.py
llama-index-postprocessor-obscate/   query engine wrapper, node postprocessor, examples/quickstart.py
LISTINGS.md              PyPI / MCP registry / awesome-list steps, prepared but NOT submitted
```

## Develop

```bash
uv venv --python 3.12 .venv
uv pip install -e ./obscate-mcp -e "./langchain-obscate[test]" -e ./llama-index-postprocessor-obscate pytest anyio
.venv/bin/python -m pytest -q tests && for d in obscate-mcp langchain-obscate llama-index-postprocessor-obscate; do (cd $d && ../.venv/bin/python -m pytest -q); done
cp .env.example .env    # add OBSCATE_API_KEY, then:
.venv/bin/python scripts/live_e2e.py
```

**Key versions.** The API returns `key_version` with every redaction (the
version of your account's key that made the tokens). `RestoreContext` carries
it, and every restore sends it back, so in-flight round trips survive a
server-side key rotation with no code from you. The MCP tools return it and
accept it, because the model carries state between tool calls. Only text you
*store* to restore later needs `key_version` stored next to it.

## Verification status (2026-11-19)

| check | status |
|---|---|
| Unit tests (fake API, no network): mcp 15, langchain 17, llamaindex 13, repo 2 | passing |
| `scripts/live_e2e.py` against the **real Obscate engine running locally** (obscate-api working tree, port 8765, throwaway local key): 23 checks covering MCP over a real stdio subprocess, LangChain Runnable and agent middleware, LlamaIndex query engine and postprocessor | 23/23 passing |
| Both `examples/quickstart.py` against the local engine with the stand-in LLM | run, output as shown in the READMEs |
| Wheels and sdists build; `twine check` passes; `uvx --from <wheel> obscate-mcp` runs in a clean environment | done |
| `server.json` validated against the MCP registry 2025-12-11 schema | done |
| `scripts/live_e2e.py` against the obscate-api `per-account-keys` branch (per-account keys, `key_version`) running locally on port 8766 | 23/23 passing |
| Same checks against the **live** https://api.obscate.com | **not yet run.** Needs a live key in `.env` |
| A **real LLM** (Anthropic) in the loop | **not yet run.** Needs `ANTHROPIC_API_KEY` (paid, under $0.01 per run) |
| Inside Claude Desktop / Cursor UI | not tested (the stdio protocol path is tested via the MCP SDK client) |
