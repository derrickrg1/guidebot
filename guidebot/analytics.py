"""Usage analytics for the admin Usage tab (2026-11-16): what people ask most, where answers come back empty or get
marked wrong, and who uses it. Read only, from the traces and feedback tables in data/chats.db and the answer cache.

Stress and regression runs post with no chat id; they're left out so the numbers are real people only.
"""
import json
import re
import time
from collections import Counter, defaultdict

from guidebot import answer_cache, history

ATT = "\n\n[Attachment: "


def _q(question: str) -> str:
    """The typed question only (attachment text cut off), for grouping."""
    return question.split(ATT, 1)[0].strip()


def report(days: int = 30) -> dict:
    since = time.time() - days * 86400
    with history._db() as con:
        traces = [dict(r) for r in con.execute(
            "SELECT * FROM traces WHERE ts >= ? AND chat_id != '' ORDER BY ts DESC", (since,))]
        feedback = [dict(r) for r in con.execute(
            "SELECT ts, email, question, note, program, section, mode FROM feedback WHERE ts >= ? ORDER BY ts DESC",
            (since,))]
    total = len(traces)
    groups: dict = defaultdict(lambda: {"n": 0, "last": 0, "people": set(), "q": ""})
    for t in traces:
        q = _q(t["question"])
        g = groups[answer_cache.norm(q)]
        g["n"] += 1
        g["people"].add(t["email"])
        if t["ts"] > g["last"]:
            g["last"], g["q"] = t["ts"], q
    top = sorted(groups.values(), key=lambda g: (-g["n"], -g["last"]))[:25]

    # A guideline answer with no citations is the likeliest gap: nothing in the guides answered it.
    gaps = [{"ts": t["ts"], "q": _q(t["question"]), "program": t["program"], "email": t["email"]}
            for t in traces if t["mode"] == "guidelines" and json.loads(t["sources"] or "[]") == []
            and t["model"] != "cache" and t["answer_chars"] < 1500][:25]
    by_program = Counter(t["program"] or "All" for t in traces).most_common(15)
    by_person = Counter(t["email"] for t in traces).most_common(15)
    secs = sorted(t["secs"] for t in traces if t["model"] != "cache")
    days_used = Counter(time.strftime("%Y-%m-%d", time.localtime(t["ts"])) for t in traces)
    return {
        "days": days,
        "totals": {"questions": total, "people": len({t["email"] for t in traces}),
                   "scenario": sum(t["mode"] == "scenario" for t in traces),
                   "cached": sum(t["model"] == "cache" for t in traces),
                   "backup": sum(bool(t["fallback"]) for t in traces),
                   "wrong": len(feedback),
                   "median_secs": round(secs[len(secs) // 2], 1) if secs else 0,
                   "p90_secs": round(secs[int(len(secs) * .9)], 1) if secs else 0},
        "top": [{"q": g["q"], "n": g["n"], "people": len(g["people"]), "last": g["last"]} for g in top],
        "gaps": gaps,
        "wrong": [{"ts": f["ts"], "q": _q(f["question"]), "note": f["note"], "program": f["program"] or f["section"],
                   "email": f["email"]} for f in feedback[:25]],
        "by_program": by_program,
        "by_person": by_person,
        "per_day": sorted(days_used.items())[-days:],
        "cache": answer_cache.stats(),
    }


def norm_email(e: str) -> str:
    return "This PC" if e == "local" else re.sub(r"@.*", "", e)
