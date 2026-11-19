# Publishing and listings: prepared, NOT submitted

Nothing in this folder has been uploaded, published or submitted anywhere.
Every step below needs the owner (Skycast) to do or approve it.

## Decisions needed first

1. **Public GitHub repo** for this folder (e.g. `github.com/<org>/obscate-integrations`).
   The MCP registry and the awesome lists need a public source link. Replace
   `REPLACE_ME` in `obscate-mcp/server.json` and in the PR text below.
2. **License.** The packages are marked **MIT** (common for client SDKs and
   expected by the awesome lists). Change `LICENSE` and the three
   `pyproject.toml` files if you want a different one.
3. **PyPI account** that will own `obscate-mcp`, `langchain-obscate` and
   `llama-index-postprocessor-obscate`. PyPI is free. All three names were
   free on PyPI when checked on 2026-11-19 (see the bottom of this file).

## 1. PyPI (free) — DONE 2026-11-19: obscate-mcp, langchain-obscate, llama-index-postprocessor-obscate 0.1.0 uploaded by the owner; clean-install verified

```bash
cd obscate-integrations
for d in obscate-mcp langchain-obscate llama-index-postprocessor-obscate; do uv build --out-dir dist $d; done
uvx twine check dist/*          # already passes locally
uvx twine upload dist/*         # needs your PyPI API token, entered by you
```

Better: set up PyPI Trusted Publishing from the GitHub repo, so there is no
token at all.

After the upload, `uvx obscate-mcp` and `pip install obscate-mcp` work for
everyone.

## 2. Official MCP registry (free)

The server name is `com.obscate/mcp`. A domain namespace proves the server
belongs to obscate.com. It needs one DNS TXT record on obscate.com, which is
in your Cloudflare account. The PyPI package proves its link through the
`<!-- mcp-name: com.obscate/mcp -->` line already in `obscate-mcp/README.md`,
so publish to PyPI first.

```bash
# install the publisher: https://github.com/modelcontextprotocol/registry (mcp-publisher)
openssl genpkey -algorithm Ed25519 -out obscate-mcp-registry.pem      # keep it OUT of git
# The publisher prints the TXT value to add at obscate.com:  v=MCPv1; k=ed25519; p=<public key>
mcp-publisher login dns --domain obscate.com --private-key <hex private key>
cd obscate-mcp && mcp-publisher publish                               # reads server.json
```

`server.json` validates against the 2025-12-11 schema (checked locally on 2026-11-19).

## 3. awesome-mcp-servers PRs (third-party repos: you open them)

**punkpeye/awesome-mcp-servers**, section "🔒 Security" (keep the list's
alphabetical order). The line to add:

```markdown
- [REPLACE_ME/obscate-integrations](https://github.com/REPLACE_ME/obscate-integrations/tree/main/obscate-mcp) 🐍 ☁️ 🏠 - Reversible PII redaction for agents: redact text or JSON (emails, phones, cards, Aadhaar/PAN, names) before a tool call and restore the reply. Format-preserving (FF1) or token strategies; hosted API or self-hosted.
```

PR title: `Add Obscate (reversible PII redaction)`

PR body:

```markdown
Adds Obscate's MCP server, which lets an agent redact personal data before
sending a payload to another tool or model, then restore the real values in
the reply.

- Tools: redact, restore, detect, redact_json, restore_json, list_entities
- Python, official MCP SDK; `uvx obscate-mcp`
- Needs an API key (free tier: 1,000 requests/month). Can point at a self-hosted instance.
- Structured IDs are regex/checksum-based; names and places are NER, best-effort.
- MIT licensed.
```

**appcypher/awesome-mcp-servers**, section "Security" (or the closest
match). Use the same line without the emoji legend:

```markdown
- **[Obscate](https://github.com/REPLACE_ME/obscate-integrations/tree/main/obscate-mcp)** - Reversible PII redaction for agents: redact text/JSON before tool calls and restore replies (format-preserving or token strategies).
```

Each list has its own CONTRIBUTING rules (badges, ordering, a Glama or
MCP-registry link). Read them before opening the PR.

## 4. LangChain / LlamaIndex discoverability (optional, later)

- LangChain: once `langchain-obscate` is on PyPI, open a docs PR adding an
  integration page under `docs/integrations/` in the langchain docs repo.
  That is their process for partner packages.
- LlamaIndex: the integration is a standalone PyPI package, so no PR is
  needed. LlamaHub lists packages under the `llama-index-*` naming
  convention, which this package follows.

## What has been verified (and what has not)

See the root `README.md` → "Verification status".

## Name availability (checked 2026-11-19 via the PyPI/npm JSON APIs, HTTP 404 = unclaimed)

`obscate-mcp`, `langchain-obscate`, `llama-index-postprocessor-obscate` and plain `obscate` are
all unclaimed on PyPI. `obscate-mcp` is unclaimed on npm. Consider reserving `obscate` on
PyPI too, so nobody squats it.
