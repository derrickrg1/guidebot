"""Race chat models on scenarios whose answers were checked by hand against the Ignite 1099 PDF.

Run:  python -m tools.compare_models qwen3:14b gpt-oss:20b

Scenario answers use the Ignite 1099 overlay rules (treated as approved in memory only, nothing is saved),
so the model's whole job is mapping the deal onto the matrix. Q&A checks read the full program and look for
the key words the answer must contain.
"""
import re
import sys
import time

from guidebot import chat, library, overlay_rules
from guidebot.answer import full_doc_messages, scenario_answer
from guidebot.llm import stream_chat

PROGRAM = "Ignite 1099"
NE = "not eligible"   # any answer that refuses a number: Not eligible / Can't price

SCENARIOS = [  # (question, earlier user message or None, expected max LTV or NE)
    ("Purchase, primary residence, single family, $650,000 loan, 720 FICO", None, 90),
    ("Purchase, primary residence, 2 unit, $650,000 loan, 720 FICO", None, 85),
    ("Purchase, investment property, 2 unit, $650,000 loan, 720 FICO", None, 80),
    ("Cash out, primary residence, condo, $500,000 loan, 740 FICO, BK discharged 30 months ago", None, 75),
    ("Purchase, investment property, single family in Philadelphia, $400,000 loan, 760 FICO", None, NE),
    ("Cash out, primary, SFR, $850,000 loan, 699 FICO", None, 80),
    ("Rate and term refi, second home, $1,200,000 loan, 690 FICO", None, 85),
    ("Investment cash out, single family, $1.2m loan, 705 FICO", None, 75),
    ("Purchase, investment, SFR, 650 FICO, $400k", None, NE),
    ("Purchase, primary, single family, $750k, 760 FICO, interest only", None, 85),
    ("Purchase, second home, warrantable condo, $600k, 745 FICO, declining market", None, 80),
    ("Purchase, primary, sfr, $650k, 610 FICO", None, NE),
    ("what if it's an investment property?", "Purchase, primary residence, single family, $650,000 loan, 720 FICO", 85),
]

QA = [  # (question, words the answer must contain (case-insensitive), what the first line must open with)
    ("What are the reserve requirements for a $1,500,000 loan?", ["9 months"], None),
    ("What's the minimum credit score for co-borrowers?", ["600"], None),
    ("What's the max DTI?", ["50%"], None),
    # yes/no: the first line has to get it right, that's what people read
    ("Can a non-occupant co-borrower be used on a cash out?", [], "no"),
    ("Can a non-occupant co-borrower be used on a primary residence purchase?", [], "yes"),
    ("Can gift funds be used for reserves?", [], "no"),
    ("Can a first time home buyer purchase an investment property?", [], "no"),
    ("Is a prepayment penalty allowed on a primary residence?", [], "no"),
    ("Are condotels eligible?", [], ("yes", "it depends")),   # allowed, with ineligible kinds listed
]


def got(answer: str | None):
    if not answer:
        return None
    if m := re.search(r"Max LTV/CLTV: (\d+(?:\.\d+)?)%", answer):
        return float(m.group(1))
    if re.search(r"not eligible|can't price", answer, re.I):
        return NE
    return answer.splitlines()[0][:60]


def run(model: str):
    docs, _ = chat.program_docs(PROGRAM, library.programs())
    mats = library.matrices(docs)
    text = "\n\n".join(library.render(d) for d in docs)
    rules = overlay_rules.for_program(PROGRAM, docs)
    rules["status"] = "approved"   # in memory only
    score, total_time, rows = 0, 0.0, []
    for q, earlier, want in SCENARIOS:
        history = [{"role": "user", "content": earlier}, {"role": "assistant", "content": "(answered)"}] if earlier else []
        t = time.time()
        try:
            ans = got(scenario_answer(q, history, mats, text, model, rules))
        except Exception as e:
            ans = f"error: {e}"[:60]
        dt = time.time() - t
        ok = ans == want
        score += ok
        total_time += dt
        rows.append(f"  {'PASS' if ok else 'FAIL'}  {dt:5.1f}s  want {want!s:>13}  got {ans!s:>13}  {q[:70]}")
    for q, words, lead in QA:
        t = time.time()
        ans = "".join(stream_chat(full_doc_messages(q, text, [], ltv_ok=True, model=model), model))
        dt = time.time() - t
        plain = re.sub(r"[\s   ]+", " ", ans).replace("‑", "-").lower()   # gpt-oss uses odd spaces
        first = re.sub(r"[^a-z ]", "", plain.strip().split(".")[0]).strip()
        ok = all(w.lower() in plain for w in words) and (not lead or first.startswith(lead))
        score += ok
        total_time += dt
        need = f"starts {lead!r}" if lead else f"needs {words}"
        rows.append(f"  {'PASS' if ok else 'FAIL'}  {dt:5.1f}s  {need}  {q}"
                    + ("" if ok or not lead else f"\n            first line: {ans.strip().splitlines()[0][:110]}"))
    n = len(SCENARIOS) + len(QA)
    print(f"\n{model}: {score}/{n} correct, {total_time:.0f}s total, {total_time / n:.1f}s per question")
    print("\n".join(rows), flush=True)


if __name__ == "__main__":
    for m in sys.argv[1:] or ["qwen3:14b", "gpt-oss:20b"]:
        run(m)
