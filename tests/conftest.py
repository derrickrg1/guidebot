import time

import pytest

from guidebot import claude_status, config, usage


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    """Tests stay offline: Gemini is off unless a test turns it on with a fake client, the Claude status check is
    answered "ok" from the cache (no real token count), and spend goes to a temp file, not data/usage.jsonl."""
    monkeypatch.setattr(config, "GEMINI_FALLBACK", False)
    monkeypatch.setattr(claude_status, "_status", {"at": time.time() + 10**9, "claude": {"ok": True, "out": False, "why": ""},
                                                   "backup": "local", "backup_model": config.LOCAL_MODEL,
                                                   "model": config.CHAT_MODEL})
    monkeypatch.setattr(usage, "LOG", tmp_path / "usage.jsonl")
