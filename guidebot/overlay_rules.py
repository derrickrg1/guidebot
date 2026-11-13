"""Overlay rules: each program's overlays as data (overlays/<Program>.json), built once from the PDF and
approved on the Verify page. At question time Python reads the deal's facts from the user's words and
applies the rules, so no model decides which overlays apply.

A rule applies when every condition in "when" is true. If a condition depends on a fact the user didn't
give, the rule isn't applied; it's listed as an "If ..." line instead. Nothing is assumed.
"""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from guidebot import config

RULES_DIR = config.ROOT / "overlays"
APPROVED_DIR = config.ROOT / "verified"

PURPOSES = {"Purchase": "purchase", "Rate/Term Refinance": "rate_term", "Cash Out Refinance": "cash_out"}
OCCUPANCIES = {"Primary Residence": "primary", "Second Home": "second", "Investment Property": "investment"}


# ---------- facts from the user's words ----------

_NEG = r"(?<!\w)(?:not|no|non|isn't|isnt|without)[\s-]+(?:a\s+|an\s+|in\s+a\s+)?"
_FLAGS = {
    "rural": r"rural",
    "declining_market": r"declining(?:\s+market)?",
    "interest_only": r"interest[\s-]*only|\bi/?o\b",
    "reduced_reserves": r"reduced reserves|reserves? reduc",
    "fthb": r"first[\s-]*time[\s-]*(?:home[\s-]*)?buyer|\bfthb\b",
    "rent_free": r"rent[\s-]*free|no rental history|without rental history",
    "non_occupant_coborrower": r"non[\s-]*occupant",
    "texas_a6": r"50\s*\(?a\)?\s*\(?6\)?|\ba6\b|texas (?:home equity|cash[\s-]*out)",
    "texas_a4": r"50\s*\(?a\)?\s*\(?4\)?|\ba4\b",
    "short_term_rental": r"short[\s-]*term[\s-]*rental|\bstr\b|airbnb|vrbo",
    "vacant": r"vacant",
    "first_time_investor": r"first[\s-]*time[\s-]*investor",
    "non_permanent_resident": r"non[\s-]*permanent[\s-]*resident|\bnpra\b",
}
# Order matters: the specific condo kinds before plain "condo".
_PROPERTY = [
    ("condotel", r"condo[\s-]*tel"),
    ("non_warrantable_condo", r"non[\s-]*warrantable"),
    ("warrantable_condo", r"(?<!non-)(?<!non )\bwarrantable condo"),
    ("condo", r"condo"),   # kind not said: the condo rules stay unknown, never assumed warrantable
    ("2-4 unit", r"\b[234][\s-]*(?:unit|units|plex)\b|duplex|triplex|fourplex|quadplex|2\s*-\s*4\s*unit"),
    ("sfr", r"single[\s-]*family|\bsfr\b|\bsfh\b|\b1[\s-]*unit\b"),
    ("pud", r"\bpud\b"),
    ("townhome", r"town[\s-]*(?:home|house)"),
]
CONDO_KINDS = ("warrantable_condo", "non_warrantable_condo", "condotel")
_CITY = {"baltimore": r"baltimore", "philadelphia": r"philadelphia|philly"}
_MONTHS = r"(\d+(?:\.\d+)?)\s*(months?|mos?|years?|yrs?)"


def _months(num: str, unit: str) -> float:
    return float(num) * (12 if unit.lower().startswith("y") else 1)


def extract_facts(text: str, occupancy: str | None = None, purpose: str | None = None,
                  fico: int | None = None, loan_amount: int | None = None) -> dict:
    """Deal facts the user actually stated. A fact that isn't mentioned stays out (unknown)."""
    t = text.lower()
    facts: dict = {}
    if occupancy:
        facts["occupancy"] = OCCUPANCIES.get(occupancy, occupancy)
    if purpose:
        facts["purpose"] = PURPOSES.get(purpose, purpose)
    if fico:
        facts["fico"] = fico
    if loan_amount:
        facts["loan_amount"] = loan_amount
    for name, rx in _FLAGS.items():
        if re.search(_NEG + f"(?:{rx})", t):
            facts[name] = False
        elif re.search(rx, t):
            facts[name] = True
    for name, rx in _PROPERTY:
        if re.search(rx, t):
            facts["property"] = name
            break
    for name, rx in _CITY.items():
        if re.search(rx, t):
            facts["city"] = name
    if m := re.search(r"(?:bk|bankrupt\w*|foreclos\w*|short sale|deed in lieu|loan mod\w*|credit event)\D{0,30}?" + _MONTHS, t):
        facts["credit_event_months"] = _months(m.group(1), m.group(2))
    if m := re.search(r"(?:owned|purchased|bought|acquired)\D{0,20}?" + _MONTHS, t):
        facts["owned_months"] = _months(m.group(1), m.group(2))
    if m := re.search(r"(\d+)\s*x\s*30", t):
        facts["mortgage_30_lates"] = int(m.group(1))
    if m := re.search(r"(\d+)\s*x\s*60", t):
        facts["mortgage_60_lates"] = int(m.group(1))
    # "52% DTI", "DTI of 52", "dti 52.5%"
    # Out-of-range numbers are some other figure ("DSCR, 720 FICO"), so they're dropped, never guessed.
    if m := re.search(r"(\d+(?:\.\d+)?)\s*%?\s*dti\b|\bdti\D{0,8}?(\d+(?:\.\d+)?)", t):
        if 0 < (v := float(m.group(1) or m.group(2))) <= 100:
            facts["dti"] = v
    # "DSCR 1.15", "dscr of .85", "0.9 DSCR"
    if m := re.search(r"(\d*\.\d+|\d+(?:\.\d+)?)\s*dscr\b|\bdscr\D{0,8}?(\d*\.\d+|\d+(?:\.\d+)?)", t):
        if 0 < (v := float(m.group(1) or m.group(2))) < 5:
            facts["dscr"] = v
    return facts


# ---------- rule evaluation ----------

def _check(value, spec) -> bool:
    if isinstance(spec, dict):
        ops = {"lt": value < spec.get("lt", float("inf")) if "lt" in spec else True,
               "lte": value <= spec["lte"] if "lte" in spec else True,
               "gt": value > spec["gt"] if "gt" in spec else True,
               "gte": value >= spec["gte"] if "gte" in spec else True,
               "not": value not in spec["not"] if "not" in spec else True}
        return all(ops.values())
    if isinstance(spec, list):
        return value in spec
    return value == spec


def evaluate(when: dict, facts: dict) -> bool | None:
    """True if every condition holds, False if any fails, None if it hinges on an unknown fact."""
    result = True
    for name, spec in when.items():
        if name == "any":
            parts = [evaluate({k: v}, facts) for k, v in spec.items()]
            r = True if True in parts else (None if None in parts else False)
        elif facts.get(name) is None:
            r = None
        elif name == "property" and facts[name] == "condo" and spec in CONDO_KINDS:
            r = None
        else:
            r = _check(facts[name], spec)
        if r is False:
            return False
        if r is None:
            result = None
    return result


def rule_value(rule: dict, facts: dict) -> float | None:
    v = rule.get("value", 0)
    if isinstance(v, dict):
        key = facts.get("purpose") if facts.get("purpose") in v else facts.get("occupancy")
        v = v.get(key)
        if v is None:
            return None
    if rule.get("investment_minus") and facts.get("occupancy") == "investment":
        v -= rule["investment_minus"]
    return v


STAR_NOTE = ("The * footnote (\"Reduce LTV by 5% from maximum on Investment Properties\") is read as lowering the "
             "starred property cap by 5% for investment. Confirm with underwriting.")


def apply_rules(rules: list[dict], facts: dict, file: str) -> dict:
    """Returns applied adjustments (for apply_adjustments), "If ..." lines for unknown facts, and confirm notes."""
    applied, unknown, confirm, seen = [], [], [], set()
    for r in rules:
        state = evaluate(r["when"], facts)
        if state is None:
            missing = sorted({k for k in r["when"] if k != "any" and facts.get(k) is None}
                             | {k for k in r["when"].get("any", {}) if facts.get(k) is None})
            if facts.get("property") == "condo" and r["when"].get("property") in CONDO_KINDS:
                missing = [f"which kind of condo ({r['label']})"]
            if tuple(missing) in seen:   # one "If" line per unknown fact (rent free has three rules)
                continue
            seen.add(tuple(missing))
            effect = "ineligible" if r["kind"] == "ineligible" else (
                f"{r['value']}% reduction" if r["kind"] == "reduction" else f"max {_value_text(r)}")
            # the sheet's own words go in the line: the viewer finds a quote word for word, a label like
            # "DTI over 50%" it couldn't find at all
            words = re.sub(r"\s*\|\s*", " ", r["quote"]).strip()
            said = f' (sheet: "{words}")' if len(words) <= 90 else ""
            unknown.append(f"If {r['label']}: {effect}{said} ({file}, p. {r['page']})"
                           + (f". Need: {missing[0]}" if missing[0].startswith("which kind") else ""))
            continue
        if not state:
            continue
        value = 0 if r["kind"] == "ineligible" else rule_value(r, facts)
        if value is None:
            continue
        applied.append({"reason": r["label"], "kind": r["kind"], "value": value, "file": file,
                        "page": r["page"], "quote": r["quote"], "id": r["id"]})
        if r.get("investment_minus") and facts.get("occupancy") == "investment" and STAR_NOTE not in confirm:
            confirm.append(STAR_NOTE)
        if r.get("confirm"):
            confirm.append(r["confirm"])
    return {"applied": applied, "unknown": unknown, "confirm": confirm}


def _value_text(r: dict) -> str:
    v = r["value"]
    if isinstance(v, dict):
        return ", ".join(f"{k.replace('_', ' ')} {n}%" for k, n in v.items()) + (
            f" (minus {r['investment_minus']}% on investment)" if r.get("investment_minus") else "")
    return f"{v}%"


# ---------- files and approval ----------

def rules_path(program: str) -> Path:
    return RULES_DIR / f"{program}.json"


def _approval_path(program: str) -> Path:
    return APPROVED_DIR / f"overlays__{program}.json"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9%$.]+", " ", s.lower()).strip()


def load(program: str, doc: dict | None) -> dict | None:
    """The program's rules plus their status: approved, unapproved, changed (rules or PDF edited since
    approval) or bad_quotes (a quote isn't in the document word for word). None if the program has no rules."""
    p = rules_path(program)
    if not p.exists():
        return None
    raw = p.read_bytes()
    data = json.loads(raw)
    data["rules_hash"] = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()   # git may swap line endings
    data["doc_hash"] = doc["hash"] if doc else ""
    text = _norm("\n".join(pg["text"] for pg in doc["pages"])) if doc else ""
    data["bad_quotes"] = [r["id"] for r in data["rules"] if _norm(r["quote"]) not in text]

    status = "unapproved"
    ap = _approval_path(program)
    if ap.exists():
        a = json.loads(ap.read_text())
        same = a["rules_hash"] == data["rules_hash"] and a["doc_hash"] == data["doc_hash"]
        status = "approved" if same else "changed"
    if data["bad_quotes"] or not doc:
        status = "bad_quotes"
    data["status"] = status
    return data


def for_program(program: str, docs: list[dict]) -> dict | None:
    """load() with the program's source document picked out of its loaded docs."""
    p = rules_path(program)
    if not p.exists():
        return None
    source = json.loads(p.read_bytes())["source"]
    return load(program, next((d for d in docs if d["path"].name == source), None))


def approve(program: str, data: dict):
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    _approval_path(program).write_text(json.dumps({
        "program": program, "rules_hash": data["rules_hash"], "doc_hash": data["doc_hash"],
        "approved_at": datetime.now().isoformat(timespec="seconds")}, indent=1))


def rules_text(data: dict) -> list[str]:
    """One plain line per rule, for the Verify page."""
    out = []
    for r in data["rules"]:
        cond = ", ".join(_cond_text(k, v) for k, v in r["when"].items())
        effect = "Ineligible" if r["kind"] == "ineligible" else (
            f"Minus {r['value']}%" if r["kind"] == "reduction" else f"Max {_value_text(r)}")
        out.append((r["label"], cond, effect, r["quote"], r["page"]))
    return out


def _cond_text(k: str, v) -> str:
    if k == "any":
        return "(" + " or ".join(_cond_text(a, b) for a, b in v.items()) + ")"
    name = k.replace("_", " ")
    if isinstance(v, bool):
        return name if v else f"not {name}"
    if isinstance(v, list):
        return f"{name} is {' or '.join(map(str, v))}"
    if isinstance(v, dict):
        ops = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
        parts = [f"{name} {ops[o]} {n}" for o, n in v.items() if o in ops]
        if "not" in v:
            parts.append(f"{name} not {' or '.join(v['not'])}")
        return " and ".join(parts)
    return f"{name} is {v}"
