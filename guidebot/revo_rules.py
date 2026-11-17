"""Revo's own rules on top of the investor's (2026-11-16): "NDM says X, Revo says Y".

Derrick writes them on the admin Revo rules tab; they live in revo_overlays.json at the repo root. Python, not a
model, decides which apply: a rule's scope has to cover the question's program (or tab), and one of its keywords
has to be in the question. Matching rules are added under the answer, quoted as written, so the model never
rewords or invents one.
"""
import json
import re
import threading
import time
import uuid

from guidebot import config

FILE = config.ROOT / "revo_overlays.json"
_lock = threading.Lock()


def load() -> list[dict]:
    if not FILE.exists():
        return []
    try:
        return json.loads(FILE.read_text(encoding="utf-8")).get("rules", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save(rules: list[dict]):
    FILE.write_text(json.dumps({"rules": rules}, indent=1), encoding="utf-8")


def _clean(rule: dict) -> dict:
    words = rule.get("keywords") or []
    if isinstance(words, str):
        words = words.split(",")
    return {"scope": str(rule.get("scope") or "*").strip() or "*",
            "topic": str(rule.get("topic") or "").strip()[:120],
            "keywords": [w.strip().lower() for w in words if w.strip()][:20],
            "rule": str(rule.get("rule") or "").strip()[:2000]}


def save(rule: dict, email: str) -> dict:
    """Adds a rule, or replaces the one with the same id."""
    r = _clean(rule)
    if not r["rule"] or not r["topic"] or not r["keywords"]:
        return {"error": "A rule needs a topic, at least one keyword and the rule itself."}
    with _lock:
        rules = load()
        rid = str(rule.get("id") or "")
        old = next((x for x in rules if x["id"] == rid), None)
        r |= {"id": rid if old else uuid.uuid4().hex[:8], "by": email,
              "added": old["added"] if old else time.strftime("%Y-%m-%d"), "updated": time.strftime("%Y-%m-%d")}
        rules = [r if x["id"] == r["id"] else x for x in rules] if old else rules + [r]
        _save(rules)
    return {"ok": True, "rule": r}


def delete(rid: str) -> dict:
    with _lock:
        rules = load()
        left = [x for x in rules if x["id"] != rid]
        if len(left) == len(rules):
            return {"error": "not found"}
        _save(left)
    return {"ok": True}


def _covers(scope: str, program: str, section: str) -> bool:
    s = scope.lower()
    if s in ("*", "all", ""):
        return True
    if section and s == section.lower():
        return True
    p = (program or "").lower()
    return bool(p) and (p == s or p.startswith(s + " ") or s in p.split(" ("))


def matching(question: str, program: str, section: str, rules: list[dict] | None = None) -> list[dict]:
    q = question.lower()
    out = []
    for r in load() if rules is None else rules:
        if not _covers(r["scope"], program, section):
            continue
        if any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", q) for w in r["keywords"]):
            out.append(r)
    return out


def block(rules: list[dict]) -> str:
    if not rules:
        return ""
    lines = [f"- **{r['topic']}** ({'all programs' if r['scope'] == '*' else r['scope']}): {r['rule']}" for r in rules]
    return "\n\n**Revo overlay** (Revo's own rule, on top of the investor guide; Revo's rule wins)\n" + "\n".join(lines)


def adding(events, question: str, program: str, section: str):
    """Passes the answer through, then adds the matching Revo rules at the end."""
    yield from events
    extra = block(matching(question, program, section))
    if extra:
        yield {"text": extra}
