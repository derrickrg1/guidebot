"""Fallback order: Claude, then the local model. No real API calls."""
import time
from types import SimpleNamespace

import pydantic

from guidebot import claude_status, config, llm, usage

MSGS = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]


def claude_down(monkeypatch, out=True):
    monkeypatch.setattr(claude_status, "_status", {"at": time.time() + 10**9,
                                                   "claude": {"ok": False, "out": out, "why": "credits out"}})


def no_claude_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Claude was called while the status said it was down")
    monkeypatch.setattr(llm, "_claude_stream", boom)
    monkeypatch.setattr(llm, "_claude_json", boom)


def test_down_goes_local_not_claude(monkeypatch):
    claude_down(monkeypatch)
    no_claude_call(monkeypatch)
    monkeypatch.setattr(llm, "_ollama_stream", lambda m, model: iter(["local"]))
    usage.begin()
    out = "".join(llm.stream_chat(MSGS, "claude-sonnet-5-5"))
    assert out == llm.FALLBACK_SHOWN + "local" and usage.end()["engine"] == "local"


def test_claude_error_marks_status_stale(monkeypatch):
    import anthropic
    err = anthropic.APIConnectionError(request=SimpleNamespace(method="POST", url="x"))

    def fail(m, model):
        raise err
        yield
    monkeypatch.setattr(llm, "_claude_stream", fail)
    monkeypatch.setattr(llm, "_ollama_stream", lambda m, model: iter(["local"]))
    out = "".join(llm.stream_chat(MSGS, "claude-sonnet-5-5"))
    assert out == llm.FALLBACK_SHOWN + "local" and claude_status._status["at"] == 0.0


def test_json_fallback(monkeypatch):
    class X(pydantic.BaseModel):
        x: int
    claude_down(monkeypatch)
    no_claude_call(monkeypatch)
    monkeypatch.setattr(llm, "_ollama_json", lambda s, u, schema, model, r: schema(x=7))
    assert llm.ask_json("sys", "user", X, "claude-sonnet-5-5").x == 7


def test_claude_up_uses_claude(monkeypatch):
    monkeypatch.setattr(llm, "_claude_stream", lambda m, model: iter(["claude"]))
    assert "".join(llm.stream_chat(MSGS, "claude-sonnet-5-5")) == "claude"


def test_status_check_caches(monkeypatch):
    calls = []

    class Fake:
        class messages:
            @staticmethod
            def count_tokens(**k):
                calls.append(k)
                raise Exception("Your credit balance is too low to access the Anthropic API.")
    monkeypatch.setattr(llm, "_anthropic", lambda: Fake)
    monkeypatch.setattr(config, "CHAT_MODEL", "claude-sonnet-5-5")
    claude_status.stale()
    s = claude_status.status()
    assert s["claude"] == {"ok": False, "out": True, "why": "credits out"} and s["backup"] == "local"
    claude_status.status()
    assert len(calls) == 1   # cached for 5 minutes
    claude_status.stale()
    claude_status.status()
    assert len(calls) == 2


def test_usage_summary(monkeypatch):
    usage.record("a@x", {"engine": "gemini", "usd": 0.5})
    usage.record("b@x", {"engine": "claude", "usd": 0.25})
    assert usage.summary()["month"] == {"usd": 0.75, "runs": 2}
    assert usage.summary("a@x")["month"] == {"usd": 0.5, "runs": 1}


def test_spend_limit_counts_as_out():
    import anthropic
    from httpx import Request, Response
    msg = "You have reached your specified workspace API usage limits. You will regain access on 2026-12-01."
    e = anthropic.BadRequestError(msg, response=Response(400, request=Request("POST", "http://x")), body=None)
    assert claude_status.is_credit_error(e) and llm._down(e)
