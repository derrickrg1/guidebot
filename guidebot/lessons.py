"""Lessons (2026-11-19): what AskNick learned from "This is wrong" reports, borrowed from AppraisAI.

On the admin Feedback tab, "Make a lesson" drafts a short, general rule from the report (Claude writes the draft,
nothing is saved). Derrick edits it and saves it, and only then does it count. Saved lessons go into every answer's
instructions for the programs they cover, under the rules and above the guidelines, so they sit in the cached block.

No PII: a lesson is a general reading rule, never a deal. The draft prompt says so, and save() refuses text that
looks like an email, phone number, SSN or street address.

data/lessons.json (git-ignored with the rest of data/). The answer cache watches it, so a new lesson clears old
cached answers.
"""
import json
import re
import threading
import time
import uuid

from pydantic import BaseModel

from guidebot import config

FILE = config.ROOT / "data" / "lessons.json"
_lock = threading.Lock()

_PII = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "an email address"),
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "an SSN"),
        (re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"), "a phone number"),
        (re.compile(r"\b\d{2,6}\s+\w+(\s\w+)?\s+(st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|ct|court|way)\b",
                    re.I), "a street address"),
        (re.compile(r"\bloan\s*(#|no\.?|number)\s*\w+", re.I), "a loan number")]


def load() -> list[dict]:
    if not FILE.exists():
        return []
    try:
        return json.loads(FILE.read_text(encoding="utf-8")).get("lessons", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict]):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps({"lessons": items}, indent=1), encoding="utf-8")


def pii(text: str) -> str:
    """What PII-looking thing is in the text, or ""."""
    return next((what for rx, what in _PII if rx.search(text)), "")


def save(item: dict, email: str) -> dict:
    text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()[:600]
    scope = str(item.get("scope") or "*").strip() or "*"
    if len(text) < 10:
        return {"error": "Write the lesson first."}
    if found := pii(text):
        return {"error": f"That looks like it has {found} in it. Lessons are general rules, no borrower details."}
    with _lock:
        items = load()
        lid = str(item.get("id") or "")
        old = next((x for x in items if x["id"] == lid), None)
        new = {"id": lid if old else uuid.uuid4().hex[:8], "scope": scope, "text": text,
               "feedback": item.get("feedback") or (old or {}).get("feedback"), "by": email,
               "added": old["added"] if old else time.strftime("%Y-%m-%d"), "updated": time.strftime("%Y-%m-%d")}
        items = [new if x["id"] == new["id"] else x for x in items] if old else items + [new]
        _save(items)
    return {"ok": True, "lesson": new}


def delete(lid: str) -> dict:
    with _lock:
        items = load()
        left = [x for x in items if x["id"] != lid]
        if len(left) == len(items):
            return {"error": "not found"}
        _save(left)
    return {"ok": True}


def _covers(scope: str, program: str, section: str) -> bool:
    from guidebot.revo_rules import _covers as covers   # same scope rules as Revo rules
    return covers(scope, program, section)


def block(program: str = "", section: str = "", every: bool = False) -> str:
    """The lessons for this program (or tab), as prompt text. every=True (all-programs answers): every lesson,
    each labeled with what it covers."""
    items = load()
    if not every:
        items = [x for x in items if _covers(x["scope"], program, section)]
    if not items:
        return ""
    lines = [f"- {x['text']}" + (f" (applies to: {x['scope']})" if every and x["scope"] not in ("*", "") else "")
             for x in items]
    return ("\n\nLESSONS FROM PAST MISTAKES (Revo's admin approved each one after a wrong answer). Follow them. "
            "If one ever conflicts with the documents' own words, the documents win.\n" + "\n".join(lines))


class Draft(BaseModel):
    lesson: str
    scope: str


DRAFT_SYSTEM = """A mortgage guideline assistant gave a wrong answer and a loan officer reported it. Write ONE short
lesson (one or two sentences, under 60 words) that would stop this kind of mistake next time.

- Make it a general rule about how to read or apply the guidelines, not a fix for this one deal.
- No borrower details: no names, addresses, emails, phone numbers, loan numbers, or the deal's dollar amounts.
  Guideline numbers (a minimum FICO, a max LTV, a reserve count) are fine when the lesson needs them.
- Only state what the report and the answer support. If the report doesn't say what the right answer is, write the
  lesson as what to check, not as a new rule.
- scope: the program the lesson is about, copied exactly from "Program" below, or "*" if it applies to every
  program."""


def draft(fb: dict, model: str | None = None) -> dict:
    from guidebot.llm import ask_json
    user = (f"Program: {fb.get('program') or fb.get('section') or 'all programs'}\nMode: {fb.get('mode') or ''}\n\n"
            f"Question: {fb.get('question', '')}\n\nWhat's wrong (from the loan officer): {fb.get('note', '')}\n\n"
            f"The answer they got:\n{(fb.get('answer') or '')[:12000]}")
    d = ask_json(DRAFT_SYSTEM, user, Draft, model or config.CHAT_MODEL)
    scope = d.scope.strip() or "*"
    if scope != "*" and scope not in (fb.get("program"), fb.get("section")):
        scope = fb.get("program") or fb.get("section") or "*"
    return {"text": d.lesson.strip(), "scope": scope, "warning": pii(d.lesson)}
