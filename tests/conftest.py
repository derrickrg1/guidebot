import pytest

from guidebot import claude_status


@pytest.fixture(autouse=True)
def _claude_on(tmp_path, monkeypatch):
    """Tests never read the server's real "credits out" state (data/claude_status.json)."""
    monkeypatch.setattr(claude_status, "FILE", tmp_path / "claude_status.json")
