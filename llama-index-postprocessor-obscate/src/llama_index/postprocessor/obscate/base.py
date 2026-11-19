"""Obscate for LlamaIndex: redact what the LLM sees, restore what it says.

Two building blocks:

* ``ObscateQueryEngine``: the one-step option. It wraps a retriever (or an
  existing ``index.as_query_engine()``), redacts the query and the retrieved
  nodes in one Obscate request, runs the response synthesizer on the redacted
  data, and restores the answer. ``source_nodes`` stay the original nodes, so
  your citations are real.
* ``ObscatePostprocessor``: a node postprocessor that redacts retrieved node
  text and metadata, for pipelines you assemble yourself. Restore the final
  answer with ``postprocessor.restore(text)``.

Scope: this protects what the LLM *provider* receives. Embeddings are
computed when you index and query. If your embedding model is a remote API,
it still sees raw text. Use a local embedding model, or redact documents
before indexing.
"""
from __future__ import annotations

import threading
from typing import Any, List, Optional

from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.base.response.schema import Response, StreamingResponse
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.query_engine.custom import CustomQueryEngine
from llama_index.core.response_synthesizers import BaseSynthesizer, get_response_synthesizer
from llama_index.core.schema import MetadataMode, NodeWithScore, QueryBundle
from pydantic import Field, PrivateAttr

from ._client import STRATEGIES, ObscateClient, RestoreContext

__all__ = ["ObscatePostprocessor", "ObscateQueryEngine", "ObscateNodeRedactor"]


def _has_text(obj: Any) -> bool:
    if isinstance(obj, str):
        return bool(obj.strip())
    if isinstance(obj, dict):
        return any(_has_text(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_text(v) for v in obj)
    return False


class ObscateNodeRedactor:
    """Shared logic: one Obscate request per query for the query + all nodes."""

    def __init__(self, strategy: str = "fpe", labels: Optional[list[str]] = None,
                 min_confidence: float = 0.0, redact_metadata: bool = True,
                 api_key: Optional[str] = None, base_url: Optional[str] = None,
                 client: Optional[ObscateClient] = None):
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {list(STRATEGIES)}")
        self.strategy, self.labels, self.min_confidence = strategy, labels, min_confidence
        self.redact_metadata = redact_metadata
        self.client = client or ObscateClient(api_key=api_key, base_url=base_url,
                                              user_agent="llama-index-postprocessor-obscate/0.1.1")

    def __repr__(self) -> str:
        return f"ObscateNodeRedactor(strategy={self.strategy!r}, client={self.client!r})"

    def _entry(self, n: NodeWithScore) -> dict:
        e: dict[str, Any] = {"t": n.node.get_content(metadata_mode=MetadataMode.NONE)}
        if self.redact_metadata:
            hidden = set(n.node.excluded_llm_metadata_keys or [])
            e["m"] = {k: v for k, v in (n.node.metadata or {}).items()
                      if isinstance(v, str) and k not in hidden}
        return e

    @staticmethod
    def _copy(n: NodeWithScore, e: dict) -> NodeWithScore:
        # Copy: the retrieved node objects are shared with your index/docstore.
        node = n.node.model_copy(deep=True)
        node.set_content(e["t"])
        if "m" in e:
            node.metadata = {**node.metadata, **e["m"]}
        return NodeWithScore(node=node, score=n.score)

    def _args(self) -> dict:
        return {"strategy": self.strategy, "labels": self.labels, "min_confidence": self.min_confidence}

    def _empty(self) -> RestoreContext:
        return RestoreContext(self.strategy, labels=self.labels, min_confidence=self.min_confidence)

    def redact(self, nodes: List[NodeWithScore], query: Optional[str] = None
               ) -> tuple[List[NodeWithScore], Optional[str], RestoreContext]:
        payload = {"q": query or "", "n": [self._entry(n) for n in nodes]}
        if not _has_text(payload):
            return list(nodes), query, self._empty()
        r, ctx = self.client.redact_json(payload, **self._args())
        red = r["redacted"]
        return [self._copy(n, e) for n, e in zip(nodes, red["n"])], (red["q"] if query is not None else None), ctx

    async def aredact(self, nodes: List[NodeWithScore], query: Optional[str] = None
                      ) -> tuple[List[NodeWithScore], Optional[str], RestoreContext]:
        payload = {"q": query or "", "n": [self._entry(n) for n in nodes]}
        if not _has_text(payload):
            return list(nodes), query, self._empty()
        r, ctx = await self.client.aredact_json(payload, **self._args())
        red = r["redacted"]
        return [self._copy(n, e) for n, e in zip(nodes, red["n"])], (red["q"] if query is not None else None), ctx

    def restore(self, text: str, ctx: RestoreContext) -> str:
        if not ctx.reversible or not _has_text(text):
            return text
        return self.client.restore(text, ctx)

    async def arestore(self, text: str, ctx: RestoreContext) -> str:
        if not ctx.reversible or not _has_text(text):
            return text
        return await self.client.arestore(text, ctx)


class ObscatePostprocessor(BaseNodePostprocessor):
    """Node postprocessor that redacts node text (and string metadata) before
    synthesis. With the default ``fpe`` strategy, ``restore(answer)`` needs no
    state. With ``tokenize``/``synthetic``, the token map of the most recent
    call is kept in memory per thread, so restore in the same thread before
    the next query.

        qe = index.as_query_engine(node_postprocessors=[obscate := ObscatePostprocessor()])
        answer = obscate.restore(str(qe.query("...")))

    Note: a postprocessor cannot change the query text the LLM sees. Use
    ``ObscateQueryEngine`` to redact the question as well.
    """

    strategy: str = Field(default="fpe", description="fpe | tokenize | synthetic | mask | hash | partial")
    labels: Optional[List[str]] = Field(default=None)
    min_confidence: float = Field(default=0.0)
    redact_metadata: bool = Field(default=True)
    _redactor: ObscateNodeRedactor = PrivateAttr()
    _local: threading.local = PrivateAttr(default_factory=threading.local)

    def __init__(self, client: Optional[ObscateClient] = None, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._redactor = ObscateNodeRedactor(self.strategy, self.labels, self.min_confidence,
                                             self.redact_metadata, api_key, base_url, client)

    @classmethod
    def class_name(cls) -> str:
        return "ObscatePostprocessor"

    def _postprocess_nodes(self, nodes: List[NodeWithScore],
                           query_bundle: Optional[QueryBundle] = None) -> List[NodeWithScore]:
        red, _, ctx = self._redactor.redact(nodes)
        self._local.ctx = ctx
        return red

    def _ctx(self) -> RestoreContext:
        ctx = getattr(self._local, "ctx", None)
        if ctx is None:
            if self.strategy != "fpe":
                raise RuntimeError("no redaction to restore yet in this thread")
            ctx = self._redactor._empty()
        return ctx

    def restore(self, text: Any) -> str:
        """Restore an answer (str or Response) produced from redacted nodes."""
        return self._redactor.restore(str(text), self._ctx())

    async def arestore(self, text: Any) -> str:
        return await self._redactor.arestore(str(text), self._ctx())


class ObscateQueryEngine(CustomQueryEngine):
    """Query engine that keeps PII away from the LLM and restores the answer.

        safe = ObscateQueryEngine.wrap(index.as_query_engine())
        print(safe.query("What did Ravi Kumar ask about?"))

    Retrieval runs on the original query (locally, against your index). Only
    the synthesis LLM sees redacted data. Streaming synthesizers are read to
    completion before restoring, because a token can be split across chunks.
    """

    retriever: BaseRetriever
    response_synthesizer: BaseSynthesizer
    node_postprocessors: List[BaseNodePostprocessor] = Field(default_factory=list)
    strategy: str = "fpe"
    labels: Optional[List[str]] = None
    min_confidence: float = 0.0
    redact_metadata: bool = True
    _redactor: ObscateNodeRedactor = PrivateAttr()

    def __init__(self, client: Optional[ObscateClient] = None, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._redactor = ObscateNodeRedactor(self.strategy, self.labels, self.min_confidence,
                                             self.redact_metadata, api_key, base_url, client)

    @classmethod
    def from_args(cls, retriever: BaseRetriever, llm: Any = None, **kwargs: Any) -> "ObscateQueryEngine":
        synth = kwargs.pop("response_synthesizer", None) or get_response_synthesizer(llm=llm)
        return cls(retriever=retriever, response_synthesizer=synth, **kwargs)

    @classmethod
    def wrap(cls, query_engine: Any, **kwargs: Any) -> "ObscateQueryEngine":
        """Wrap a ``RetrieverQueryEngine`` (what ``index.as_query_engine()`` returns)."""
        try:
            return cls(retriever=query_engine.retriever,
                       response_synthesizer=query_engine._response_synthesizer,
                       node_postprocessors=list(getattr(query_engine, "_node_postprocessors", [])),
                       **kwargs)
        except AttributeError:
            raise TypeError("wrap() expects a RetrieverQueryEngine; use from_args(retriever, llm) instead")

    def _retrieve(self, query: str) -> List[NodeWithScore]:
        nodes = self.retriever.retrieve(query)
        for p in self.node_postprocessors:
            nodes = p.postprocess_nodes(nodes, query_bundle=QueryBundle(query))
        return nodes

    @staticmethod
    def _text(resp: Any) -> str:
        if isinstance(resp, StreamingResponse):
            return "".join(resp.response_gen)
        return str(resp.response if hasattr(resp, "response") else resp)

    def custom_query(self, query_str: str) -> Response:
        nodes = self._retrieve(query_str)
        red_nodes, red_query, ctx = self._redactor.redact(nodes, query_str)
        resp = self.response_synthesizer.synthesize(red_query, nodes=red_nodes)
        answer = self._redactor.restore(self._text(resp), ctx)
        return Response(response=answer, source_nodes=nodes,
                        metadata={"obscate_strategy": self.strategy})

    async def acustom_query(self, query_str: str) -> Response:
        nodes = await self.retriever.aretrieve(query_str)
        for p in self.node_postprocessors:
            nodes = await p.apostprocess_nodes(nodes, query_bundle=QueryBundle(query_str))
        red_nodes, red_query, ctx = await self._redactor.aredact(nodes, query_str)
        resp = await self.response_synthesizer.asynthesize(red_query, nodes=red_nodes)
        if isinstance(resp, StreamingResponse):
            text = "".join(resp.response_gen)
        else:
            text = str(resp.response)
        answer = await self._redactor.arestore(text, ctx)
        return Response(response=answer, source_nodes=nodes, metadata={"obscate_strategy": self.strategy})
