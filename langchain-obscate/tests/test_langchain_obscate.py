import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool

sys.path.insert(0, str(Path(__file__).parent))
from fake_api import FakeObscate  # noqa: E402

from langchain_obscate import (ObscateClient, ObscateDetectTool, ObscateError, ObscateRedaction,  # noqa: E402
                               ObscateRedactor, ObscateRedactTool, with_obscate)

SECRET = "I am Ravi Kumar, email ravi@example.com, phone +91 98765 43210."
RAW = ["Ravi Kumar", "ravi@example.com", "98765 43210"]


class RecordingModel(BaseChatModel):
    """Fake LLM: records exactly what it was sent, echoes the last message, and
    (optionally) asks to call a tool with a value copied from the input."""
    seen: list = []
    tool_once: bool = False

    @property
    def _llm_type(self) -> str:
        return "recording"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.seen.append([m.model_copy() for m in messages])
        last = messages[-1]
        text = last.content if isinstance(last.content, str) else json.dumps(last.content)
        if self.tool_once and not any(isinstance(m, ToolMessage) for m in messages):
            email = next(w.strip(",.") for w in text.split() if "@" in w or w.startswith("[EMAIL"))
            msg = AIMessage(content="", tool_calls=[{"name": "send_email", "args": {"to": email},
                                                     "id": "call_1", "type": "tool_call"}])
        else:
            msg = AIMessage(content=f"Echo: {text}")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def seen_text(model) -> str:
    return json.dumps([[m.model_dump() for m in call] for call in model.seen], default=str)


@pytest.fixture
def fake():
    return FakeObscate()


@pytest.fixture
def client(fake):
    t = fake.transport()
    return ObscateClient(api_key="ob_test_key", base_url="https://fake.obscate.test", transport=t,
                         async_transport=t)


@pytest.fixture
def model():
    return RecordingModel(seen=[])


@pytest.mark.parametrize("strategy", ["fpe", "tokenize", "synthetic"])
def test_runnable_redacts_in_restores_out(client, model, strategy):
    safe = ObscateRedaction(model, strategy=strategy, client=client)
    out = safe.invoke([SystemMessage("You help Ravi Kumar."), HumanMessage(SECRET)])
    for v in RAW:
        assert v not in seen_text(model), f"LLM saw {v!r}"
    assert out.content == f"Echo: {SECRET}"


def test_runnable_one_api_request_per_model_call(client, model, fake):
    ObscateRedaction(model, client=client).invoke([HumanMessage(SECRET), AIMessage("ok"), HumanMessage(SECRET)])
    paths = [p for p, _ in fake.calls]
    assert paths.count("/v1/redact-json") == 1   # one metered unit for the whole conversation


def test_tokenize_tokens_consistent_across_messages(client, model):
    ObscateRedaction(model, strategy="tokenize", client=client).invoke(
        [HumanMessage("mail ravi@example.com"), HumanMessage("again ravi@example.com")])
    sent = model.seen[0]
    assert sent[0].content == "mail [EMAIL_1]" and sent[1].content == "again [EMAIL_1]"


def test_chain_with_prompt_variables_and_str_output(client, model):
    prompt = ChatPromptTemplate.from_messages([("human", "Write to {name} at {email}")])
    chain = with_obscate(prompt | model | StrOutputParser(), client=client)
    out = chain.invoke({"name": "Ravi Kumar", "email": "ravi@example.com"})
    assert "ravi@example.com" not in seen_text(model)
    assert out == "Echo: Write to Ravi Kumar at ravi@example.com"


def test_wrapping_just_the_llm_inside_a_chain(client, model):
    prompt = ChatPromptTemplate.from_messages([("human", "{q}")])
    chain = prompt | ObscateRedaction(model, client=client) | StrOutputParser()
    assert chain.invoke({"q": SECRET}) == f"Echo: {SECRET}"
    assert "ravi@example.com" not in seen_text(model)


def test_plain_string_input(client, model):
    out = ObscateRedaction(model, client=client).invoke(SECRET)
    assert out.content == f"Echo: {SECRET}"
    assert "ravi@example.com" not in seen_text(model)


def test_list_content_blocks_and_images_untouched(client, model):
    img = {"type": "image_url", "image_url": {"url": "https://x.test/a.png"}}
    msg = HumanMessage(content=[{"type": "text", "text": SECRET}, img])
    red, ctx = ObscateRedactor(client=client).redact_messages([msg])
    assert red[0].content[1] == img
    assert "ravi@example.com" not in red[0].content[0]["text"]


def test_one_way_strategy_is_not_restored(client, model):
    out = ObscateRedaction(model, strategy="mask", client=client).invoke(SECRET)
    assert "ravi@example.com" not in out.content


def test_fails_closed_when_api_errors(model):
    fake = FakeObscate(fail_with=503)
    t = fake.transport()
    c = ObscateClient(api_key="ob_test_key", base_url="https://fake.obscate.test", transport=t)
    with pytest.raises(ObscateError) as ei:
        ObscateRedaction(model, client=c).invoke(SECRET)
    assert model.seen == []           # the LLM was never called
    assert "ravi" not in str(ei.value).lower()


def test_async_path(client, model):
    import asyncio
    out = asyncio.run(ObscateRedaction(model, client=client).ainvoke([HumanMessage(SECRET)]))
    assert out.content == f"Echo: {SECRET}"
    assert "ravi@example.com" not in seen_text(model)


def test_agent_middleware_redacts_model_but_tools_get_real_values(client):
    pytest.importorskip("langchain.agents")
    from langchain.agents import create_agent
    from langchain_obscate import ObscateMiddleware

    received = []

    @tool
    def send_email(to: str) -> str:
        """Send an email."""
        received.append(to)
        return f"sent to {to}"

    model = RecordingModel(seen=[], tool_once=True)
    agent = create_agent(model, tools=[send_email], system_prompt="Assist Ravi Kumar.",
                         middleware=[ObscateMiddleware(client=client)])
    result = agent.invoke({"messages": [HumanMessage(SECRET)]})
    assert received == ["ravi@example.com"]                    # tool got the real value
    for v in RAW:
        assert v not in seen_text(model), f"LLM saw {v!r}"   # incl. tool result + system prompt
    assert len(model.seen) == 2
    assert "ravi@example.com" in result["messages"][-1].content  # user gets restored answer


def test_tools(client):
    assert "ravi@example.com" not in ObscateRedactTool(client=client).invoke({"text": SECRET})
    summary = json.loads(ObscateDetectTool(client=client).invoke({"text": SECRET}))
    assert summary == {"total": 3, "by_label": {"EMAIL": 1, "PHONE": 1, "PERSON": 1}}


def test_privacy_nothing_sensitive_logged_or_written(client, model, caplog, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.DEBUG)
    for strategy in ("fpe", "tokenize"):
        r = ObscateRedactor(strategy=strategy, client=client)
        red, ctx = r.redact_messages([HumanMessage(SECRET)])
        r.restore_message(AIMessage(red[0].content), ctx)
        assert "ravi@example.com" not in repr(ctx) and "ravi@example.com" not in repr(r)
    bad = ObscateClient(api_key="nope", base_url="https://fake.obscate.test",
                        transport=FakeObscate().transport())
    with pytest.raises(ObscateError):
        ObscateRedaction(model, client=bad).invoke(SECRET)
    logged = "\n".join(rec.getMessage() + repr(rec.args) for rec in caplog.records)
    out = capsys.readouterr()
    for v in RAW + ["[EMAIL_1]"]:
        assert v not in logged and v not in out.out + out.err
    assert list(tmp_path.iterdir()) == []
    assert "nope" not in repr(bad)


@pytest.mark.parametrize("as_messages", [True, False])
def test_key_version_carried_through_round_trip(client, model, fake, as_messages):
    """Keys rotate on the server while the LLM is thinking: the wrapper must
    restore with the version the redaction was made under, with no developer code."""
    from langchain_core.runnables import RunnableLambda

    def rotating_llm(x):
        fake.key_version = 2
        return model.invoke(x) if as_messages else f"Echo: {x}"
    safe = ObscateRedaction(RunnableLambda(rotating_llm), client=client)
    out = safe.invoke([HumanMessage(SECRET)] if as_messages else SECRET)
    assert (out.content if as_messages else out) == f"Echo: {SECRET}"
    restore_calls = [b for p, b in fake.calls if p.startswith("/v1/restore")]
    assert restore_calls and all(b.get("key_version") == 1 for b in restore_calls)
