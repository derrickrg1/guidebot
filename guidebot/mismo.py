"""Loan file import (2026-11-16): a MISMO 3.x XML export (what Encompass, Arive and most LOSs save as "MISMO 3.4")
attached with the + becomes the same deal sentence the scenario form writes, so the deal reader takes it as is.

Only the deal facts come out: credit score, loan amount, price/value, occupancy, purpose, property type, units,
ZIP. Names, SSNs, addresses and income stay in the file and never reach a model.

The score is the usual qualifying one: each borrower's middle score (or lower of two), then the lowest borrower's.
"""
import re
import xml.etree.ElementTree as ET


class NotMismo(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _all(root, name: str) -> list[str]:
    return [(e.text or "").strip() for e in root.iter() if _local(e.tag) == name and (e.text or "").strip()]


def _first(root, *names: str) -> str:
    for n in names:
        v = _all(root, n)
        if v:
            return v[0]
    return ""


def _money(s: str) -> int | None:
    try:
        return int(round(float(s.replace(",", "").replace("$", ""))))
    except ValueError:
        return None


def _scores(root) -> list[int]:
    """Qualifying score per borrower: middle of three (lower of two), from each PARTY that has scores."""
    per = []
    for party in (e for e in root.iter() if _local(e.tag) == "PARTY"):
        s = sorted(int(v) for v in _all(party, "CreditScoreValue") if v.isdigit() and 300 <= int(v) <= 900)
        if s:
            per.append(s[(len(s) - 1) // 2] if len(s) >= 3 else s[0])
    if not per:   # scores outside PARTY (some exports put them under CREDIT_SCORES on the loan)
        s = sorted(int(v) for v in _all(root, "CreditScoreValue") if v.isdigit() and 300 <= int(v) <= 900)
        per = [s[(len(s) - 1) // 2] if len(s) >= 3 else s[0]] if s else []
    return per


def _subject_zip(root) -> str:
    for e in root.iter():
        if _local(e.tag) in ("SUBJECT_PROPERTY", "PROPERTY"):
            z = _first(e, "PostalCode")
            if z:
                return z[:5]
    return ""


def facts(data: bytes) -> dict:
    head = data[:4096].decode("utf-8", "ignore")
    if "<!DOCTYPE" in head.upper() or "<!ENTITY" in head.upper():
        raise NotMismo("XML with a DOCTYPE isn't accepted.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise NotMismo("not readable XML")
    if _local(root.tag) != "MESSAGE" and not any(_local(e.tag) == "DEAL" for e in root.iter()):
        raise NotMismo("not a MISMO file")
    f: dict = {}
    sc = _scores(root)
    if sc:
        f["fico"] = min(sc)
        f["borrower_scores"] = sc
    f["loan"] = _money(_first(root, "BaseLoanAmount", "NoteAmount", "LoanAmountRequested"))
    f["price"] = _money(_first(root, "PurchasePriceAmount", "SalesContractAmount"))
    f["value"] = _money(_first(root, "PropertyEstimatedValueAmount", "PropertyValuationAmount", "AppraisedValueAmount"))
    purpose = _first(root, "LoanPurposeType").lower()
    cash = _first(root, "RefinanceCashOutDeterminationType").lower()
    f["purpose"] = ("purchase" if purpose == "purchase" else "cash-out refinance" if cash == "cashout"
                    else "rate and term refinance" if purpose == "refinance" else "")
    use = _first(root, "PropertyUsageType").lower()
    f["occupancy"] = {"primaryresidence": "primary residence", "secondhome": "second home",
                      "investment": "investment property", "investor": "investment property"}.get(use, "")
    units = _first(root, "FinancedUnitCount", "PropertyUnitCount")
    f["units"] = int(units) if units.isdigit() else None
    legal = _first(root, "ProjectLegalStructureType").lower()
    pud = _first(root, "PUDIndicator", "PropertyInProjectIndicator").lower() == "true" and not legal
    f["property"] = ("condo" if legal in ("condominium", "condo") else
                     f"{f['units']}-unit" if (f["units"] or 1) > 1 else "PUD" if pud else "single family")
    f["zip"] = _subject_zip(root)
    f["state"] = _first(root, "StateCode")
    return f


def sentence(f: dict) -> str:
    usd = lambda n: f"${n:,}"
    parts = []
    if f.get("fico"):
        parts.append(f"{f['fico']} FICO")
    if f.get("loan"):
        parts.append(f"{usd(f['loan'])} loan amount")
    if f.get("purpose") == "purchase" and f.get("price"):
        parts.append(f"purchase price {usd(f['price'])}")
    elif f.get("value"):
        parts.append(f"value {usd(f['value'])}")
    parts += [x for x in (f.get("occupancy"), f.get("purpose"), f.get("property")) if x]
    if re.fullmatch(r"\d{5}", f.get("zip") or ""):
        parts.append(f"ZIP {f['zip']}")
    out = "Loan file (MISMO): " + ", ".join(parts) if parts else "Loan file (MISMO): no deal facts found."
    if len(f.get("borrower_scores") or []) > 1:
        out += f"\n(Qualifying score is the lowest borrower's middle score: {', '.join(map(str, f['borrower_scores']))}.)"
    missing = [n for n, k in (("credit score", "fico"), ("loan amount", "loan"), ("occupancy", "occupancy"),
                              ("purpose", "purpose")) if not f.get(k)]
    if missing:
        out += f"\n(Not in the file: {', '.join(missing)}.)"
    return out


def read(data: bytes) -> str:
    return sentence(facts(data))
