"""County loan limits, read by Python, never by the model.

Conforming: FHFA's county list (limits/fhfa_<year>.xlsx, the "HERA-based final flat" file from fhfa.gov). A
Conventional sheet's High Balance column only applies up to the county's own limit; above it the loan is jumbo.
FHA: HUD's national floor and ceiling only (HUD's county lookup has no bulk download), so Python can say a loan is
under every county's FHA limit or over all of them, and points to HUD's lookup in between.
Where the property is: "Maricopa County, AZ", "Maricopa County" (every state with one is listed), or a ZIP code
("85251", "zip 85251") through the Census ZIP to county file (limits/zip_county.csv).
"""
import csv
import functools
import re
from pathlib import Path

from guidebot import config

DIR = config.ROOT / "limits"
HUD_LOOKUP = "https://entp.hud.gov/idapp/html/hicost1.cfm"
# HUD's CY2026 forward limits (FHA INFO 2025-xx): 65% and 150% of FHFA's baseline, by units.
FHA_FLOOR = {1: 541_287, 2: 693_050, 3: 837_700, 4: 1_041_125}
FHA_CEILING = {1: 1_249_125, 2: 1_599_375, 3: 1_933_200, 4: 2_402_625}
FHA_SPECIAL = {"AK", "HI", "GU", "VI"}   # higher ceilings there; Python doesn't rule on them
FHA_YEAR = 2026

STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
          "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
          "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
          "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
          "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
          "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
          "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
          "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
          "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
          "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR", "guam": "GU",
          "virgin islands": "VI"}


@functools.lru_cache(maxsize=1)
def _fhfa() -> tuple[int, dict]:
    """(year, {fips: {"name", "state", "limits": {units: amount}}})."""
    import pandas as pd
    path = max(DIR.glob("fhfa_*.xlsx"))
    df = pd.read_excel(path, header=1, dtype=str)
    out = {}
    for r in df.itertuples(index=False):
        if not (isinstance(r[0], str) and r[0].isdigit()):
            continue
        out[r[0].zfill(2) + r[1].zfill(3)] = {"name": r[2].title(), "state": r[3],
                                              "limits": {u: int(float(r[4 + u])) for u in range(1, 5)}}
    return int(re.search(r"\d{4}", path.stem)[0]), out


@functools.lru_cache(maxsize=1)
def _zips() -> dict:
    out: dict = {}
    with open(DIR / "zip_county.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["zip"], []).append((r["county_fips"], float(r["share"])))
    return out


def year() -> int:
    return _fhfa()[0]


def baseline(units: int = 1) -> int:
    return min(c["limits"][units] for c in _fhfa()[1].values())


def ceiling(units: int = 1) -> int:
    """The high-cost ceiling outside Alaska, Hawaii, Guam and the Virgin Islands."""
    return max(c["limits"][units] for c in _fhfa()[1].values() if c["state"] not in FHA_SPECIAL)


_COUNTY = re.compile(r"\b((?:[A-Z][a-zA-Z.'\-]*\s){0,3}?[A-Z][a-zA-Z.'\-]*)\s+(County|Parish|Borough)\b"
                     r"(?:,?\s+([A-Z]{2}\b|[A-Z][a-z]+(?:\s[A-Z][a-z]+)?))?")
# A 5-digit number is a ZIP only after "zip", a state code, a comma or "in" ("loan amount 75000" isn't one).
_ZIP = re.compile(r"(?:\bzip(?:\s*code)?\s*:?\s*|\b[A-Z]{2}\s+|,\s*|\bin\s+)(\d{5})\b(?![,\d])", re.I)


def counties(text: str) -> list[dict]:
    """Counties the text names, by county name or ZIP. Each: {"fips", "name", "state", "limits", "how"}."""
    _, by_fips = _fhfa()
    found = {}
    for m in _COUNTY.finditer(text):
        name, kind, st = m[1].strip(), m[2], (m[3] or "").strip()
        st = STATES.get(st.lower(), st if len(st) == 2 and st.isupper() else "")
        full = f"{name} {kind}".lower()
        for words in (full, full.split(" ", 1)[-1]):   # "in Maricopa County": drop a leading word if needed
            hits = {f: c for f, c in by_fips.items() if c["name"].lower() == words and (not st or c["state"] == st)}
            if hits:
                found.update({f: {**c, "fips": f, "how": f"{c['name']}, {c['state']}"} for f, c in hits.items()})
                break
    for m in _ZIP.finditer(text):
        z = m[1]
        for fips, share in _zips().get(z, []):
            if fips in by_fips and share >= 0.05:
                c = by_fips[fips]
                found.setdefault(fips, {**c, "fips": fips, "how": f"ZIP {z}: {c['name']}, {c['state']}"
                                        + (f" ({share:.0%} of the ZIP)" if share < 0.95 else "")})
    return list(found.values())


def conforming_check(text: str, loan: int, units: int, sheet: list | None = None) -> dict:
    """{"counties", "over": [counties whose limit the loan is over], "lines": markdown bullets}.
    sheet: the program sheet's own [conforming, high balance] limits. NDM's sheets can run ahead of FHFA (an early
    $845,000 baseline in November 2026), and the sheet wins: a county at FHFA's baseline gets the sheet's conforming
    limit, a high-cost county gets the higher of FHFA's limit and the sheet's conforming, never above the sheet's top."""
    yr, places = year(), counties(text)
    units = units if units in (1, 2, 3, 4) else 1
    base, lines = baseline(units), []
    conf = int(sheet[0]) if sheet else base
    top = int(sheet[1]) if sheet and len(sheet) > 1 else None
    if not places:
        if loan > conf:
            lines.append(f"${loan:,} is over the conforming limit (${conf:,}, {units} unit). That's High Balance only in a "
                         f"county whose limit covers it. Name the county or ZIP and I'll check it.")
        return {"counties": [], "over": [], "lines": lines}
    over = []
    for c in places:
        fhfa = c["limits"][units]
        cap = conf if fhfa == base else max(conf, fhfa)
        cap = min(cap, top) if top else cap
        if loan > cap:
            over.append(c)
            verdict = "over it: jumbo, not conventional"
        else:
            verdict = "fits (High Balance)" if loan > conf else "fits (conforming)"
        src = (f"FHFA {yr} baseline county; the sheet's conforming limit applies" if fhfa == base and conf != base
               else f"FHFA {yr}, {'baseline' if fhfa == base else 'high-cost'}")
        lines.append(f"{c['how']}: limit ${cap:,} ({units} unit; {src}). ${loan:,} {verdict}.")
    if sheet and conf != base:
        lines.append(f"The sheet's conforming limit (${conf:,}) is ahead of FHFA's official {yr} baseline (${base:,}). "
                     f"High-cost county limits here are FHFA's {yr} figures until FHFA posts the new year.")
    return {"counties": places, "over": over, "lines": lines}


def fha_lines(text: str, loan: int, units: int) -> list[str]:
    units = units if units in (1, 2, 3, 4) else 1
    lo, hi, yr = FHA_FLOOR[units], FHA_CEILING[units], FHA_YEAR
    places = counties(text)
    special = any(c["state"] in FHA_SPECIAL for c in places)
    where = f" in {', '.join(c['how'] for c in places)}" if places else ""
    if loan <= lo:
        return [f"${loan:,} is under the {yr} FHA floor (${lo:,}, {units} unit), so it's within the FHA limit in every county."]
    if loan > hi and not special:
        return [f"${loan:,} is over the {yr} FHA ceiling (${hi:,}, {units} unit), so it's over the FHA limit in every "
                f"county outside AK, HI, Guam and the Virgin Islands."]
    return [f"${loan:,} is between the {yr} FHA floor (${lo:,}) and ceiling (${hi:,}) for {units} unit. The county's "
            f"own FHA limit decides it{where}: check HUD's lookup ({HUD_LOOKUP})."]


ASKS = re.compile(r"\b(loan|conforming|high[- ]balance|fha|county)\s+(loan\s+)?limits?\b|\blimits?\s+(for|in)\b.*\b(county|zip|\d{5})",
                  re.I)


def answer(question: str) -> str | None:
    """"What's the conforming limit in Los Angeles County?" answered from the files, or None when the question
    isn't about a limit or names no county."""
    if not ASKS.search(question):
        return None
    places = counties(question)
    if not places:
        return None
    yr = year()
    lines = [f"**{yr} conforming loan limits (FHFA)**", "", "| County | 1 unit | 2 units | 3 units | 4 units |",
             "|---|---|---|---|---|"]
    for c in places:
        lines.append(f"| {c['how']} | " + " | ".join(f"${c['limits'][u]:,}" for u in range(1, 5)) + " |")
    lines += ["", f"Baseline (most counties): ${baseline(1):,}. Above the baseline and up to the county's limit is High "
              "Balance (Freddie: Super Conforming); above the county's limit is jumbo.", "",
              f"**FHA ({FHA_YEAR})**: floor ${FHA_FLOOR[1]:,}, ceiling ${FHA_CEILING[1]:,} (1 unit). Each county's FHA "
              f"limit is somewhere in that range; HUD's lookup has it: {HUD_LOOKUP}"]
    return "\n".join(lines)
