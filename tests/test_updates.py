"""The "What's new" banner: NDM sheet changes after the first look, agency files after their first load."""
import json
import os
from datetime import date

from guidebot import updates


def test_first_look_is_the_baseline_then_changes_are_news(tmp_path, monkeypatch):
    sheet = tmp_path / "docs" / "Ignite DSCR" / "a.pdf"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"one")
    monkeypatch.setattr(updates.config, "ROOT", tmp_path)
    monkeypatch.setattr(updates, "SEEN", tmp_path / "seen.json")
    monkeypatch.setattr(updates.library, "programs", lambda: {"Ignite DSCR": [sheet]})
    monkeypatch.setattr(updates.agency, "load_state", lambda: {"updates": [
        {"id": "a1", "date": "2026-11-12", "title": "First load", "kind": "new", "file": "FHA/x.pdf"},
        {"id": "a2", "date": "2026-11-14", "title": "ML 2026-12", "kind": "new", "file": "FHA/ml.pdf"}]})
    sections = {"NonQM": lambda p: p.startswith("Ignite"), "FHA": lambda p: p.startswith("FHA")}
    today = date(2026, 11, 15)
    assert updates.recent("NonQM", sections, today=today) == []          # what's there on the first look isn't news
    sheet.write_bytes(b"two")
    os.utime(sheet, (sheet.stat().st_atime, sheet.stat().st_mtime + 5))   # same size: two writes in one clock tick look unchanged
    news = updates.recent("NonQM", sections, today=today)
    assert [(u["title"], u["kind"], u["date"]) for u in news] == [("Ignite DSCR sheet", "changed", "2026-11-15")]
    assert [u["title"] for u in updates.recent("FHA", sections, today=today)] == ["ML 2026-12"]   # not the first load
    assert len(json.loads((tmp_path / "seen.json").read_text())["events"]) == 1
