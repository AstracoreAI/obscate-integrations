# obscate-mcp

An MCP server that gives any AI agent reversible PII redaction. Your agent
redacts personal data before a payload leaves (for another model, a tool or a
third-party API), then restores the real values in the reply.

It is a thin wrapper over the [Obscate](https://obscate.com) HTTP API.
Full docs: [obscate.com/docs/mcp](https://obscate.com/docs/mcp).
Detection runs in the API (hosted, or your own self-hosted instance). This
package only forwards calls.

<!-- mcp-name: com.obscate/mcp -->

| tool | what it does | API units |
|---|---|---|
| `redact` | redact a text; default `fpe` keeps formats and needs no map to restore | 1 per 100 KB |
| `restore` | put the original values back | free |
| `detect` | classify what PII a text contains (labels, offsets, counts) without changing it | 1 per 100 KB |
| `redact_json` | redact values inside a JSON object/array; keys and shape are unchanged | 1 per 100 KB |
| `restore_json` | restore any JSON reply | free |
| `list_entities` | supported labels and strategies | free, no key |

What to expect from detection: structured IDs (email, phone, cards, Aadhaar,
PAN, GSTIN, IBAN, SSN, IPs, AWS keys) are near-perfect because they are
regex- and checksum-based. Names and places come from an NER model and are
best-effort. Check the output if recall on those matters to you.
([Detectors and accuracy](https://obscate.com/docs/detectors),
[benchmark methodology](https://obscate.com/docs/benchmark).)

## Quickstart (about 3 minutes)

1. **Get a free API key** at [obscate.com](https://obscate.com) ([quickstart](https://obscate.com/docs/quickstart)). It is free
   for 1,000 requests a month at 60 a minute, with no card. The key is shown
   once.
2. **Install** with one of these:
   ```bash
   uvx obscate-mcp --version          # no install, runs from PyPI
   pip install obscate-mcp            # or install into an environment
   ```
3. **Add it to your MCP client** (below), with the key in the `env` block.
4. Ask your agent: *"Redact this before you send it to the CRM: Ravi Kumar,
   ravi@example.com, +91 98765 43210"*.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "obscate": {
      "command": "uvx",
      "args": ["obscate-mcp"],
      "env": { "OBSCATE_API_KEY": "ob_..." }
    }
  }
}
```

### Claude Code

```bash
claude mcp add obscate --env OBSCATE_API_KEY=ob_... -- uvx obscate-mcp
```

### Cursor

`~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per project). Use the
same JSON block as Claude Desktop.

### Configuration

| env var | default | |
|---|---|---|
| `OBSCATE_API_KEY` | none | required for every tool except `list_entities` |
| `OBSCATE_BASE_URL` | `https://api.obscate.com` | point at a self-hosted Obscate (the self-hosted edition is in pilot; ask contact@obscate.com) |

Do not commit config files that contain the key. If your client supports env
files or secret references, use them.

`obscate-mcp --transport streamable-http` serves over HTTP instead of stdio.

## Example agent flow

The agent has a customer record and must create a ticket in a third-party
helpdesk tool, which should never see the raw PII.

```text
user:   File a ticket: Ravi Kumar (ravi@example.com, +91 98765 43210) was double-charged on card 4242 4242 4242 4242.

agent → redact_json({"json": {"customer": "Ravi Kumar", "email": "ravi@example.com",
                              "phone": "+91 98765 43210", "issue": "double-charged on card 4242 4242 4242 4242"}})
      ← {"redacted": {"customer": "[PERSON:…]", "email": "<email-shaped value>", "phone": "<phone-shaped value>",
                      "issue": "double-charged on card <valid-looking card number>"},
         "type_changes": [], "keys_redacted": false, …}

agent → helpdesk.create_ticket(<the redacted object>)        # the tool only sees redacted values
      ← {"ticket": 812, "summary": "Refund for [PERSON:…], reply to <email-shaped value>"}

agent → restore_json({"json": <the tool's reply>, "type_changes": []})
      ← {"json": {"ticket": 812, "summary": "Refund for Ravi Kumar, reply to ravi@example.com"}}
```

With `strategy: "tokenize"` the redacted values look like `[EMAIL_1]`, and
you pass the returned `token_map` to `restore` / `restore_json`. With `fpe`
(the default) no map exists. Restore needs only your key.

**fpe caveat:** an email, phone number or ID that a model *invents*, which
was never redacted, is indistinguishable from an fpe token. `restore` will
decrypt it into a different value. Restore only text whose structured values
came from your own redaction. If that matters, use `tokenize`, which only
replaces tokens that appear in the map. See [strategies](https://obscate.com/docs/strategies).

## Privacy

- The server never logs, prints or stores the text, the detected values or
  the token map. Errors carry the HTTP status and the API's fixed message
  only. This is enforced by `tests/test_server.py::test_privacy_*`.
- `redact` and `detect` do not echo the original values back into the model's
  context (`detect` can with `include_values: true`).
- The Obscate API has the same contract on its side: it does not log or store
  payloads. The hosted API runs in DigitalOcean Bangalore. Traffic passes
  through DigitalOcean's Cloudflare edge. See
  [security & limits](https://obscate.com/docs/security).

## Develop

```bash
pip install -e '.[test]' && pytest -q      # unit tests against an in-process fake API
OBSCATE_API_KEY=ob_... python ../scripts/live_e2e.py mcp   # real end-to-end against the live API
```

License: MIT.
