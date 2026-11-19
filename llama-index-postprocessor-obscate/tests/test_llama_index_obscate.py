import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, List

import pytest
from llama_index.core import Document, MockEmbedding, VectorStoreIndex
from llama_index.core.llms import CompletionResponse
from llama_index.core.llms.mock import MockLLM
from llama_index.core.schema import NodeWithScore, TextNode

sys.path.insert(0, str(Path(__file__).parent))
from fake_api import FakeObscate  # noqa: E402

from llama_index.postprocessor.obscate import (ObscateClient, ObscateError, ObscatePostprocessor,  # noqa: E402
                                               ObscateQueryEngine)

DOC = "Ravi Kumar (ravi@example.com, +91 98765 43210) reported a double charge."
RAW = ["Ravi Kumar", "ravi@example.com", "98765 43210"]


class RecordingLLM(MockLLM):
    """Returns the full prompt as its answer (so restore is visible) and records it."""
    prompts: List[str] = []

    def complete(self, prompt: str, formatted: bool = False, **kw: Any) -> CompletionResponse:
        self.prompts.append(prompt)
        return CompletionResponse(text=prompt)

    async def acomplete(self, prompt: str, formatted: bool = False, **kw: Any) -> CompletionResponse:
        return self.complete(prompt, formatted, **kw)


@pytest.fixture
def fake():
    return FakeObscate()


@pytest.fixture
def client(fake):
    t = fake.transport()
    return ObscateClient(api_key="ob_test_key", base_url="https://fake.obscate.test", transport=t,
                         async_transport=t)


@pytest.fixture
def index():
    return VectorStoreIndex.from_documents(
        [Document(text=DOC, metadata={"author": "Priya Sharma", "ticket": "T-1"}),
         Document(text="Unrelated note about shipping times.")],
        embed_model=MockEmbedding(embed_dim=8))


@pytest.mark.parametrize("strategy", ["fpe", "tokenize", "synthetic"])
def test_query_engine_redacts_query_nodes_and_restores(index, client, strategy):
    llm = RecordingLLM()
    qe = ObscateQueryEngine.wrap(index.as_query_engine(llm=llm, similarity_top_k=2),
                                 client=client, strategy=strategy)
    resp = qe.query("What did Ravi Kumar ask?")
    seen = "\n".join(llm.prompts)
    for v in RAW + ["Priya Sharma"]:
        assert v not in seen, f"LLM saw {v!r}"
    # MockLLM echoes the prompt, so the restored answer contains the originals
    assert "ravi@example.com" in str(resp) and "What did Ravi Kumar ask?" in str(resp)
    # citations are the untouched originals; the index itself is not modified
    assert any("ravi@example.com" in n.node.get_content() for n in resp.source_nodes)
    assert any(DOC in n.get_content() for n in index.docstore.docs.values())


def test_query_engine_one_request_per_query(index, client, fake):
    qe = ObscateQueryEngine.wrap(index.as_query_engine(llm=RecordingLLM()), client=client)
    qe.query("What did Ravi Kumar ask?")
    assert [p for p, _ in fake.calls].count("/v1/redact-json") == 1


def test_from_args(index, client):
    llm = RecordingLLM()
    qe = ObscateQueryEngine.from_args(index.as_retriever(similarity_top_k=2), llm=llm, client=client)
    assert "ravi@example.com" in str(qe.query("Who reported a charge?"))
    assert "ravi@example.com" not in "".join(llm.prompts)


def test_async_query(index, client):
    llm = RecordingLLM()
    qe = ObscateQueryEngine.wrap(index.as_query_engine(llm=llm), client=client)
    resp = asyncio.run(qe.aquery("What did Ravi Kumar ask?"))
    assert "ravi@example.com" in str(resp)
    assert "ravi@example.com" not in "".join(llm.prompts)


@pytest.mark.parametrize("strategy", ["fpe", "tokenize"])
def test_postprocessor_in_standard_query_engine(index, client, strategy):
    llm = RecordingLLM()
    obscate = ObscatePostprocessor(client=client, strategy=strategy)
    qe = index.as_query_engine(llm=llm, node_postprocessors=[obscate], similarity_top_k=2)
    answer = obscate.restore(qe.query("Summarise the ticket"))
    assert "ravi@example.com" not in "".join(llm.prompts)
    assert "Priya Sharma" not in "".join(llm.prompts)       # metadata redacted too
    assert "ravi@example.com" in answer


def test_postprocessor_does_not_mutate_input_nodes(client):
    n = NodeWithScore(node=TextNode(text=DOC), score=1.0)
    out = ObscatePostprocessor(client=client).postprocess_nodes([n])
    assert n.node.get_content() == DOC
    assert "ravi@example.com" not in out[0].node.get_content()


def test_excluded_llm_metadata_is_not_sent(client, fake):
    node = TextNode(text="hello", metadata={"secret_path": "/home/ravi@example.com"},
                    excluded_llm_metadata_keys=["secret_path"])
    ObscatePostprocessor(client=client).postprocess_nodes([NodeWithScore(node=node)])
    assert "secret_path" not in fake.sent_text()


def test_fails_closed(index):
    t = FakeObscate(fail_with=503).transport()
    c = ObscateClient(api_key="ob_test_key", base_url="https://fake.obscate.test", transport=t)
    llm = RecordingLLM()
    with pytest.raises(ObscateError):
        ObscateQueryEngine.wrap(index.as_query_engine(llm=llm), client=c).query("Ravi Kumar?")
    assert llm.prompts == []


def test_privacy_nothing_sensitive_logged_or_written(index, client, caplog, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.DEBUG)
    for strategy in ("fpe", "tokenize"):
        qe = ObscateQueryEngine.wrap(index.as_query_engine(llm=RecordingLLM()),
                                     client=client, strategy=strategy)
        qe.query("What did Ravi Kumar ask?")
        p = ObscatePostprocessor(client=client, strategy=strategy)
        p.postprocess_nodes([NodeWithScore(node=TextNode(text=DOC))])
        assert "ravi@example.com" not in repr(p._local.ctx)
    bad = ObscateClient(api_key="wrong", base_url="https://fake.obscate.test",
                        transport=FakeObscate().transport())
    with pytest.raises(ObscateError):
        ObscatePostprocessor(client=bad).postprocess_nodes([NodeWithScore(node=TextNode(text=DOC))])
    # LlamaIndex core itself logs retrieved node text at DEBUG (llama_index.core.indices.utils),
    # upstream of us; the README tells users to keep that logger above DEBUG. Everything else
    # (this package, httpx, the root logger) must be clean.
    upstream = [r for r in caplog.records if r.name.startswith("llama_index.core")]
    assert all(r.levelno <= logging.DEBUG for r in upstream)
    logged = "\n".join(rec.getMessage() + repr(rec.args) for rec in caplog.records
                       if not rec.name.startswith("llama_index.core"))
    out = capsys.readouterr()
    for v in RAW + ["[EMAIL_1]"]:
        assert v not in logged and v not in out.out + out.err
    assert list(tmp_path.iterdir()) == []


def test_key_version_carried_through_round_trip(index, client, fake):
    class RotatingLLM(RecordingLLM):
        """Rotates the fake API's key version while 'thinking'."""
        def complete(self, prompt: str, formatted: bool = False, **kw: Any) -> CompletionResponse:
            fake.key_version = 2
            return super().complete(prompt, formatted, **kw)
    llm = RotatingLLM()
    qe = ObscateQueryEngine.wrap(index.as_query_engine(llm=llm, similarity_top_k=2), client=client)
    resp = qe.query("What did Ravi Kumar ask?")
    assert "ravi@example.com" in str(resp) and "WRONG-KEY-VERSION" not in str(resp)
    assert [b.get("key_version") for p, b in fake.calls if p == "/v1/restore-fpe"] == [1]
