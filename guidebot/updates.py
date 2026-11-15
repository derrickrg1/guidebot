"""What changed lately, for the banner over the chat (2026-11-15).

Two sources, both by date only. Nothing here says what a change means; that's for the reader to open.
  * NDM sheets in docs/: data/sheets_seen.json keeps each file's hash. The first run records what's there as the
    baseline (no news). After that a new file or a changed hash is an update, dated the day the server saw it.
  * Agency guides (agency.py's state): new and changed files, except the first load (its earliest date), when all
    127 files arrived at once.
"""
import json
import threading
import time
from datetime import date, timedelta

from guidebot import agency, config, library

SEEN = config.ROOT / "data" / "sheets_seen.json"
DAYS = 30
_lock = threading.Lock()


def _sheets(today: str) -> list[dict]:
    """Updates to NDM sheets, after bringing the seen-file up to date."""
    with _lock:
        seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else None
        first = seen is None
        seen = seen or {"files": {}, "events": []}
        dirty = first
        for prog, paths in library.programs().items():
            for path in library.unique_docs(paths)[0]:
                rel = path.relative_to(config.ROOT).as_posix()
                st = path.stat()
                rec = seen["files"].get(rel)
                if rec and rec["mtime"] == st.st_mtime and rec["size"] == st.st_size:
                    continue
                h = library.file_hash(path)
                if not rec or rec["hash"] != h:
                    if not first:
                        seen["events"].insert(0, {"date": today, "program": prog, "file": path.name,
                                                  "kind": "changed" if rec else "new"})
                seen["files"][rel] = {"hash": h, "mtime": st.st_mtime, "size": st.st_size}
                dirty = True
        if dirty:
            del seen["events"][200:]
            SEEN.parent.mkdir(parents=True, exist_ok=True)
            SEEN.write_text(json.dumps(seen, indent=1), encoding="utf-8")
        return seen["events"]


def recent(section: str, sections: dict, days: int = DAYS, today: date | None = None) -> list[dict]:
    """Updates from the last `days` days that belong to this tab, newest first."""
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    out = [{"id": f"sheet-{e['date']}-{e['file']}", "date": e["date"], "title": f"{e['program']} sheet",
            "kind": e["kind"], "file": e["file"]}
           for e in _sheets(today.isoformat())
           if e["date"] >= since and sections.get(section, lambda p: False)(e["program"])]
    state = agency.load_state()
    first = min((u["date"] for u in state["updates"]), default="")
    out += [{"id": u["id"], "date": u["date"], "title": u["title"], "kind": u["kind"], "file": u["file"].rsplit("/", 1)[-1]}
            for u in state["updates"]
            if u["date"] >= since and u["date"] != first and u["kind"] in ("new", "changed")
            and u["file"].split("/", 1)[0] == section]
    return sorted(out, key=lambda u: u["date"], reverse=True)[:6]
