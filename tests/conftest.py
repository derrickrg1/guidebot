import time

import pytest

from guidebot import answer_cache, claude_status, config, revo_rules, usage


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    """Tests stay offline: the Claude status check is answered "ok" from the cache (no real token count), and spend
    goes to a temp file, not data/usage.jsonl."""
    monkeypatch.setattr(claude_status, "_status", {"at": time.time() + 10**9, "claude": {"ok": True, "out": False, "why": ""},
                                                   "backup": "local", "backup_model": config.LOCAL_MODEL,
                                                   "model": config.CHAT_MODEL})
    monkeypatch.setattr(usage, "LOG", tmp_path / "usage.jsonl")
    # the answer cache and Revo's rules live in temp files too, so a test never reads or writes the real ones
    monkeypatch.setattr(answer_cache, "DB", tmp_path / "answer_cache.db")
    monkeypatch.setattr(revo_rules, "FILE", tmp_path / "revo_overlays.json")
