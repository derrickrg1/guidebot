"""Credits out: Claude isn't called again until a check gets through; the balance estimate counts down."""
import datetime as dt
from types import SimpleNamespace

import pytest

from guidebot import claude_status, llm


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_status, "FILE", tmp_path / "claude_status.json")


def test_out_skips_claude(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "_claude_stream", lambda m, model: calls.append(model) or iter(["claude"]))
    monkeypatch.setattr(llm, "_ollama_stream", lambda m, model: iter(["local"]))
    claude_status.mark_out("Your credit balance is too low")
    out = "".join(llm.stream_chat([{"role": "user", "content": "hi"}], "claude-sonnet-5-5"))
    assert calls == [] and out == llm.FALLBACK_SHOWN + "local"
    claude_status._update(out=False)
    assert "".join(llm.stream_chat([{"role": "user", "content": "hi"}], "claude-sonnet-5-5")) == "claude"


def test_spend_counts_down():
    claude_status.set_balance(10)
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0)
    claude_status.spend("claude-sonnet-5-5", usage)   # $2 in + $1 out
    assert claude_status.load()["balance"] == pytest.approx(7.0)
    claude_status.mark_out("credit balance")
    assert claude_status.load()["balance"] == 0.0


def test_next_check_hours():
    at = lambda h, m=0: dt.datetime(2026, 11, 13, h, m)
    assert claude_status.next_check(at(8, 30)) == at(9)
    assert claude_status.next_check(at(9)) == at(12)
    assert claude_status.next_check(at(13)) == at(15)
    assert claude_status.next_check(at(16)) == dt.datetime(2026, 11, 14, 9)
