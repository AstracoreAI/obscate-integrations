"""llama-index-postprocessor-obscate end-to-end: RAG where the LLM never sees the PII.

    pip install "llama-index-postprocessor-obscate[examples]"
    export OBSCATE_API_KEY=ob_...            # free key: https://obscate.com
    export ANTHROPIC_API_KEY=sk-ant-...      # optional; without it a local stand-in LLM is used
    python examples/quickstart.py

Costs: 1 Obscate request per query (free tier: 1,000/month) and one small LLM
call if ANTHROPIC_API_KEY is set. Embeddings use LlamaIndex's MockEmbedding, so
no embedding key is needed (retrieval is trivial with 3 documents).
"""
import os
from pathlib import Path
from typing import Any

from llama_index.core import Document, MockEmbedding, VectorStoreIndex
from llama_index.core.llms import CompletionResponse, CustomLLM, LLMMetadata
from llama_index.core.llms.callbacks import llm_completion_callback

from llama_index.postprocessor.obscate import ObscateQueryEngine


def load_env() -> None:
    """Read KEY=VALUE lines from a .env here or in a parent folder (values never printed)."""
    for d in [Path.cwd(), *Path(__file__).resolve().parents]:
        f = d / ".env"
        if f.is_file():
            for line in f.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


class StandInLLM(CustomLLM):
    """Offline stand-in: 'answers' by quoting the first context line it was given."""
    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(model_name="stand-in")

    @llm_completion_callback()
    def complete(self, prompt: str, formatted: bool = False, **kw: Any) -> CompletionResponse:
        ctx = [l for l in prompt.splitlines() if "charged" in l]
        return CompletionResponse(text="(stand-in LLM) From the tickets: " + (ctx[0].strip() if ctx else "n/a"))

    @llm_completion_callback()
    def stream_complete(self, prompt: str, formatted: bool = False, **kw: Any):
        yield self.complete(prompt)


class ShowPrompt(CustomLLM):
    """Demo only: prints the prompt exactly as the LLM provider receives it, then delegates."""
    inner: Any = None

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(model_name="show-prompt")

    @llm_completion_callback()
    def complete(self, prompt: str, formatted: bool = False, **kw: Any) -> CompletionResponse:
        print("2) What the LLM receives:\n" + "\n".join("   " + l for l in prompt.splitlines() if l.strip()))
        return self.inner.complete(prompt, formatted=formatted, **kw)

    @llm_completion_callback()
    def stream_complete(self, prompt: str, formatted: bool = False, **kw: Any):
        yield self.complete(prompt)


def get_llm():
    if os.environ.get("ANTHROPIC_API_KEY"):
        from llama_index.llms.anthropic import Anthropic
        return Anthropic(model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"), max_tokens=300)
    print("(no ANTHROPIC_API_KEY: using a local stand-in LLM)")
    return StandInLLM()


def main() -> None:
    load_env()
    if not os.environ.get("OBSCATE_API_KEY"):
        raise SystemExit("Set OBSCATE_API_KEY (free key at https://obscate.com) or put it in a .env file.")
    docs = [
        Document(text="Ticket 812: Ravi Kumar (ravi.kumar@example.com, +91 98765 43210) was charged "
                      "twice on card 4242 4242 4242 4242 and wants a refund."),
        Document(text="Ticket 813: Priya Sharma (priya@example.org) asked to change her delivery address."),
        Document(text="Refund policy: duplicate charges are refunded within 5 business days."),
    ]
    index = VectorStoreIndex.from_documents(docs, embed_model=MockEmbedding(embed_dim=16))
    llm = ShowPrompt(inner=get_llm())

    # One step: wrap the normal query engine. strategy="fpe" by default.
    qe = ObscateQueryEngine.wrap(index.as_query_engine(llm=llm, similarity_top_k=3))

    question = "What does Ravi Kumar want, and what email should we reply to?"
    print("1) Your question:\n  ", question)
    answer = qe.query(question)
    print("3) Restored answer for your user:\n  ", answer)


if __name__ == "__main__":
    main()
