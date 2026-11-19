# Installing the Obscate MCP server (for AI agents such as Cline)

The Obscate MCP server is the Python package `obscate-mcp` on PyPI. It runs
locally over stdio and calls the hosted Obscate API (https://api.obscate.com)
to redact sensitive data (cards, Aadhaar/PAN, emails, phones, names) before a
tool call and restore it in the reply.

## 1. Prerequisite: uv

The server is launched with `uvx`. Check that it exists:

```bash
uvx --version
```

If it is missing, ask the user to install uv: https://docs.astral.sh/uv/getting-started/installation/

## 2. API key — ask the user for it

Every tool except `list_entities` needs an Obscate API key (it starts with
`ob_`). **Ask the user for their key.** Never invent one, and never print it back.

If the user does not have one yet, they can create a free key (1,000 requests a
month, no card) with their own email address:

```bash
curl -s https://api.obscate.com/v1/signup -H 'content-type: application/json' \
  -d '{"email":"<the user's email>"}'
```

The key is in the `api_key` field and is shown only once; the user should save it.

## 3. Add the server to the MCP settings

Add this entry to the MCP settings file (for Cline: `cline_mcp_settings.json`),
with the user's key in place of `ob_...`:

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

Optional: `OBSCATE_BASE_URL` points the server at a different Obscate API. Leave
it unset to use the hosted API. (A self-hosted edition is in pilot and not
generally available.)

## 4. Verify

Call the `list_entities` tool (needs no key). It should return the supported
entity labels and strategies. Then call `redact` with a short test string such
as `Card 4242 4242 4242 4242 for test@example.com` and `strategy: "fpe"`, and
`restore` on the result; the original text should come back exactly.

## Notes

- Tools: `redact`, `restore`, `detect`, `redact_json`, `restore_json`, `list_entities`.
- If you store redacted tokens to restore later, also store the `key_version`
  returned by `redact` and pass it to `restore`.
- Known limitation: if a model quotes only part of a redacted value ("card ending
  in 7588"), that fragment cannot be restored, and with `fpe`/`synthetic` the wrong
  digits look real. For text a person will read, use `strategy: "tokenize"`.
  Details: https://obscate.com/docs/mcp#partial-values · Research: https://obscate.com/blog/partial-values-research
- Docs: https://obscate.com/docs/mcp · Source (MIT): https://github.com/AstracoreAI/obscate-integrations
