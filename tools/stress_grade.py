"""Second grader for stress runs: Claude Opus reads each guideline answer next to the pages it cites and judges
what Python can't: did it answer what the underwriter actually asked, is every claim backed by those pages, and
(for "This is wrong" reports) does the new answer fix what was reported. stress_check.py stays the first grader;
this one only reads, never changes an answer.

    .venv\\Scripts\\python.exe -m tools.stress_grade sonnet [id-prefix ...]   -> data/stress/grade-sonnet.md

Costs API money (one Opus call per answer graded), so it's run by hand, not nightly.
"""
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from guidebot.llm import _claude_json
from tools import stress_check as sc

GRADER = "claude-opus-5-5"
STRESS = Path(__file__).resolve().parents[1] / "data" / "stress"


class Grade(BaseModel):
    answers_the_question: bool       # does it answer what was asked (not a neighboring question)
    unsupported_claims: list[str]    # statements the cited pages don't back up, quoted short
    missing: list[str]               # what an underwriter would still need to act on it
    fixes_report: bool | None = None  # only for reported answers: is the reported problem fixed
    score: int                       # 1 (wrong or useless) to 5 (an underwriter could act on it as is)
    why: str


SYSTEM = """You grade answers from a mortgage guideline assistant for underwriters and account executives.
You get the question, the answer, and the text of every page the answer cites. Judge only from those pages.
- answers_the_question: false if it answers a different or broader question, or dodges.
- unsupported_claims: each specific rule, number or condition in the answer that the cited pages don't state. A
  claim marked as the general guideline with an agency citation is checked against that page like any other.
- missing: what the asker would still need (a condition or exception on those pages that changes the answer).
- fixes_report: when a REPORTED PROBLEM is given, true only if the new answer fixes it; otherwise null.
- score: 5 = right, sourced and complete; 4 = right with a small gap; 3 = mostly right, something an underwriter
  must catch; 2 = misleading; 1 = wrong.
Be strict and brief."""


def cited_text(answer: str, limit: int = 60000) -> str:
    parts, seen = [], set()
    for c in sc.citations(answer):
        key = (c["file"], c["page"])
        if not c["page"] or key in seen:
            continue
        seen.add(key)
        parts.append(f"=== {c['file']} | p. {c['page']} ===\n{sc.page_text(c['file'], c['page'])}")
    return "\n\n".join(parts)[:limit]


def grade(r: dict) -> Grade:
    report = r["expect"].get("report")
    user = (f"QUESTION:\n{r['q']}\n\nANSWER:\n{r['answer']}\n\n"
            + (f"REPORTED PROBLEM with an earlier answer:\n{report}\n\n" if report else "")
            + f"CITED PAGES:\n{cited_text(r['answer']) or '(none cited)'}")
    return _claude_json(SYSTEM, user, Grade, GRADER)   # no qwen fallback: a grade must come from Opus


def main(args: list[str]):
    name, prefixes = args[0], args[1:]
    rows = [json.loads(line) for line in (STRESS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if line]
    rows = [r for r in rows if sc.route_of(r) == "guide" and "[ERROR]" not in r["answer"]
            and (not prefixes or any(r["id"].startswith(p) for p in prefixes))]
    out, scores = [f"# Opus grades: {name}", ""], []
    for i, r in enumerate(rows, 1):
        try:
            g = grade(r)
        except Exception as e:
            print(f"{i}/{len(rows)} {r['id']}: grader failed ({e})", flush=True)
            continue
        scores.append(g.score)
        flag = g.score <= 3 or g.unsupported_claims or not g.answers_the_question or g.fixes_report is False
        print(f"{i}/{len(rows)} {r['id']}: {g.score}", flush=True)
        if flag:
            out += [f"## {r['id']} ({g.score}/5)", f"**Q:** {r['q']}", "", f"- {g.why}"]
            out += [f"- Unsupported: {c}" for c in g.unsupported_claims]
            out += [f"- Missing: {m}" for m in g.missing]
            if g.fixes_report is not None:
                out.append(f"- Reported problem fixed: {'yes' if g.fixes_report else 'NO'}")
            out.append("")
    if scores:
        out[1:1] = [f"{len(scores)} graded, average {sum(scores) / len(scores):.2f}/5, "
                    f"{sum(s >= 4 for s in scores)} at 4 or 5.", ""]
    (STRESS / f"grade-{name}.md").write_text("\n".join(out), encoding="utf-8")
    print(out[2] if scores else "nothing graded")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
