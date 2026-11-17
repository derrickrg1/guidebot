"""Regression check when a guide changes (2026-11-16): reruns the stress cases for that tab and flags any case that
was clean last time and has a problem now, so one new sheet or handbook update can't quietly break answers.

Runs inside the server on its own thread (never a second process), posting to the server like tools/stress_run.py
does, graded by tools/stress_check.py. No chat id goes with the questions, so nothing is saved or cached.

What triggers it: updates.py seeing a new or changed NDM sheet, or a new agency guide update. Deal cases (Python
matrix lookups) always run. Guideline cases call the model and cost money, so they only run when
GUIDEBOT_AUTO_REGRESS_PAID=1 is in .env, or when Derrick clicks "Run all" on the admin Updates tab.

Results: data/regress/runs.json (newest first) and data/regress/last.json (each case's problems, the baseline the
next run compares to). Each run also writes data/regress/<when>-<section>.md.
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from guidebot import config

DIR = config.ROOT / "data" / "regress"
RUNS = DIR / "runs.json"
LAST = DIR / "last.json"
SEEN = DIR / "seen.json"
_lock = threading.Lock()
_running: dict = {}


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, obj):
    DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1), encoding="utf-8")


def cases(section: str, paid: bool) -> list[dict]:
    from tools.stress_bank import bank
    return [c for c in bank() if c["section"] == section and not c["id"].startswith("fb-")
            and (paid or c.get("mode") == "deal" or (c.get("expect") or {}).get("route") in ("deal", "pick", "missing"))]


def run(section: str, why: str, paid: bool | None = None, log=print) -> dict:
    """Reruns the section's cases and compares each to its last result. Returns the run's summary."""
    from tools import stress_check, stress_run
    paid = os.environ.get("GUIDEBOT_AUTO_REGRESS_PAID") == "1" if paid is None else paid
    todo = cases(section, paid)
    if not todo:
        return {"section": section, "why": why, "cases": 0}
    with _lock:
        if _running.get(section):
            return {"section": section, "why": why, "skipped": "already running"}
        _running[section] = time.time()
    try:
        log(f"Regression check: {len(todo)} {section} cases ({why})")
        with ThreadPoolExecutor(3) as pool:
            results = list(pool.map(stress_run.ask, todo))
        base = _read(LAST, {})
        broke, fixed, still, out = [], [], 0, {}
        for r in results:
            probs = stress_check.check(r)
            out[r["id"]] = probs
            before = base.get(r["id"])
            if probs and before == []:
                broke.append({"id": r["id"], "q": r["q"], "problems": probs[:4]})
            elif probs:
                still += 1
            elif before:
                fixed.append(r["id"])
        _write(LAST, base | out)
        when = time.strftime("%Y-%m-%d %H:%M")
        summary = {"when": when, "section": section, "why": why, "cases": len(results), "paid": paid,
                   "clean": sum(not p for p in out.values()), "broke": broke, "fixed": fixed, "still": still}
        runs = _read(RUNS, [])
        _write(RUNS, ([summary] + runs)[:60])
        lines = [f"# Regression check {when}, {section}", f"Why: {why}", "",
                 f"{summary['clean']}/{len(results)} clean, {len(broke)} newly broken, {len(fixed)} fixed, "
                 f"{still} still failing", ""]
        for b in broke:
            lines += [f"## {b['id']}", f"**Q:** {b['q']}", ""] + [f"- {p}" for p in b["problems"]] + [""]
        (DIR / f"{when.replace(':', '').replace(' ', '-')}-{section}.md").write_text("\n".join(lines), encoding="utf-8")
        log(f"Regression check done: {summary['clean']}/{len(results)} clean, {len(broke)} newly broken")
        return summary
    finally:
        with _lock:
            _running.pop(section, None)


def start(section: str, why: str, paid: bool | None = None, log=print):
    threading.Thread(target=run, args=(section, why, paid, log), daemon=True).start()


def overview() -> dict:
    return {"runs": _read(RUNS, [])[:20], "running": sorted(_running)}


# ---------- what triggers it ----------

def _changes(sections: dict) -> dict:
    """{section: why} for guide changes not yet checked: NDM sheet events (updates.py) and agency updates."""
    from guidebot import agency, updates
    seen = _read(SEEN, None)
    sheets = _read(updates.SEEN, {}).get("events", [])
    state = agency.load_state()
    now = {"sheets": [f"{e['date']}|{e['file']}" for e in sheets], "agency": [u["id"] for u in state["updates"]]}
    if seen is None:   # first look: today's state is the baseline, nothing to rerun
        _write(SEEN, now)
        return {}
    out = {}
    for e in sheets:
        if f"{e['date']}|{e['file']}" not in seen["sheets"]:
            sec = next((s for s, test in sections.items() if test(e["program"])), "")
            if sec:
                out.setdefault(sec, f"{e['program']} sheet {e['kind']}: {e['file']}")
    for u in state["updates"]:
        if u["id"] not in seen["agency"] and u["kind"] in ("new", "changed"):
            sec = u["file"].split("/", 1)[0]
            if sec in sections:
                out.setdefault(sec, f"agency update: {u['title']}")
    _write(SEEN, now)
    return out


def watch(sections: dict, log=print, every: int = 3600):
    """Server thread: once an hour, reruns the cases for any tab whose guides changed since the last look."""
    def loop():
        time.sleep(15 * 60)
        while True:
            try:
                from guidebot import updates
                for sec in sections:   # brings sheets_seen.json up to date
                    updates.recent(sec, sections)
                for sec, why in _changes(sections).items():
                    run(sec, why, log=log)
            except Exception as e:
                log(f"Regression watch: {type(e).__name__}: {e}")
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()
