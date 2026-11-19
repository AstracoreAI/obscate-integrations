"""Real end-to-end check of all three integrations against a running Obscate API.

    # key in obscate-integrations/.env (gitignored) or the environment:
    python scripts/live_e2e.py            # all
    python scripts/live_e2e.py mcp        # or: langchain | llamaindex

Uses OBSCATE_BASE_URL (default https://api.obscate.com). Uses about 12 API
units in total (restores are free). If ANTHROPIC_API_KEY is set, the
LangChain and LlamaIndex checks also make one real LLM call each (a fraction
of a cent); otherwise they use a stand-in model. Prints check names and
PASS/FAIL only, never the key or payloads.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = "Ravi Kumar (ravi.kumar@example.com, +91 98765 43210) paid with card 4242 4242 4242 4242."
RAW = ["ravi.kumar@example.com", "98765 43210", "4242 4242 4242 4242"]
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def load_env() -> None:
    f = ROOT / ".env"
    if f.is_file():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def clean(text: str) -> bool:
    return not any(v in text for v in RAW)


# -- MCP: real stdio subprocess ---------------------------------------------------
async def run_mcp() -> None:
    from mcp import Client, StdioServerParameters
    print("obscate-mcp (stdio subprocess):")
    params = StdioServerParameters(command=sys.executable, args=["-m", "obscate_mcp"],
                                   env={**os.environ})
    async with Client(params) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        check("lists redact/restore/detect/redact_json/restore_json",
              {"redact", "restore", "detect", "redact_json", "restore_json"} <= tools)

        def data(r):
            if r.is_error:
                raise RuntimeError(r.content[0].text)
            return json.loads(r.content[0].text) if r.structured_content is None else r.structured_content

        for strategy in ("fpe", "tokenize", "synthetic"):
            r = data(await c.call_tool("redact", {"text": SAMPLE, "strategy": strategy}))
            check(f"redact[{strategy}] removes structured PII", clean(r["redacted"]))
            args = {"text": r["redacted"], "strategy": strategy}
            if strategy != "fpe":
                args["token_map"] = r["token_map"]
            back = data(await c.call_tool("restore", args))
            check(f"restore[{strategy}] round-trips exactly", back["text"] == SAMPLE)
        d = data(await c.call_tool("detect", {"text": SAMPLE}))
        check("detect finds EMAIL, PHONE, CREDIT_CARD without echoing values",
              {"EMAIL", "PHONE", "CREDIT_CARD"} <= set(d["counts"]["by_label"]) and clean(json.dumps(d)))
        payload = {"customer": {"name": "Ravi Kumar", "email": "ravi.kumar@example.com",
                                "phone": "+91 98765 43210"}, "qty": 3}
        rj = data(await c.call_tool("redact_json", {"json": payload}))
        check("redact_json keeps shape and removes PII",
              rj["redacted"]["qty"] == 3 and clean(json.dumps(rj["redacted"])))
        bj = data(await c.call_tool("restore_json", {"json": rj["redacted"], "type_changes": rj["type_changes"],
                                                     "keys_redacted": rj["keys_redacted"]}))
        check("restore_json round-trips", bj["json"] == payload)


# -- LangChain ----------------------------------------------------------------------
def run_langchain() -> None:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from langchain_obscate import ObscateMiddleware, with_obscate

    print("langchain-obscate:")
    seen: list[str] = []

    class Echo(BaseChatModel):
        @property
        def _llm_type(self):
            return "echo"

        def bind_tools(self, tools, **kw):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kw):
            seen.append(json.dumps([m.model_dump() for m in messages], default=str))
            last = messages[-1]
            if isinstance(last, HumanMessage) and "send" in last.content:
                email = next(w.strip("(),.") for w in last.content.split() if "@" in w)
                msg = AIMessage("", tool_calls=[{"name": "send_email", "args": {"to": email},
                                                 "id": "c1", "type": "tool_call"}])
            else:
                msg = AIMessage(f"Echo: {last.content}")
            return ChatResult(generations=[ChatGeneration(message=msg)])

    for strategy in ("fpe", "tokenize", "synthetic"):
        seen.clear()
        out = with_obscate(Echo(), strategy=strategy).invoke([HumanMessage(SAMPLE)])
        check(f"Runnable[{strategy}]: LLM saw no structured PII", clean("".join(seen)))
        check(f"Runnable[{strategy}]: output restored", out.content == f"Echo: {SAMPLE}")

    got: list[str] = []

    @tool
    def send_email(to: str) -> str:
        """Send an email."""
        got.append(to)
        return f"queued for {to}"

    from langchain.agents import create_agent
    seen.clear()
    agent = create_agent(Echo(), tools=[send_email], middleware=[ObscateMiddleware()])
    agent.invoke({"messages": [HumanMessage("please send a note to ravi.kumar@example.com")]})
    check("Middleware: tool received the real email", got == ["ravi.kumar@example.com"])
    check("Middleware: LLM never saw it (incl. tool result)", "ravi.kumar@example.com" not in "".join(seen))

    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"), max_tokens=200)
        ans = with_obscate(llm).invoke(
            "Repeat this email address exactly and nothing else: ravi.kumar@example.com")
        check("Real LLM (Anthropic): answer restored to the original email",
              "ravi.kumar@example.com" in ans.content)
    else:
        print("  SKIP  real LLM call (ANTHROPIC_API_KEY not set)")


# -- LlamaIndex ---------------------------------------------------------------------
def run_llamaindex() -> None:
    from typing import Any, List
    from llama_index.core import Document, MockEmbedding, VectorStoreIndex
    from llama_index.core.llms import CompletionResponse
    from llama_index.core.llms.mock import MockLLM
    from llama_index.postprocessor.obscate import ObscatePostprocessor, ObscateQueryEngine

    print("llama-index-postprocessor-obscate:")

    class Echo(MockLLM):
        prompts: List[str] = []

        def complete(self, prompt: str, formatted: bool = False, **kw: Any) -> CompletionResponse:
            self.prompts.append(prompt)
            return CompletionResponse(text=prompt)

    index = VectorStoreIndex.from_documents([Document(text=SAMPLE)], embed_model=MockEmbedding(embed_dim=8))
    for strategy in ("fpe", "tokenize"):
        llm = Echo()
        resp = ObscateQueryEngine.wrap(index.as_query_engine(llm=llm), strategy=strategy).query(
            "What card did ravi.kumar@example.com use?")
        check(f"QueryEngine[{strategy}]: LLM saw no structured PII", clean("".join(llm.prompts)))
        check(f"QueryEngine[{strategy}]: answer restored", SAMPLE in str(resp))
    llm = Echo()
    pp = ObscatePostprocessor()
    ans = pp.restore(index.as_query_engine(llm=llm, node_postprocessors=[pp]).query("Summarise"))
    check("Postprocessor[fpe]: nodes redacted, answer restored", clean("".join(llm.prompts)) and SAMPLE in ans)

    if os.environ.get("ANTHROPIC_API_KEY"):
        from llama_index.llms.anthropic import Anthropic
        real = Anthropic(model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"), max_tokens=200)
        r = ObscateQueryEngine.wrap(index.as_query_engine(llm=real)).query(
            "What is the customer's email address? Reply with the address only.")
        check("Real LLM (Anthropic): answer restored to the original email", "ravi.kumar@example.com" in str(r))
    else:
        print("  SKIP  real LLM call (ANTHROPIC_API_KEY not set)")


def main() -> int:
    load_env()
    if not os.environ.get("OBSCATE_API_KEY"):
        print("OBSCATE_API_KEY is not set (put it in obscate-integrations/.env)")
        return 2
    print("target:", os.environ.get("OBSCATE_BASE_URL", "https://api.obscate.com"))
    which = sys.argv[1:] or ["mcp", "langchain", "llamaindex"]
    for w in which:
        try:
            {"mcp": lambda: asyncio.run(run_mcp()), "langchain": run_langchain,
             "llamaindex": run_llamaindex}[w]()
        except Exception as e:  # report type + message (Obscate errors never contain payloads)
            check(f"{w}: crashed with {type(e).__name__}: {e}", False)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
