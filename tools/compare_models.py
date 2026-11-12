"""Race chat models on scenarios whose answers were checked by hand against each program's PDF.

Run:  python -m tools.compare_models claude-sonnet-5-5 qwen3:14b
      python -m tools.compare_models claude-sonnet-5-5 --program "Ignite P&L"

Scenario answers use each program's overlay rules (treated as approved in memory only, nothing is saved),
so the model's whole job is mapping the deal onto the matrix. Expected numbers were worked out by hand:
matrix cell from the PDF, then the overlays read off the same PDF. Q&A checks read the full program and
look for the key words the answer must contain.

Ignite Express DSCR has Q&A only for now: its approved matrix was saved by the old parser with shifted
headers, so deal answers are blocked until it's re-read on the Verify page.
"""
import re
import sys
import time

from guidebot import chat, library, overlay_rules
from guidebot.answer import full_doc_messages, scenario_answer
from guidebot.llm import stream_chat

NE = "not eligible"   # any answer that refuses a number: Not eligible / Can't price

# program: (scenarios as (question, earlier user message or None, expected max LTV or NE),
#           Q&A as (question, words the answer must contain, what the first line must open with))
PROGRAMS = {
    "Ignite 1099": ([
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
        ("Purchase, primary, SFR, $650k, 720 FICO, 52% DTI", None, NE),   # expanded DTI not available
    ], [
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
    ]),
    "Ignite Bank Statements": ([
        ("Purchase, primary, SFR, $650,000 loan, 700 FICO", None, 90),
        ("Cash out, investment, SFR, $1,200,000 loan, 720 FICO", None, 80),
        ("Purchase, investment, 2 unit, $800k, 740 FICO", None, 80),             # 85 cell, 2-4 unit 85 - 5 investment
        ("Purchase, primary, SFR, $650k, 720 FICO, 53% DTI", None, 80),          # expanded DTI cap
        ("Cash out, primary, SFR, $650k, 720 FICO, 53% DTI", None, NE),          # expanded DTI is purchase / R&T only
        ("Purchase, investment, SFR, $500k, 640 FICO", None, NE),                # N/A in the matrix
    ], [
        ("What's the minimum expense factor on business bank statements?", ["10%"], None),
        ("How many months of reserves are required for a $2,500,000 loan?", ["12 months"], None),
        ("What's the max DTI with expanded DTI?", ["55%"], None),
        ("Can gift funds be used for reserves?", [], "no"),
    ]),
    "Ignite Full Doc": ([
        ("Purchase, investment, SFR, $750k, 720 FICO, interest only", None, 80),     # IO caps investment at 80
        ("Purchase, primary, SFR, $750k, 720 FICO, interest only", None, 90),        # IO cap is investment only here
        ("Rate and term, second home, warrantable condo, $1.2m, 700 FICO", None, 85),
        ("Purchase, primary, SFR, $1.4m, 710 FICO, 54% DTI", None, 80),             # expanded DTI cap
        ("Purchase, primary, SFR, $1.6m, 720 FICO, 52% DTI", None, NE),             # expanded DTI max $1.5MM
        ("Purchase, primary, SFR, $500k, 720 FICO, 58% DTI", None, NE),
    ], [
        ("Can asset depletion use expanded DTI?", [], "no"),
        ("What's the minimum credit score for co-borrowers?", ["600"], None),
        ("Is a prepayment penalty allowed on a second home?", [], "no"),
    ]),
    "Ignite P&L": ([
        ("Purchase, primary, SFR, $650k, 720 FICO", None, 85),
        ("Cash out, investment, SFR, $1.8m loan, 700 FICO", None, 60),
        ("Purchase, second home, SFR, $900k, 690 FICO, BK discharged 30 months ago", None, 75),   # 80 - 5
        ("Purchase, investment, SFR in Baltimore, $400k, 740 FICO", None, NE),
        ("Cash out, primary, SFR, $1.2m, 680 FICO, purchased 4 months ago", None, 65),            # 70 - 5 seasoning
    ], [
        ("What's the minimum expense factor for a product based business?", ["30%"], None),
        ("What's the minimum ownership percentage of the business?", ["25%"], None),
        ("Can the borrower prepare their own P&L?", [], "no"),
        ("Can gift funds be used for reserves?", [], "no"),
    ]),
    "Ignite Express DSCR": ([], [
        ("What's the minimum DSCR for a rural property?", ["1.0"], None),
        ("How much of their own funds must the borrower put down when using gift funds?", ["10%"], None),
        ("Can a first time home buyer use this program?", [], "no"),
        ("What's the prepayment penalty?", ["5%"], None),
        ("Is a primary residence allowed?", [], "no"),
    ]),
}


def got(answer: str | None):
    if not answer:
        return None
    if m := re.search(r"Max LTV/CLTV: (\d+(?:\.\d+)?)%", answer):
        return float(m.group(1))
    if re.search(r"not eligible|can't price", answer, re.I):
        return NE
    return answer.splitlines()[0][:60]


def run_program(model: str, program: str, scenarios, qa) -> tuple[int, int, float, list[str]]:
    docs, _ = chat.program_docs(program, library.programs())
    mats = library.matrices(docs)
    text = "\n\n".join(library.render(d) for d in docs)
    rules = overlay_rules.for_program(program, docs)
    if rules:
        rules["status"] = "approved"   # in memory only
    score, total_time, rows = 0, 0.0, [f" {program}"]
    for q, earlier, want in scenarios:
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
    for q, words, lead in qa:
        t = time.time()
        ans = "".join(stream_chat(full_doc_messages(q, text, [], ltv_ok=True, model=model), model))
        dt = time.time() - t
        plain = re.sub(r"[\s   ]+", " ", ans).replace("‑", "-").lower()   # gpt-oss uses odd spaces
        first = re.sub(r"[^a-z ]", "", plain.strip().split(".")[0]).strip()
        ok = all(w.lower() in plain for w in words) and (not lead or first.startswith(lead))
        score += ok
        total_time += dt
        need = f"starts {lead!r}" if lead else f"needs {words}"
        rows.append(f"  {'PASS' if ok else 'FAIL'}  {dt:5.1f}s  {need}  {q}"
                    + ("" if ok or not lead else f"\n            first line: {ans.strip().splitlines()[0][:110]}"))
    return score, len(scenarios) + len(qa), total_time, rows


def run(model: str, only: str | None = None):
    score = n = 0
    total_time, rows = 0.0, []
    for program, (scenarios, qa) in PROGRAMS.items():
        if only and program != only:
            continue
        s, c, t, r = run_program(model, program, scenarios, qa)
        score, n, total_time = score + s, n + c, total_time + t
        rows += r + [f"  = {s}/{c}"]
    print(f"\n{model}: {score}/{n} correct, {total_time:.0f}s total, {total_time / max(n, 1):.1f}s per question")
    print("\n".join(rows), flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    only = None
    if "--program" in args:
        i = args.index("--program")
        only = args[i + 1]
        del args[i:i + 2]
    for m in args or ["qwen3:14b", "gpt-oss:20b"]:
        run(m, only)
