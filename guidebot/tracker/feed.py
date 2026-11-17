"""The "What changed" feed: data/agency_changes.jsonl, one JSON object per agency release, oldest first.

Schema (the in-app feed and the regression rerun read it, so keep it exact):
  id, agency ("FHA"|"Fannie"|"Freddie"|"VA"), title, url, published (ISO date), effective (ISO date or null),
  summary, changes: [{topic, old, new, section, effective}], fetched_at

data/tracker_state.json remembers releases looked at and left out of the feed (servicing only, nothing for an
originator), so they aren't summarized again every week.
"""
import json
import threading
import time
from datetime import datetime

from guidebot import config

FEED = config.ROOT / "data" / "agency_changes.jsonl"
STATE = config.ROOT / "data" / "tracker_state.json"
AGENCIES = ("FHA", "Fannie", "Freddie", "VA")
FIELDS = ("id", "agency", "title", "url", "published", "effective", "summary", "changes", "fetched_at")
CHANGE_FIELDS = ("topic", "old", "new", "section", "effective")
_lock = threading.Lock()


def read() -> list[dict]:
    """Every release in the feed, oldest first. A repeated id (two writers at once) keeps the last copy."""
    if not FEED.exists():
        return []
    out = {}
    for line in FEED.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue   # a half-written last line
        out.pop(rec.get("id"), None)
        out[rec.get("id")] = rec
    return list(out.values())


def ids() -> set[str]:
    return {r["id"] for r in read()}


def load_state() -> dict:
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    state.setdefault("skipped", {})
    state.setdefault("errors", {})
    state.setdefault("last_poll", 0)
    return state


def save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def known(release_id: str) -> bool:
    """In the feed already, or looked at and left out."""
    return release_id in ids() or release_id in load_state()["skipped"]


def skip(release_id: str, title: str, why: str):
    with _lock:
        state = load_state()
        state["skipped"][release_id] = {"title": title, "why": why, "at": now()}
        state["errors"].pop(release_id, None)
        save_state(state)


def error(release_id: str, title: str, why: str):
    with _lock:
        state = load_state()
        state["errors"][release_id] = {"title": title, "why": str(why)[:300], "at": now()}
        save_state(state)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _no_dashes(s):
    """No em or en dashes anywhere in what LOs read (Derrick's rule)."""
    return s.replace(" — ", ", ").replace("—", ", ").replace("–", "-") if isinstance(s, str) else s


def shape(rec: dict) -> dict:
    """The record with exactly the feed's fields, in order, and nothing else."""
    if rec.get("agency") not in AGENCIES:
        raise ValueError(f"agency must be one of {AGENCIES}, not {rec.get('agency')!r}")
    out = {k: _no_dashes(rec.get(k)) for k in FIELDS}
    out["changes"] = [{k: _no_dashes(c.get(k)) for k in CHANGE_FIELDS} for c in rec.get("changes") or []]
    out["fetched_at"] = out["fetched_at"] or now()
    return out


def append(rec: dict) -> dict:
    rec = shape(rec)
    with _lock:
        if rec["id"] in ids():
            return rec
        FEED.parent.mkdir(parents=True, exist_ok=True)
        with FEED.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        state = load_state()
        if state["errors"].pop(rec["id"], None):
            save_state(state)
    return rec


def touch_poll():
    with _lock:
        state = load_state()
        state["last_poll"] = time.time()
        save_state(state)
