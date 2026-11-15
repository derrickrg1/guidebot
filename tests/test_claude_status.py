"""Fallback order: Claude, then Gemini, then the local model. Gemini here is always a fake client: no real API calls,
no real files."""
import time
from types import SimpleNamespace

import pydantic
import pytest

from guidebot import claude_status, config, gemini, llm, usage

MSGS = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]


def claude_down(monkeypatch, out=True):
    monkeypatch.setattr(claude_status, "_status", {"at": time.time() + 10**9,
                                                   "claude": {"ok": False, "out": out, "why": "credits out"}})


class FakeGemini:
    """Stands in for genai.Client: records what it was sent, answers from a script."""
    def __init__(self, chunks=("Gem", "ini"), fail=False, json_text='{"x": 7}'):
        self.sent, self.chunks, self.fail, self.json_text = [], chunks, fail, json_text
        self.models = self

    def _meta(self):
        return SimpleNamespace(prompt_token_count=1_000_000, cached_content_token_count=0,
                               candidates_token_count=100_000, thoughts_token_count=100_000)

    def generate_content_stream(self, model, contents, config):
        self.sent.append((model, contents, config))
        if self.fail:
            raise ConnectionError("gemini down")
        for i, c in enumerate(self.chunks):
            yield SimpleNamespace(text=c, usage_metadata=self._meta() if i == len(self.chunks) - 1 else None)

    def generate_content(self, model, contents, config):
        self.sent.append((model, contents, config))
        if self.fail:
            raise ConnectionError("gemini down")
        return SimpleNamespace(text=self.json_text, usage_metadata=self._meta(), candidates=[], prompt_feedback=None)


def gemini_on(monkeypatch, fake):
    monkeypatch.setattr(config, "GEMINI_FALLBACK", True)
    monkeypatch.setattr(config, "GEMINI_KEY", "test-key")
    monkeypatch.setattr(gemini, "_client", lambda: fake)


def no_claude_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Claude was called while the status said it was down")
    monkeypatch.setattr(llm, "_claude_stream", boom)
    monkeypatch.setattr(llm, "_claude_json", boom)


def test_down_goes_to_gemini_not_claude(monkeypatch):
    fake = FakeGemini()
    gemini_on(monkeypatch, fake)
    claude_down(monkeypatch)
    no_claude_call(monkeypatch)
    usage.begin()
    out = "".join(llm.stream_chat(MSGS, "claude-sonnet-5-5"))
    run = usage.end()
    assert out == llm.FALLBACK_SHOWN + "Gemini"
    system = fake.sent[0][2].system_instruction
    assert system == "rules"   # the same system prompt Claude gets
    # $0.75/M in, $3.75/M out, thinking bills as output: 0.75 + 0.2M * 3.75 = $1.50
    assert run["engine"] == "gemini" and run["model"] == "gemini-3.8-flash" and run["usd"] == pytest.approx(1.50)


def test_gemini_failure_falls_to_local(monkeypatch):
    gemini_on(monkeypatch, FakeGemini(fail=True))
    claude_down(monkeypatch)
    no_claude_call(monkeypatch)
    monkeypatch.setattr(llm, "_ollama_stream", lambda m, model: iter(["local"]))
    usage.begin()
    out = "".join(llm.stream_chat(MSGS, "claude-sonnet-5-5"))
    assert out == llm.FALLBACK_SHOWN + "local" and usage.end()["engine"] == "local"


def test_gemini_off_goes_local(monkeypatch):
    claude_down(monkeypatch)
    no_claude_call(monkeypatch)
    monkeypatch.setattr(llm, "_ollama_stream", lambda m, model: iter(["local"]))
    assert "".join(llm.stream_chat(MSGS, "claude-sonnet-5-5")) == llm.FALLBACK_SHOWN + "local"


def test_claude_error_marks_status_stale(monkeypatch):
    import anthropic
    gemini_on(monkeypatch, FakeGemini())
    err = anthropic.APIConnectionError(request=SimpleNamespace(method="POST", url="x"))

    def fail(m, model):
        raise err
        yield
    monkeypatch.setattr(llm, "_claude_stream", fail)
    out = "".join(llm.stream_chat(MSGS, "claude-sonnet-5-5"))
    assert out == llm.FALLBACK_SHOWN + "Gemini" and claude_status._status["at"] == 0.0


def test_json_fallback(monkeypatch):
    class X(pydantic.BaseModel):
        x: int
    fake = FakeGemini()
    gemini_on(monkeypatch, fake)
    claude_down(monkeypatch)
    no_claude_call(monkeypatch)
    assert llm.ask_json("sys", "user", X, "claude-sonnet-5-5").x == 7
    cfg = fake.sent[0][2]
    assert cfg.response_mime_type == "application/json" and cfg.response_json_schema == X.model_json_schema()


def test_claude_up_uses_claude(monkeypatch):
    gemini_on(monkeypatch, FakeGemini())
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
