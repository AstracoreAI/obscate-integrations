# llama-index-postprocessor-obscate

Reversible PII redaction for LlamaIndex RAG. The question and the retrieved
chunks are redacted before the LLM sees them, and the answer comes back with
the real values restored. Your index and your citations stay untouched.

It is a thin wrapper over the [Obscate](https://obscate.com) HTTP API.
Detection runs in the API, not in this package. Full docs:
[obscate.com/docs/llamaindex](https://obscate.com/docs/llamaindex).

## Quickstart (under 5 minutes)

```bash
pip install "llama-index-postprocessor-obscate[examples]"
export OBSCATE_API_KEY=ob_...          # free: 1,000 requests/month at https://obscate.com, no card
export ANTHROPIC_API_KEY=sk-ant-...    # optional: without it the example uses a local stand-in LLM
python examples/quickstart.py
```

Real output (stand-in LLM, Obscate engine; redacted values differ per key):

```text
1) Your question:
   What does Ravi Kumar want, and what email should we reply to?
2) What the LLM receives:
   Ticket 813: Priya Sharma (mlaem@bskcfsg.org) asked to change her delivery address.
   Ticket 812: [PERSON:wdmvms3g…] (seab.yomog@tziwxuo.com, +91 45671 58378) was charged twice on card 7982 1772 2838 5004 ...
   Query: What does [PERSON:wdmvms3g…] want, and what email should we reply to?
3) Restored answer for your user:
   (stand-in LLM) From the tickets: Ticket 812: Ravi Kumar (ravi.kumar@example.com, +91 98765 43210) was charged twice ...
```

Look at line 2: **"Priya Sharma" was not caught** in that sentence, while
"Ravi Kumar" was. That is typical. Emails, phones, cards and Indian IDs are
near-perfect because they use patterns and checksums. Names and places come
from an NER model and are best-effort. Test on your own data.
([Detectors](https://obscate.com/docs/detectors), [benchmark](https://obscate.com/docs/benchmark).)

## Use it

**One step: wrap your query engine.**

```python
from llama_index.postprocessor.obscate import ObscateQueryEngine

qe = ObscateQueryEngine.wrap(index.as_query_engine(llm=llm))   # strategy="fpe" by default
print(qe.query("What did Ravi Kumar ask about?"))              # answer restored
```

Or build it from parts with
`ObscateQueryEngine.from_args(retriever, llm=llm, strategy="tokenize")`.
Retrieval runs on the original query against your own index. Only the
synthesis LLM sees redacted text. The query and all chunks go in **one**
Obscate request, so tokens are consistent: the same person gets the same
token in the question and in the chunks.

**Node postprocessor, for pipelines you assemble yourself:**

```python
from llama_index.postprocessor.obscate import ObscatePostprocessor

obscate = ObscatePostprocessor()                       # fpe: restore needs no state
qe = index.as_query_engine(llm=llm, node_postprocessors=[obscate])
answer = obscate.restore(qe.query("Summarise ticket 812"))
```

A postprocessor can't change the question text, which still reaches the LLM
unredacted. Use `ObscateQueryEngine` when questions contain PII. With
`tokenize`/`synthetic`, the postprocessor keeps the last token map per thread
in memory, so call `restore` in the same thread before the next query.

String metadata the LLM would see (for example `author`) is redacted too.
Keys listed in `excluded_llm_metadata_keys` are not sent at all. Turn this off
with `redact_metadata=False`.

## Strategies

`fpe` (default) keeps formats, for example valid-looking card numbers and
email-shaped addresses. Values that can't keep their format, such as names,
become bracketed `[PERSON:…]` tokens. Restoring fpe needs only your key.
`tokenize` (`[EMAIL_1]`) and `synthetic` (realistic fakes) restore from an
in-memory token map. `mask`, `hash` and `partial` are one-way, so the answer
stays redacted.

**fpe caveat:** an email, phone or ID the model *invents* (never redacted)
can't be told apart from an fpe token, and restore turns it into a different
value. Use `tokenize` if that matters.

**Key versions are handled for you.** The query engine restores with the
`key_version` its redaction returned, so a key rotation on Obscate's side
never breaks an answer. If you keep fpe output to restore later, keep
`key_version` with it.

## What it does not cover

- **Embeddings.** Your embedding model sees raw text when you index and query.
  If it is a remote API, use a local embedding model or redact documents
  before indexing.
- **Streaming.** Answers are read to completion and then restored, because a
  token can be split across chunks.
- **LlamaIndex's own DEBUG logs.** `llama_index.core.indices.utils` logs
  retrieved chunk text at DEBUG level, which is upstream of this package.
  Keep the `llama_index` logger at INFO or above in production.

## Cost, failure and self-hosting

- 1 Obscate request per query (1 unit per 100 KB). Restores are free. The
  free tier covers 1,000 queries a month ([pricing](https://obscate.com/pricing)).
- **Fails closed.** If Obscate errors, the LLM is not called and
  `ObscateError` is raised.
- Set `OBSCATE_BASE_URL` to use a self-hosted Obscate. The self-hosted
  edition is in pilot; ask contact@obscate.com.

## Privacy

This package never logs, prints or writes the text, the detected values or
the token map. Errors carry the HTTP status and the API's fixed message only.
This is enforced by `tests/test_llama_index_obscate.py::test_privacy_*`.

## Develop

```bash
pip install -e '.[test]' && pytest -q    # unit tests against an in-process fake API (no network)
```

License: MIT.
