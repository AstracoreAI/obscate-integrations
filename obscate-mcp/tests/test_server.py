import json
import logging
import os
import sys
from pathlib import Path

import pytest
from mcp import Client

sys.path.insert(0, str(Path(__file__).parent))
from fake_api import FakeObscate  # noqa: E402

from obscate_mcp._client import ObscateClient  # noqa: E402
from obscate_mcp.server import build_server  # noqa: E402

pytestmark = pytest.mark.anyio

SECRET_TEXT = "Ravi Kumar (ravi@example.com, +91 98765 43210) asked about his order."
RAW_VALUES = ["Ravi Kumar", "ravi@example.com", "98765 43210"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make(fake=None, key="ob_test_key"):
    fake = fake or FakeObscate()
    t = fake.transport()
    client = ObscateClient(api_key=key, base_url="https://fake.obscate.test", transport=t, async_transport=t)
    return fake, build_server(client)


def data(result):
    assert not result.is_error, result.content
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


async def test_lists_all_tools():
    _, server = make()
    async with Client(server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert {"redact", "restore", "detect", "redact_json", "restore_json", "list_entities"} <= names


async def test_redact_restore_fpe_roundtrip_without_map():
    fake, server = make()
    async with Client(server) as c:
        r = data(await c.call_tool("redact", {"text": SECRET_TEXT}))
        assert r["strategy"] == "fpe" and "token_map" not in r
        for v in RAW_VALUES:
            assert v not in r["redacted"]
        # originals are not echoed back into the model context
        assert all("text" not in e for e in r["entities"])
        back = data(await c.call_tool("restore", {"text": r["redacted"]}))
    assert back["text"] == SECRET_TEXT
    assert fake.calls[-1][0] == "/v1/restore-fpe"


async def test_tokenize_roundtrip_needs_map():
    _, server = make()
    async with Client(server) as c:
        r = data(await c.call_tool("redact", {"text": SECRET_TEXT, "strategy": "tokenize"}))
        assert "[EMAIL_1]" in r["redacted"]
        missing = await c.call_tool("restore", {"text": r["redacted"], "strategy": "tokenize"})
        assert missing.is_error
        back = data(await c.call_tool("restore", {"text": "Reply to [EMAIL_1]", "strategy": "tokenize",
                                                  "token_map": r["token_map"]}))
    assert back["text"] == "Reply to ravi@example.com"


async def test_redact_json_and_restore_json():
    _, server = make()
    payload = {"customer": {"name": "Ravi Kumar", "email": "ravi@example.com"}, "qty": 3}
    async with Client(server) as c:
        r = data(await c.call_tool("redact_json", {"json": payload}))
        assert r["redacted"]["qty"] == 3
        assert "ravi@example.com" not in json.dumps(r["redacted"])
        reply = {"draft": f"Hi {r['redacted']['customer']['name']}"}
        back = data(await c.call_tool("restore_json", {"json": reply, "type_changes": r["type_changes"]}))
    assert back["json"] == {"draft": "Hi Ravi Kumar"}


async def test_detect_hides_values_by_default():
    _, server = make()
    async with Client(server) as c:
        r = data(await c.call_tool("detect", {"text": SECRET_TEXT}))
        r2 = data(await c.call_tool("detect", {"text": SECRET_TEXT, "include_values": True}))
    assert r["counts"]["by_label"] == {"PERSON": 1, "EMAIL": 1, "PHONE": 1}
    assert "ravi@example.com" not in json.dumps(r)
    assert "ravi@example.com" in json.dumps(r2)


async def test_one_way_strategy_cannot_be_restored():
    _, server = make()
    async with Client(server) as c:
        res = await c.call_tool("restore", {"text": "x", "strategy": "mask"})
    assert res.is_error


async def test_missing_key_gives_actionable_error():
    _, server = make(key="")
    async with Client(server) as c:
        res = await c.call_tool("redact", {"text": SECRET_TEXT})
    assert res.is_error and "OBSCATE_API_KEY" in res.content[0].text


@pytest.mark.parametrize("status,needle", [(401, "OBSCATE_API_KEY"), (402, "quota"), (429, "retry after 7"),
                                           (503, "failed closed")])
async def test_api_errors_are_reported_without_payload(status, needle):
    fake = FakeObscate(fail_with=status)
    _, server = make(fake)
    async with Client(server) as c:
        res = await c.call_tool("redact", {"text": SECRET_TEXT})
    assert res.is_error
    msg = res.content[0].text
    assert needle in msg and str(status) in msg
    for v in RAW_VALUES:
        assert v not in msg


async def test_base_url_env_is_honoured(monkeypatch):
    monkeypatch.setenv("OBSCATE_BASE_URL", "https://obscate.internal.example/")
    assert ObscateClient().base_url == "https://obscate.internal.example"
    monkeypatch.delenv("OBSCATE_BASE_URL")
    assert ObscateClient().base_url == "https://api.obscate.com"


async def test_privacy_nothing_sensitive_is_logged_or_written(caplog, tmp_path, monkeypatch, capsys):
    """The privacy promise: no raw text, detected values or token map in logs,
    stdout/stderr, or files — on success AND on failure paths."""
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.DEBUG)  # every logger, every level
    for fail in (None, 422, 503):
        _, server = make(FakeObscate(fail_with=fail))
        async with Client(server) as c:
            r = await c.call_tool("redact", {"text": SECRET_TEXT, "strategy": "tokenize"})
            await c.call_tool("detect", {"text": SECRET_TEXT})
            await c.call_tool("redact_json", {"json": {"name": "Ravi Kumar", "email": "ravi@example.com"}})
            if not r.is_error:
                await c.call_tool("restore", {"text": data(r)["redacted"], "strategy": "tokenize",
                                              "token_map": data(r)["token_map"]})
            await c.call_tool("restore", {"text": "[EMAIL_1]", "strategy": "tokenize",
                                          "token_map": {"[EMAIL_1]": "ravi@example.com"}})
    logged = "\n".join(rec.getMessage() + repr(rec.args) for rec in caplog.records)
    out = capsys.readouterr()
    for v in RAW_VALUES + ["[EMAIL_1]"]:
        assert v not in logged, f"{v!r} leaked into logs"
        assert v not in out.out + out.err
    assert list(tmp_path.iterdir()) == []
    assert "ob_test_key" not in repr(ObscateClient(api_key="ob_test_key"))


async def test_key_version_is_returned_and_restores_after_rotation():
    """A server-side key rotation between redact and restore must not break the
    round trip: redact returns key_version, restore sends it back."""
    fake, server = make()
    async with Client(server) as c:
        r = data(await c.call_tool("redact", {"text": SECRET_TEXT}))
        rj = data(await c.call_tool("redact_json", {"json": {"note": SECRET_TEXT}}))
        assert r["key_version"] == rj["key_version"] == 1
        fake.key_version = 2                                   # rotation happens mid-flight
        back = data(await c.call_tool("restore", {"text": r["redacted"], "key_version": 1}))
        jback = data(await c.call_tool("restore_json", {"json": rj["redacted"], "key_version": 1}))
        stale = data(await c.call_tool("restore", {"text": r["redacted"]}))
    assert back["text"] == SECRET_TEXT and jback["json"] == {"note": SECRET_TEXT}
    assert stale["text"] != SECRET_TEXT                        # proves the version mattered


def test_restore_context_merge_respects_key_version():
    from obscate_mcp._client import RestoreContext
    a = RestoreContext("tokenize", {"[A_1]": "x"}, key_version=1)
    assert a.merge(RestoreContext("tokenize", {"[B_1]": "y"})).key_version == 1
    with pytest.raises(ValueError):
        a.merge(RestoreContext("tokenize", key_version=2))
