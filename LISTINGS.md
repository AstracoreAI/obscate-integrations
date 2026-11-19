# Publishing and listings — status (updated 2026-11-19)

- GitHub: PUBLIC at https://github.com/AstracoreAI/obscate-integrations (main only; MIT; obscate-api stays private).
- PyPI: langchain-obscate and llama-index-postprocessor-obscate 0.1.0; obscate-mcp 0.1.1 (README: self-hosted "in pilot"),
  uploaded by the owner 2026-11-19.
  Releases from now on: Trusted Publishing (OIDC) via .github/workflows/release.yml — no PyPI token anywhere.
  Push a tag `<package>-v<version>` matching pyproject (e.g. `obscate-mcp-v0.1.2`); the workflow tests, builds,
  `twine check`s and publishes. PyPI trusted publishers added for all three projects (owner AstracoreAI, repo
  obscate-integrations, workflow release.yml, environment pypi). RELEASED 2026-11-19 via the workflow, with PyPI
  attestations: obscate-mcp 0.1.2, langchain-obscate 0.1.1, llama-index-postprocessor-obscate 0.1.1 (README-only:
  partial-value guidance from the 1,800-reply test); clean `uvx` install of each verified.
- MCP registry: PUBLISHED com.obscate/mcp 0.1.1 (2026-11-19), DNS-verified via the apex TXT record on obscate.com.
  Signing key: ~/.config/obscate/mcp-registry-ed25519.pem (0600, outside git). To publish a new version: bump
  server.json + pyproject, upload to PyPI, then `mcp-publisher login dns --domain obscate.com --private-key <hex>`
  and `mcp-publisher publish` from obscate-mcp/. Keep the TXT record in place.
- punkpeye/awesome-mcp-servers: PR opened — https://github.com/punkpeye/awesome-mcp-servers/pull/20028
- LangChain docs listing: issue filed https://github.com/langchain-ai/docs/issues/7899 (form fields; labels need a
  maintainer — asked in a comment). Component: middleware (`ObscateMiddleware`), docs https://obscate.com/docs/langchain.
- Glama: `glama.json` committed (maintainers: AstracoreAI). TODO (owner, in browser): run the Claim flow on
  glama.ai for this repo; re-run it after any change to glama.json.
- Cline MCP marketplace: READY, NOT SUBMITTED. Logo: assets/obscate-logo-400.png; install guide: llms-install.md.
  The form requires confirming "I have tested that Cline can successfully set up this server using only the
  README.md and/or llms-install.md file" — do that test first (VS Code + Cline: ask Cline to install from this
  repo URL, give it a free key, call list_entities and a redact/restore), then file
  https://github.com/cline/mcp-marketplace/issues/new?template=mcp-server-submission.yml with the repo URL and
  https://raw.githubusercontent.com/AstracoreAI/obscate-integrations/main/assets/obscate-logo-400.png
- LlamaIndex: no documented listing path for external packages (main repo no longer accepts integrations;
  LlamaHub redirects to a docs page with no submit route). Ask maintainers on Discord / GitHub discussions.
- PulseMCP: submissions paused; says it will ingest the official registry when it reopens. Nothing to do.
- mcp.so: only a paid $39 listing was verifiable — skipped (needs owner approval to spend).
- Smithery: needs an MCPB bundle; process unverified — later, if worth it.
- appcypher/awesome-mcp-servers: ARCHIVED (read-only since 2026-05); no PR possible.

---

# Original plan (kept for reference)

Nothing in this folder has been uploaded, published or submitted anywhere.
Every step below needs the owner (Skycast) to do or approve it.

## Decisions needed first

1. **Public GitHub repo** for this folder (e.g. `github.com/<org>/obscate-integrations`).
   The MCP registry and the awesome lists need a public source link. Replace
   `AstracoreAI` in `obscate-mcp/server.json` and in the PR text below.
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
- [AstracoreAI/obscate-integrations](https://github.com/AstracoreAI/obscate-integrations/tree/main/obscate-mcp) 🐍 ☁️ - Reversible PII redaction for agents: redact text or JSON (emails, phones, cards, Aadhaar/PAN, names) before a tool call and restore the reply. Format-preserving (FF1) or token strategies; hosted API (self-hosted edition in pilot).
```

PR title: `Add Obscate (reversible PII redaction)`

PR body:

```markdown
Adds Obscate's MCP server, which lets an agent redact personal data before
sending a payload to another tool or model, then restore the real values in
the reply.

- Tools: redact, restore, detect, redact_json, restore_json, list_entities
- Python, official MCP SDK; `uvx obscate-mcp`
- Needs an API key (free tier: 1,000 requests/month). A self-hosted edition is in pilot (not generally available).
- Structured IDs are regex/checksum-based; names and places are NER, best-effort.
- MIT licensed.
```

**appcypher/awesome-mcp-servers**, section "Security" (or the closest
match). Use the same line without the emoji legend:

```markdown
- **[Obscate](https://github.com/AstracoreAI/obscate-integrations/tree/main/obscate-mcp)** - Reversible PII redaction for agents: redact text/JSON before tool calls and restore replies (format-preserving or token strategies).
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
