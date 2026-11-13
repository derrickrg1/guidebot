"""The Revo site's calculators in Python, so AskNick computes these instead of the model working them out:
DSCR (../site/assets/dscr.js), VA IRRRL (va-irrrl.js), FHA Streamline (fha-streamline.js), temporary buydowns
(buydown.js). Each is a line-for-line port, rounding included; tests/test_calculators.py runs the site's own JS on
random inputs and checks these against it.

Dates are 'YYYY-MM-DD' strings, handled as whole days. Rates are percents (6.5 = 6.5%).
"""
import math
from datetime import date, timedelta


def _day(s: str) -> date:
    return date.fromisoformat(str(s))


def _cents(n: float) -> float:
    return math.floor((n + 1e-9) * 100 + 0.5) / 100   # JS Math.round: halves go up


def _js_round(x: float) -> float:
    return math.floor(x + 0.5)


def _r9(x: float) -> float:
    return _js_round(x * 1e9) / 1e9


# ---------- DSCR ----------

def pmt(monthly_rate: float, n: int, amount: float) -> float:
    if monthly_rate == 0:
        return amount / n
    return monthly_rate * amount / (1 - (1 + monthly_rate) ** -n)


def dscr(rent: float, loan: float = 0, rate_pct: float = 0, years: float = 30, io: bool = False, taxes: float = 0,
         hoi: float = 0, hoa: float = 0, flood: float = 0, pitia: float | None = None) -> dict:
    """DSCR = monthly rent / PITIA (ITIA when interest only). Tiers from the Ignite DSCR matrix: 1.00+ qualifies,
    .75 to .99 is Ignite's .75-.99 tier, under .75 doesn't qualify. pitia: a total given outright."""
    r, n = rate_pct / 100 / 12, int(_js_round(years * 12))
    payment = 0.0 if pitia is not None else (loan * r if io else pmt(r, n, loan) if loan else 0.0)
    total = pitia if pitia is not None else payment + taxes + hoi + hoa + flood
    if not (total > 0) or not (rent > 0):
        return {"payment": payment, "total": total, "dscr": None, "tier": None}
    ratio = _js_round(rent / total * 100) / 100
    return {"payment": payment, "total": total, "dscr": ratio,
            "tier": "qualifies" if ratio >= 1 else "ignite" if ratio >= 0.75 else "no"}


# ---------- VA IRRRL ----------

VA_FUNDING_FEE = 0.005   # ".50% regardless of Subsequent Use" for non-exempt veterans
VA_MAX_MONTHS, VA_PITI_TRIGGER, VA_TERM_PLUS, VA_MAX_TERM = 36, 0.20, 10, 30


def va_seasoning(first_payment_due: str, sixth_payment_made: str) -> dict:
    """The new Note date: on or after the later of the sixth monthly payment and 210 days after the first due date."""
    by210, by_sixth = _day(first_payment_due) + timedelta(days=210), _day(sixth_payment_made)
    earliest = max(by210, by_sixth)
    return {"by210": by210.isoformat(), "bySixth": by_sixth.isoformat(), "earliest": earliest.isoformat(),
            "reason": "210 days after the first payment due date" if earliest == by210 and by210 != by_sixth
            else "The sixth monthly payment"}


def va_pi(amount: float, rate_pct: float, years: float) -> float:
    r, n = rate_pct / 100 / 12, int(_js_round(years * 12))
    if not (amount > 0) or not (n > 0):
        return 0
    return _cents(amount / n if r == 0 else amount * r / (1 - (1 + r) ** -n))


def va_irrrl(payoff: float = 0, costs_financed: float = 0, costs_cash: float = 0, exempt: bool = False,
             old_pi: float = 0, new_rate: float = 0, new_years: float = 30, orig_years: float = 0,
             taxes_insurance: float = 0, old_type: str = "fixed", new_type: str = "fixed", points: float = 0,
             value: float = 0, cash_back: float = 0, texas: bool = False) -> dict:
    """The new loan, payment change, 36-month recoupment, the 20% PITI trigger, the term, cash back, and the
    Fixed-to-ARM LTV cap. Recoupment: all loan costs / monthly P&I reduction (38 U.S.C. 3709)."""
    base = _cents(payoff + costs_financed)
    fee = 0 if exempt else _cents(base * VA_FUNDING_FEE)
    amount = _cents(base + fee)
    new_pi = va_pi(amount, new_rate, new_years)
    old = _cents(old_pi)
    savings = _cents(old - new_pi)
    costs = _cents(costs_financed + costs_cash)
    months = costs / savings if savings > 0 else (math.inf if costs > 0 else 0)
    recoups = months <= VA_MAX_MONTHS + 1e-9 if savings > 0 else costs == 0
    old_piti, new_piti = _cents(old + taxes_insurance), _cents(new_pi + taxes_insurance)
    piti_change = (new_piti - old_piti) / old_piti if old_piti > 0 else 0
    max_years = min(orig_years + VA_TERM_PLUS, VA_MAX_TERM)
    cash_max = 0 if texas else 500
    arm = old_type == "fixed" and new_type == "arm" and points > 0
    ltv = base / value if arm and value > 0 else None
    ltv_max = None if not arm else 0.90 if points > 1 else 1.00 if points < 1 else None   # exactly 1%: no cap given
    return {"base": base, "fee": fee, "amount": amount, "newPI": new_pi, "oldPI": old, "savings": savings,
            "costs": costs, "months": months, "recoups": recoups, "oldPITI": old_piti, "newPITI": new_piti,
            "pitiChange": piti_change, "creditQualifying": piti_change >= VA_PITI_TRIGGER - 1e-12,
            "maxYears": max_years, "termOk": new_years <= max_years, "cashMax": cash_max, "cashOk": cash_back <= cash_max,
            "appraisal": arm, "ltv": ltv, "ltvMax": ltv_max,
            "ltvOk": None if ltv is None or ltv_max is None else ltv <= ltv_max + 1e-12}


VA_NTB = {
    "fixed-fixed": {"label": "Fixed → Fixed", "need": 0.5, "text": "Fixed to Fixed requires 0.5% rate reduction"},
    "fixed-arm": {"label": "Fixed → ARM", "need": 2.0, "text": "Fixed to ARM requires 2.0% rate reduction"},
    "arm-fixed": {"label": "ARM → Fixed", "need": None, "text": "ARM-to-Fixed: No rate reduction required, rate may increase."},
    "arm-arm": {"label": "ARM → ARM", "need": 0, "text": "ARM to Fixed and ARM to ARM are exempt from rate reduction"},
}


def va_ntb(kind: str, old_rate: float, new_rate: float) -> dict:
    t, reduction = VA_NTB[kind], _r9(old_rate - new_rate)
    meets = True if t["need"] is None else reduction > 0 if t["need"] == 0 else reduction >= t["need"]
    return {"reduction": reduction, "meets": meets, "rule": t["text"],
            "target": None if t["need"] is None else _r9(old_rate - t["need"]), "strict": t["need"] == 0}


# ---------- FHA Streamline ----------

FHA_UFMIP = 0.0175


def _edate(d: date, months: int) -> date:
    """Excel EDATE: same day n months later, or that month's last day."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def fha_seasoning(first_payment_due: str, original_closing: str) -> dict:
    """The later of 211 days after the original closing, or the day after six full months from the first due date."""
    by_closing = _day(original_closing) + timedelta(days=211)
    by_six = _edate(_day(first_payment_due), 6) + timedelta(days=1)
    earliest = max(by_closing, by_six)
    return {"byClosing": by_closing.isoformat(), "bySixMonths": by_six.isoformat(), "earliest": earliest.isoformat(),
            "reason": "210 Days from closing date" if earliest == by_closing
            else "Six full months have passed since first payment date"}


def fha_max_loan(principal: float = 0, original_amount: float = 0, per_diem: str = "daily", funding: str = "",
                 last_payment: str = "", daily_interest: float = 0, monthly_interest: float = 0, pro_rata_mip: float = 0,
                 late_charges: float = 0, escrow_shortage: float = 0, unearned_ufmip: float = 0) -> dict:
    """Maximum streamline mortgage, as the FHA Streamline Worksheet spreadsheet works it out."""
    daily = per_diem == "daily"
    days = ((_day(funding) - _day(last_payment).replace(day=1)).days + 1) if daily and funding else 0
    unacceptable = days > 59
    interest = (0 if unacceptable else daily_interest * days) if daily else monthly_interest
    total = principal + interest + pro_rata_mip + late_charges + escrow_shortage
    lesser = min(total, original_amount)
    refund_estimate = lesser * FHA_UFMIP
    refund = refund_estimate if refund_estimate < unearned_ufmip else unearned_ufmip
    base = math.floor(lesser - refund + 1e-9)
    new_ufmip = base * FHA_UFMIP
    return {"days": days, "unacceptable": unacceptable, "interest": interest, "total": total, "lesser": lesser,
            "refundEstimate": refund_estimate, "refund": refund, "base": base, "newUfmip": new_ufmip,
            "maxMortgage": math.floor(base + new_ufmip + 1e-9)}


FHA_NTB = {
    "fixed-fixed": {"label": "Fixed → Fixed", "need": 0.5, "text": "At least .5 Below"},
    "fixed-arm1": {"label": "Fixed → One-Year ARM", "need": 2, "text": "At least 2 percentage points below"},
    "fixed-hybrid": {"label": "Fixed → Hybrid ARM", "need": 2, "text": "At Least 2% Below"},
    "arm-lt15-fixed": {"label": "ARM with less than 15 months to next change → Fixed", "need": -2, "text": "No More than 2%"},
    "arm-lt15-arm1": {"label": "ARM with less than 15 months to next change → One-Year ARM", "need": 1,
                      "text": "At least 1 percentage point below"},
    "arm-lt15-hybrid": {"label": "ARM with less than 15 months to next change → Hybrid ARM", "need": 1, "text": "At Least 1% Below"},
    "arm-ge15-fixed": {"label": "ARM with 15 or more months to next change → Fixed", "need": -2, "text": "No More than 2%"},
    "arm-ge15-arm1": {"label": "ARM with 15 or more months to next change → One-Year ARM", "need": 2,
                      "text": "At least 2 percentage points below"},
    "arm-ge15-hybrid": {"label": "ARM with 15 or more months to next change → Hybrid ARM", "need": 1, "text": "At Least 1% Below"},
}


def fha_ntb(kind: str, prior_rate: float, prior_mip: float, new_rate: float, new_mip: float,
            term_reduction_over_3: bool = False) -> dict:
    """Combined rate = note rate + annual MIP. Fixed to fixed cutting the term by more than 3 years only needs a
    lower combined rate."""
    prior, nxt = prior_rate + prior_mip, new_rate + new_mip
    reduction = _r9(prior - nxt)
    t = FHA_NTB[kind]
    term_rule = kind == "fixed-fixed" and term_reduction_over_3
    meets = nxt < prior if term_rule else reduction >= t["need"]
    target = prior if term_rule else _r9(prior - t["need"])
    return {"prior": prior, "next": nxt, "reduction": reduction, "meets": meets, "target": target,
            "strict": bool(term_rule), "rule": "Below the current rate" if term_rule else t["text"]}


# ---------- temporary buydowns ----------

BUYDOWNS = {"3-2-1": [3, 2, 1], "2-1": [2, 1], "1-1": [1, 1], "1-0": [1]}


def round2(x: float) -> float:
    """Excel ROUND(x, 2): half away from zero, with the spreadsheet's nudge for float error (2.675 -> 2.68)."""
    s, a = (-1 if x < 0 else 1), abs(x)
    return s * _js_round(a * 100 + 1e-7) / 100


def _xl_pmt(monthly_rate: float, n: int, amount: float) -> float:
    if monthly_rate == 0:
        return amount / n
    f = (1 + monthly_rate) ** n
    return amount * monthly_rate * f / (f - 1)


def buydown(amount: float, rate_pct: float, years: int = 30) -> dict:
    """Payments per bought-down year and the cost (sum of the annual subsidies, the minimum seller credit)."""
    n, r = years * 12, rate_pct / 100
    note = round2(_xl_pmt(r / 12, n, amount))
    options = []
    for key, steps in BUYDOWNS.items():
        rows = []
        for i, pts in enumerate(steps):
            rate = r - pts / 100
            pay = round2(_xl_pmt(rate / 12, n, amount))
            monthly = round2(note - pay)
            rows.append({"year": i + 1, "ratePct": rate * 100, "payment": pay, "monthlySubsidy": monthly,
                         "annualSubsidy": monthly * 12})
        options.append({"key": key, "label": key, "rows": rows, "cost": round2(sum(x["annualSubsidy"] for x in rows)),
                        "fullPaymentYear": len(steps) + 1})
    return {"notePayment": note, "totalPayments": n, "options": options}
