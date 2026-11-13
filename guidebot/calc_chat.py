"""Calculator questions in chat: "DSCR on $3,200 rent, $400k at 7.5%, taxes 350, insurance 120", "cost of a 2-1
buydown on $500k at 6.875%", "IRRRL from 7.25 to 6.5 fixed to fixed", "FHA streamline seasoning, first payment
3/1/2026, closed 1/20/2026".

The model only reads the numbers out of the question (CalcInputs). Every number it returns has to be in the question
itself or it's dropped, and Python (calculators.py, the site's math) does all of the arithmetic.
"""
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel

from guidebot import calculators as C
from guidebot.llm import ask_json

_NUM = r"\d[\d,]*(?:\.\d+)?"
GATES = {
    "dscr": re.compile(rf"\bdscr\b.*\brent|\brent\b.*\bdscr\b", re.I | re.S),
    "buydown": re.compile(rf"buy\s*-?\s*down.*\$\s*{_NUM}|\$\s*{_NUM}.*buy\s*-?\s*down", re.I | re.S),
    "va_irrrl": re.compile(r"\birrrl\b", re.I),
    "fha_streamline": re.compile(r"\bstreamline\b", re.I),
}
_RATES = re.compile(rf"{_NUM}\s*%|\b\d\.\d+\b")
_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b", re.I)


class CalcInputs(BaseModel):
    kind: Literal["dscr", "buydown", "va_irrrl", "fha_streamline", "none"]
    monthly_rent: float | None = None
    loan_amount: float | None = None
    rate: float | None = None             # note rate, percent
    years: float | None = None
    interest_only: bool = False
    taxes: float | None = None            # monthly
    insurance: float | None = None        # monthly
    hoa: float | None = None              # monthly
    flood: float | None = None            # monthly
    pitia: float | None = None            # monthly total, when given outright
    buydown: Literal["3-2-1", "2-1", "1-1", "1-0"] | None = None
    old_rate: float | None = None
    new_rate: float | None = None
    old_type: Literal["fixed", "arm"] | None = None
    new_type: Literal["fixed", "arm"] | None = None
    old_mip: float | None = None          # FHA annual MIP, percent
    new_mip: float | None = None
    old_pi: float | None = None           # current monthly principal and interest
    payoff: float | None = None
    costs: float | None = None            # closing costs, total
    first_payment_date: str | None = None  # YYYY-MM-DD
    sixth_payment_date: str | None = None
    closing_date: str | None = None


SYSTEM = """Read the numbers out of a mortgage calculator question. Copy each number exactly as the user wrote it;
never compute, estimate or fill in a number they didn't give. Leave a field null when it isn't stated.
kind: dscr (debt service coverage), buydown (temporary buydown payments or cost), va_irrrl, fha_streamline, or none
when the question gives no numbers to calculate with. Rates and MIP are percents (7.25). Monthly amounts stay monthly;
if taxes or insurance are given per year, put the yearly figure and say nothing else. Dates as YYYY-MM-DD."""


def _flat(text: str) -> str:
    return re.sub(r"[,$\s]", "", text.lower())


def _in_text(value: float, text: str) -> bool:
    """The number as the user could have typed it: 400000, 400,000, 400k, $1.2M, 7.25."""
    flat = _flat(text)
    forms = {f"{value:g}", f"{value:.2f}", f"{value:.3f}", f"{value:.1f}"}
    for unit, mult in (("k", 1e3), ("m", 1e6)):
        if value >= mult and value % (mult / 1000) == 0:
            forms.add(f"{value / mult:g}{unit}")
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", flat) for f in forms)


def _dates_in(text: str) -> set[str]:
    out = set()
    for m in _DATE.finditer(text):
        s = m[0]
        try:
            if "/" in s:
                mo, d, y = (int(x) for x in s.split("/"))
                out.add(date(y + 2000 if y < 100 else y, mo, d).isoformat())
            elif "-" in s:
                out.add(date.fromisoformat(s).isoformat())
            else:
                from datetime import datetime
                out.add(datetime.strptime(re.sub(r"[.,]", "", s[:3] + " " + s.split(None, 1)[1]), "%b %d %Y").date().isoformat())
        except ValueError:
            pass
    return out


def _checked(inp: CalcInputs, text: str) -> CalcInputs:
    """Drops any number or date the question doesn't actually contain. Yearly taxes/insurance become monthly."""
    data, dates = inp.model_dump(), _dates_in(text)
    for k, v in list(data.items()):
        if isinstance(v, float) and not _in_text(v, text):
            data[k] = None
        if k.endswith("_date") and v and v not in dates:
            data[k] = None
    for k in ("taxes", "insurance", "hoa", "flood"):
        v = data[k]
        if v and re.search(rf"{k[:4]}\w*[^.\n]{{0,25}}(per year|/\s*y(ea)?r|annual|yearly)|(annual|yearly)\s+{k[:4]}", text, re.I):
            data[k] = v / 12
    return CalcInputs(**data)


# Python fallbacks for numbers the model drops (qwen3 read the rent and loan but not "at 7.5%, taxes $350").
_MONEY = r"\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([km])?\b"   # "2500, loan" is 2500, not "2500,"
_FILL = {
    "monthly_rent": rf"{_MONEY}\s*(?:/\s*mo\w*|a month|per month|monthly)?\s*(?:gross\s+)?rents?\b|\brents?\s*(?:of|is|are|at|:)?\s*{_MONEY}",
    "loan_amount": rf"{_MONEY}\s*(?:loan|mortgage)|\b(?:loan|loan amount|mortgage)\s*(?:of|is|amount|at|:)?\s*{_MONEY}",
    "rate": r"\b(?:at|rate\s*(?:of|is|:)?)\s*(\d{1,2}(?:\.\d+)?)\s*%",
    "taxes": rf"\btax\w*\s*(?:of|are|is|:)?\s*{_MONEY}|{_MONEY}\s*(?:/\s*mo\w*\s*)?(?:in\s+)?tax",
    "insurance": rf"\b(?:insurance|hoi|hazard)\s*(?:of|is|:)?\s*{_MONEY}|{_MONEY}\s*(?:/\s*mo\w*\s*)?(?:in\s+)?(?:insurance|hoi)",
    "hoa": rf"\bhoa\s*(?:dues|fees?)?\s*(?:of|is|are|:)?\s*{_MONEY}|{_MONEY}\s*(?:/\s*mo\w*\s*)?hoa",
}
_FROM_TO = re.compile(r"from\s*(\d{1,2}(?:\.\d+)?)\s*%?\s*to\s*(\d{1,2}(?:\.\d+)?)\s*%?", re.I)


def _num(groups) -> float | None:
    vals = [g for g in groups if g]
    if not vals:
        return None
    n = float(vals[0].replace(",", ""))
    unit = vals[1].lower() if len(vals) > 1 and vals[1].lower() in ("k", "m") else ""
    return n * {"k": 1e3, "m": 1e6}.get(unit, 1)


def _fill(inp: CalcInputs, text: str) -> CalcInputs:
    data = inp.model_dump()
    for field, rx in _FILL.items():
        if data[field] is None and (m := re.search(rx, text, re.I)):
            data[field] = _num(m.groups())
    if data["loan_amount"] is None and data["kind"] == "buydown" and (m := re.search(rf"\bon\s+(?:a\s+)?{_MONEY}", text, re.I)):
        data["loan_amount"] = _num(m.groups())   # "3-2-1 buydown on $650k"
    if (data["old_rate"] is None or data["new_rate"] is None) and (m := _FROM_TO.search(text)):
        data["old_rate"], data["new_rate"] = float(m[1]), float(m[2])
    if data["years"] is None and (m := re.search(r"\b(15|20|25|30|40)\s*[- ]?(?:yr|year)", text, re.I)):
        data["years"] = float(m[1])
    if data["buydown"] is None and (m := re.search(r"\b(3-2-1|2-1|1-1|1-0)\b", text)):
        data["buydown"] = m[1]
    data["interest_only"] = data["interest_only"] or bool(re.search(r"interest[- ]only|\bI/?O\b", text))
    return CalcInputs(**data)


def kind_of(question: str) -> str | None:
    for kind, gate in GATES.items():
        if gate.search(question):
            if kind in ("va_irrrl", "fha_streamline") and not (len(_RATES.findall(question)) >= 2 or _DATE.search(question)):
                continue   # a rule question ("IRRRL seasoning?") stays a guideline question
            return kind
    return None


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _dscr(i: CalcInputs) -> str | None:
    if not i.monthly_rent or not (i.pitia or (i.loan_amount and i.rate)):
        need = [n for n, ok in (("the monthly rent", i.monthly_rent), ("the loan amount", i.pitia or i.loan_amount),
                                ("the note rate", i.pitia or i.rate)) if not ok]
        return (f"To work out the DSCR I need {' and '.join(need)} (or the full monthly PITIA), plus taxes and "
                "insurance if you have them. Python does the math, not the model.")
    r = C.dscr(i.monthly_rent, i.loan_amount or 0, i.rate or 0, i.years or 30, i.interest_only, i.taxes or 0,
               i.insurance or 0, i.hoa or 0, i.flood or 0, pitia=i.pitia)
    if r["dscr"] is None:
        return None
    tier = {"qualifies": "1.00 or higher: meets the standard DSCR minimum",
            "ignite": "0.75 to 0.99: only the Ignite DSCR .75-.99 tier",
            "no": "under 0.75: doesn't qualify on any DSCR tier"}[r["tier"]]
    lines = [f"**DSCR: {r['dscr']:.2f}** ({tier})", "", "| | Monthly |", "|---|---|",
             f"| Rent | {_money(i.monthly_rent)} |"]
    if i.pitia:
        lines.append(f"| PITIA (as given) | {_money(r['total'])} |")
    else:
        pay = "Interest only" if i.interest_only else f"P&I ({i.years or 30:g} yr)"
        lines += [f"| {pay}: {_money(i.loan_amount)} at {i.rate:g}% | {_money(r['payment'])} |"]
        lines += [f"| {name} | {_money(v)} |" for name, v in (("Taxes", i.taxes), ("Insurance", i.insurance),
                                                               ("HOA", i.hoa), ("Flood", i.flood)) if v]
        lines.append(f"| **Total (PITIA)** | **{_money(r['total'])}** |")
        missing = [n for n, v in (("taxes", i.taxes), ("insurance", i.insurance)) if not v]
        if missing:
            lines += ["", f"- No {' or '.join(missing)} given, so they're left out. Add them for the real DSCR."]
    lines += ["", f"DSCR = rent / PITIA = {_money(i.monthly_rent)} / {_money(r['total'])}. Calculated by Python "
              "(the Revo site's DSCR calculator). Each program's DSCR minimum is on its sheet."]
    return "\n".join(lines)


def _buydown(i: CalcInputs) -> str | None:
    if not (i.loan_amount and i.rate):
        need = [n for n, ok in (("the loan amount", i.loan_amount), ("the note rate", i.rate)) if not ok]
        return f"To price the buydown I need {' and '.join(need)}. Python does the math, not the model."
    years = int(i.years or 30)
    r = C.buydown(i.loan_amount, i.rate, years)
    opts = [o for o in r["options"] if not i.buydown or o["key"] == i.buydown]
    lines = [f"**Temporary buydown on {_money(i.loan_amount)} at {i.rate:g}%** ({years} yr, note payment "
             f"{_money(r['notePayment'])})", ""]
    for o in opts:
        lines += [f"**{o['label']}: cost {_money(o['cost'])}** (the minimum seller/lender credit)", "",
                  "| Year | Rate | Payment | Monthly subsidy | Year's subsidy |", "|---|---|---|---|---|"]
        lines += [f"| {x['year']} | {x['ratePct']:.3f}% | {_money(x['payment'])} | {_money(x['monthlySubsidy'])} | "
                  f"{_money(x['annualSubsidy'])} |" for x in o["rows"]]
        lines += [f"| {o['fullPaymentYear']}+ | {i.rate:.3f}% | {_money(r['notePayment'])} | | |", ""]
    lines.append("Calculated by Python (the Revo site's buydown calculator, which matches the Buydown Calculator "
                 "spreadsheet to the cent). Whether a buydown is allowed is on each program's sheet.")
    return "\n".join(lines)


def _kind_change(i: CalcInputs, fha: bool) -> str | None:
    if not (i.old_type and i.new_type):
        return None
    key = f"{i.old_type}-{i.new_type}"
    if fha and i.new_type == "arm":
        return None   # FHA splits ARMs into one-year and hybrid, and ARMs by months to the next change
    if fha and i.old_type == "arm":
        return None
    return key


def _irrrl(i: CalcInputs, question: str) -> str | None:
    parts = []
    if i.old_rate and i.new_rate:
        key = _kind_change(i, fha=False) or ("fixed-fixed" if not re.search(r"\barm\b", question, re.I) else None)
        if key:
            n = C.va_ntb(key, i.old_rate, i.new_rate)
            ok = "✅ Meets" if n["meets"] else "❌ Doesn't meet"
            parts += [f"**Net tangible benefit ({C.VA_NTB[key]['label']}): {ok}**", "",
                      f"- {i.old_rate:g}% to {i.new_rate:g}%: a {n['reduction']:g} point reduction. Rule: {n['rule']}."
                      + (f" Needs {n['target']:g}% or lower." if n["target"] is not None and not n["strict"] else "")]
            if not i.old_type:
                parts.append("- Read as fixed to fixed. Say if either loan is an ARM.")
    if i.payoff and i.old_pi and i.new_rate and i.costs is not None:
        r = C.va_irrrl(payoff=i.payoff, costs_financed=i.costs, old_pi=i.old_pi, new_rate=i.new_rate,
                       new_years=i.years or 30, orig_years=i.years or 30)
        months = "never (no payment drop)" if r["months"] == float("inf") else f"{r['months']:.1f} months"
        parts += ["", f"**Recoupment: {months}** ({'✅ within' if r['recoups'] else '❌ over'} 36 months)", "",
                  f"- New loan {_money(r['amount'])} (payoff + costs + {_money(r['fee'])} funding fee at 0.50%). "
                  f"New P&I {_money(r['newPI'])} vs {_money(r['oldPI'])}: saves {_money(r['savings'])}/month.",
                  "- Costs treated as financed, veteran not fee-exempt. Say so if either is different."]
    if i.first_payment_date and i.sixth_payment_date:
        s = C.va_seasoning(i.first_payment_date, i.sixth_payment_date)
        parts += ["", f"**Seasoning: earliest new Note date {s['earliest']}** ({s['reason']}; 210 days after the first "
                  f"due date is {s['by210']}, sixth payment {s['bySixth']})"]
    if not parts:
        return None
    return "\n".join(parts + ["", "Calculated by Python (the Revo site's VA IRRRL worksheet, from NDM's VA IRRRL matrix)."])


def _streamline(i: CalcInputs, question: str) -> str | None:
    parts = []
    if i.old_rate and i.new_rate and i.old_mip is not None and i.new_mip is not None:
        key = _kind_change(i, fha=True) or ("fixed-fixed" if not re.search(r"\barm\b", question, re.I) else None)
        if key:
            cut = bool(re.search(r"(more than|over)\s*(3|three)\s*years?|30\s*(yr|year)s?\s*to\s*(15|20)", question, re.I))
            n = C.fha_ntb(key, i.old_rate, i.old_mip, i.new_rate, i.new_mip, cut)
            ok = "✅ Meets" if n["meets"] else "❌ Doesn't meet"
            parts += [f"**Net tangible benefit ({C.FHA_NTB[key]['label']}): {ok}**", "",
                      f"- Combined rate (note + annual MIP): {n['prior']:.3f}% to {n['next']:.3f}%, a {n['reduction']:.3f} "
                      f"point reduction. Rule: {n['rule']}.", f"- New combined rate needs to be {'under' if n['strict'] else 'at or under'} "
                      f"{n['target']:.3f}%."]
    elif i.old_rate and i.new_rate:
        parts.append("For the FHA streamline net tangible benefit I need the annual MIP on both loans (the test is note "
                     "rate + MIP).")
    if i.first_payment_date and i.closing_date:
        s = C.fha_seasoning(i.first_payment_date, i.closing_date)
        parts += ["", f"**Seasoning: earliest case number / closing {s['earliest']}** ({s['reason']}; closing + 211 days "
                  f"is {s['byClosing']}, six full months after the first payment is {s['bySixMonths']})"]
    if not parts:
        return None
    return "\n".join(parts + ["", "Calculated by Python (the Revo site's FHA Streamline worksheet, matched to the "
                                  "spreadsheet cell for cell)."])


def answer(question: str, model: str) -> str | None:
    """Markdown with the calculation, or None when it isn't a calculator question or the numbers aren't all there."""
    kind = kind_of(question)
    if not kind:
        return None
    try:
        read = ask_json(SYSTEM, f"Question: {question}", CalcInputs, model)
    except Exception:
        read = CalcInputs(kind=kind)
    inp = _checked(_fill(read, question), question)
    return {"dscr": lambda: _dscr(inp), "buydown": lambda: _buydown(inp),
            "va_irrrl": lambda: _irrrl(inp, question), "fha_streamline": lambda: _streamline(inp, question)}[kind]()
