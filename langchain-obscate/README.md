# langchain-obscate

Keep personal data away from your LLM provider. You add one wrapper. User
input, prompt variables and tool results are redacted before they reach the
model, and the model's answer (including tool-call arguments) comes back with
the real values restored.

It is a thin wrapper over the [Obscate](https://obscate.com) HTTP API.
Detection runs in the API, not in this package. Full docs:
[obscate.com/docs/langchain](https://obscate.com/docs/langchain).

```text
your input ──redact──▶ LLM sees "seab.yomog@tziwxuo.com", "[PERSON:…]" ──restore──▶ your user sees "ravi.kumar@example.com", "Ravi Kumar"
```

## Quickstart (under 5 minutes)

```bash
pip install "langchain-obscate[examples]"
export OBSCATE_API_KEY=ob_...          # free: 1,000 requests/month at https://obscate.com, no card
export ANTHROPIC_API_KEY=sk-ant-...    # optional: without it the example uses a local stand-in LLM
python examples/quickstart.py
```

Output (real run with the stand-in LLM; the redacted values differ per key):

```text
1) Your input:
   Customer Ravi Kumar (email ravi.kumar@example.com phone +91 98765 43210) says card 4242 4242 4242 4242 was charged twice. ...
2) What the LLM receives:
  [human] [PERSON:rug3juf…] (email seab.yomog@tziwxuo.com phone +91 45671 58378) says card 7982 1772 2838 5004 was charged twice. ...
3) Restored answer for your user:
   ... will confirm by email to ravi.kumar@example.com.
```

## Use it

**Any chat model, LLM or chain:** wrap it with `with_obscate`.

```python
from langchain_obscate import with_obscate

safe_llm = with_obscate(ChatAnthropic(model="claude-haiku-5-5"))
safe_llm.invoke("Draft a reply to ravi@example.com about order 1182")   # -> AIMessage, restored

chain = with_obscate(prompt | llm | StrOutputParser())                # prompt variables redacted
chain.invoke({"name": "Ravi Kumar", "email": "ravi@example.com"})     # -> str, restored
```

**Agents (`create_agent`):** add the middleware. It redacts every model call,
covering user messages, tool results, earlier turns and the system prompt. It
restores the model's reply before your tools run, so your tools receive real
values and the provider never does.

```python
pip install "langchain-obscate[agents]"

from langchain.agents import create_agent
from langchain_obscate import ObscateMiddleware

agent = create_agent("anthropic:claude-haiku-5-5", tools=[send_email],
                     middleware=[ObscateMiddleware()])
```

**Explicit tools:** use `ObscateRedactTool` (fpe-redacts a text) and
`ObscateDetectTool` (labels and counts only). Use them when the agent itself
should scrub a record before passing it downstream. The agent's own LLM still
sees the tool input, so use the middleware to protect against the provider.

### Why middleware rather than a callback

LangChain callbacks are observers. They are told what is sent to the model
but cannot change it. Redaction has to rewrite the request, so it lives in a
Runnable wrapper (chains) and in agent middleware (agents). The quickstart
uses a callback only to *show* you what the provider receives.

## Strategies

| strategy | LLM sees | restore needs | notes |
|---|---|---|---|
| `fpe` (default) | `seab.yomog@tziwxuo.com`, `7982 1772 2838 5004`, `[PERSON:…]` | your API key only | formats kept (valid-looking card numbers, emails). Values that can't keep their format fall back to bracketed `[LABEL:…]` tokens |
| `tokenize` | `[EMAIL_1]`, `[PERSON_1]` | the token map (held in memory for one call) | clearest for the model; consistent across all messages of one call |
| `synthetic` | realistic fake names/emails | the token map | most natural prompts |
| `mask` / `hash` / `partial` | `****`, hashes, `****1234` | not restorable | output stays redacted |

More: [strategies](https://obscate.com/docs/strategies).

```python
with_obscate(llm, strategy="tokenize", labels=["EMAIL", "PHONE"], min_confidence=0.5)
```

## What to expect

- **Detection quality.** Structured IDs (email, phone, credit card, Aadhaar,
  PAN, GSTIN, IBAN, SSN, IP, AWS keys) are near-perfect because they use
  patterns and checksums. Names and places use an NER model and are
  best-effort, so some will get through. Use `labels` and test on your data.
  ([Detectors](https://obscate.com/docs/detectors), [benchmark](https://obscate.com/docs/benchmark).)
- **fpe caveat.** If the model *invents* a new email, phone or ID that was
  never redacted, fpe restore cannot tell it from a token and turns it into a
  different value. Use `tokenize` if your model makes up contact details.
- **Key versions are handled for you.** Each redaction records the
  `key_version` of your account's key, and the wrapper sends it back on
  restore. A round trip keeps working even if Obscate rotates keys mid-call.
  If you keep a `RestoreContext` (or fpe output) to restore later, keep its
  `key_version` too.
- **Cost.** One Obscate request per model call (the whole conversation goes
  in one request, 1 unit per 100 KB). Restores are free. A 5-step agent run
  uses 5 units, so the free tier covers about 200 such runs a month.
  ([Pricing](https://obscate.com/pricing).)
- **Partial values can't be restored.** If the model quotes only part of a
  redacted value ("card ending in 7588", where 7588 is the end of the fpe
  token), that fragment comes back as-is and is wrong. Seen with real Claude
  output. Ask the model to repeat values whole, or use `tokenize`, whose
  `[CREDIT_CARD_1]` tokens models don't split.
- **Streaming is buffered.** `stream()` on the wrapper yields one restored
  chunk at the end. With the agent middleware, `stream_mode="messages"` shows
  the raw, still-redacted tokens; the messages in agent state are restored.
- **Fails closed.** If Obscate errors (network, quota, 503), the LLM is not
  called and `ObscateError` is raised.
- **Self-hosted.** Set `OBSCATE_BASE_URL` to your instance. The self-hosted
  edition is in pilot; ask contact@obscate.com.

## Privacy

This package never logs, prints or writes the text, the detected values or
the token map. Token maps live in memory for one call and are hidden from
`repr()`. Errors carry the HTTP status and the API's fixed message only. This
is enforced by `tests/test_langchain_obscate.py::test_privacy_*`.

## Develop

```bash
pip install -e '.[test]' && pytest -q    # unit tests against an in-process fake API (no network)
```

License: MIT.
