"""Program-level LTV questions asked in Guidelines mode: "Max LTV for a 3-unit investment cash-out on Freddie?",
"FHA cash-out max LTV?", "What's the max LTV for a cash-out with a DSCR of 0.9?".

The AI may never state an LTV, and before this nothing else answered, so an underwriter got "switch to Scenario
mode" (or "pick a program" with the program already picked). Python reads the approved grids instead: every row
the stated facts fit, its cell, a chip that opens the page with the row marked, and what the number still depends
on. A specific deal (FICO and loan amount) still goes to the matrix lookup, never here.
"""
import re

from guidebot import conv_matrix, gov_matrix
from guidebot.answer import RawScenario, fill_scenario, matrix_cite
from guidebot.matrix_lookup import dimension_columns, to_number, value_columns

ASKS = re.compile(r"\b(max(imum)?\s*(allowable\s*)?(ltv|cltv|tltv|hcltv|loan[- ]to[- ]value)|(ltv|cltv)\s*(limit|max|cap)"
                  r"|(how high|highest)\b.{0,25}\b(ltv|cltv)|(what|whats|what's)\b.{0,15}\b(ltv|cltv)\b)", re.I)
_DSCR = re.compile(r"\bdscr\s*(?:of|is|at|:)?\s*(\d?\.\d+|\d(?:\.\d+)?)\b", re.I)
# Short names an AE uses for a sheet ("on Freddie"): named_programs needs the whole name
ALIASES = {"freddie": "Conventional Freddie Mac", "fannie": "Conventional Fannie Mae"}
_OCC_WORDS = {"Primary Residence": "primary", "Second Home": "second", "Investment Property": "invest"}
_PUR_WORDS = {"Purchase": r"purch", "Rate/Term Refinance": r"rate|r\s*&\s*t|r/t", "Cash Out Refinance": r"cash"}


def asks_ltv(question: str) -> bool:
    """Asks what the max LTV is, without a deal to price (no FICO and loan amount together)."""
    raw = fill_scenario(RawScenario(), question)
    return bool(ASKS.search(question)) and not (raw.fico and raw.loan_amount)


def aliased(question: str, programs: list[str]) -> list[str]:
    q = question.lower()
    return [p for w, p in ALIASES.items() if re.search(rf"\b{w}\b", q) and p in programs]


def _units_said(question: str) -> bool:
    return bool(re.search(r"\b[1-8]\s*[- ]?units?\b|duplex|triplex|fourplex|quadplex|[2-4]-family|manufactured",
                          question, re.I))


def _fmt(v: float) -> str:
    return f"{v:g}%"


# ---------- loan amount x FICO grids (Express, Ignite) ----------
def _grid_columns(m: dict, raw, dscr: float | None) -> list[str]:
    cols = value_columns(m["df"])
    occ = _OCC_WORDS.get(raw.occupancy or "")
    if occ and any(any(w in c.lower() for w in _OCC_WORDS.values()) for c in cols):
        cols = [c for c in cols if occ in c.lower()]
    if raw.purpose:
        cols = [c for c in cols if re.search(_PUR_WORDS[raw.purpose], c, re.I)]
    if dscr is not None:
        title = (m["df"].attrs.get("title") or "").lower()
        band = lambda t: "high" if re.search(r"1\.00|>\s*1", t) else "low" if re.search(r"\.75", t) else None
        want = "high" if dscr >= 1 else "low"
        if any(band(c.lower()) for c in cols):
            cols = [c for c in cols if band(c.lower()) == want]
        elif band(title) and band(title) != want:
            cols = []
    return cols


def _grid_rows(m: dict, raw, dscr: float | None) -> list[str]:
    df = m["df"]
    dims = dimension_columns(df)
    if dims != ["Loan Amount", "FICO"]:
        return []
    out = []
    for col in _grid_columns(m, raw, dscr):
        vals = [(i, to_number(str(df.iloc[i][col]).strip("*%"))) for i in range(len(df))]
        vals = [(i, v) for i, v in vals if v is not None and str(df.iloc[i][col]).strip("*").endswith("%")]
        if not vals:
            out.append(f"| {col} | **Not allowed** (N/A on every row) | | ({m['file']}, p. {m['page']}) |")
            continue
        top = max(v for _, v in vals)
        low = min(v for _, v in vals)
        best = [i for i, v in vals if v == top]
        # where the top number is: each loan amount row group, from its lowest FICO
        where = {}
        for i in best:
            r = df.iloc[i]
            fico = to_number(r["FICO"]) or 0
            if r["Loan Amount"] not in where or fico < where[r["Loan Amount"]][0]:
                where[r["Loan Amount"]] = (fico, i)
        groups = list(where.items())
        said = "; ".join(f"{amt}, FICO {f:g}+" for amt, (f, _) in groups[:2]) + (" …" if len(groups) > 2 else "")
        i0 = groups[0][1][1]
        cite = matrix_cite(m, {"i": i0, "trace": [f"Column: {col}"]})
        rng = f"**{_fmt(top)}**" + (f" (down to {_fmt(low)})" if low < top else "")
        out.append(f"| {col} | {rng} | {said} | {cite} |")
    return out


# ---------- Conventional sheets ----------
def _conv_rows(m: dict, raw, question: str) -> tuple[list[str], set[str]]:
    df = m["df"]
    units, mh = conv_matrix.deal_units(raw, question)
    mine = df
    if raw.occupancy:
        mine = mine[mine["Occupancy"] == raw.occupancy]
    if raw.purpose:
        mine = mine[mine["Loan Purpose"].map(lambda c: raw.purpose in conv_matrix.purposes(c))]
    if _units_said(question):
        mine = mine[mine["Units"].map(lambda c: conv_matrix.units_fit(c, units, mh))]
    has_high = "High Balance Max LTV" in df.columns
    label = df.attrs.get("high_label") or "High Balance"
    out, marks = [], set()
    for idx, r in mine.iterrows():
        i = int(df.index.get_loc(idx))
        cells = []
        for col, name in (("Conforming Max LTV", "Conforming"), ("High Balance Max LTV", label)):
            if col not in df.columns:
                continue
            ltv, mk, extra = conv_matrix.ltv_of(r[col])
            marks.update(mk)
            cells.append(f"{name}: **{_fmt(ltv)}**" + (f" ({extra})" if extra else "") if ltv is not None
                         else f"{name}: not allowed")
        unit = f"{r['Units']} unit(s)" if re.fullmatch(r"[\d-]+", str(r["Units"])) else r["Units"]
        cite = conv_matrix.cite(m, {"i": i, "col": "Conforming Max LTV"})
        out.append(f"| {r['Occupancy']}, {r['Loan Purpose']}, {unit} | {'; '.join(cells)} | "
                   f"FICO {r['Minimum FICO']}+ | {cite} |")
    return out, marks


# ---------- FHA / VA sheets ----------
def _gov_rows(m: dict, raw) -> list[str]:
    df = m["df"]
    mine = df if not raw.purpose else df[df["Loan Purpose"].map(gov_matrix._purpose) == raw.purpose]
    out = []
    for _, r in mine.iterrows():
        cap = f", loan up to {r['Maximum Loan Amount']}" if "Maximum Loan Amount" in df.columns else ""
        out.append(f"| {r['Loan Purpose']} | **{r['Max LTV']}** | FICO {r['Minimum FICO']}+{cap} | "
                   f"({m['file']}, p. {m['page']}) |")
    return out


def overview(question: str, programs: dict[str, list[dict]]) -> str | None:
    """programs: {name: matrices (library.matrices)}. Markdown, or None when no approved grid can answer."""
    raw = fill_scenario(RawScenario(), question)
    d = _DSCR.search(question)
    dscr = float(d[1]) if d else None
    parts, blocked, gov_missing, conv_marks = [], [], [], {}
    for prog, mats in programs.items():
        if not mats:
            if prog.startswith(("FHA", "VA")):
                gov_missing.append(prog)
            continue
        bad = [m for m in mats if m["status"] != "verified"
               or (m.get("reference") or {}).get("status") == "mismatch"]
        if bad:
            blocked.append(prog)
            continue
        conv = conv_matrix.conv_mats(mats)
        if conv:
            for m in conv:
                rows, marks = _conv_rows(m, raw, question)
                if rows:
                    parts.append((prog, "| Row | Max LTV | Min FICO | Source |", rows))
                    notes = [n for n in conv_matrix.footnotes(m["df"].attrs.get("notes", ""))
                             if n[:1] in marks and n[1:2] == ":"]
                    conv_marks[prog] = notes
            continue
        if gov_matrix.is_gov(mats):
            rows = [r for m in mats for r in _gov_rows(m, raw)]
            if rows:
                parts.append((prog, "| Purpose | Max LTV | Needs | Source |", rows))
            continue
        rows = [r for m in mats for r in _grid_rows(m, raw, dscr)]
        if rows:
            parts.append((prog, "| Column | Max LTV | Where (loan amount, FICO) | Source |", rows))
    if not parts:
        # nothing to show: the guideline answer runs as before ("FHA minimum credit score and max LTV?" lost its
        # credit score half to a bare "no LTV table yet")
        return None
    said = ", ".join(x for x in (raw.occupancy, raw.purpose, dscr is not None and f"DSCR {dscr:g}") if x)
    out = [f"**Max LTV from the approved matrices**{f' ({said})' if said else ''}. Python read these off the "
           "grids; the AI doesn't write LTVs.", ""]
    # "CLTV limit with a community second?": the answer is a footnote, so it goes first, in the sheet's words
    if re.search(r"second|secondary financing|subordinat|\b(cltv|tltv|hcltv)\b", question, re.I):
        lead = [f"- {prog}, footnote {n}" for prog, notes in conv_marks.items() for n in notes
                if re.search(r"second|subordinat|CLTV|TLTV", n, re.I)]
        if lead:
            out += ["**On the sheet**", *lead, ""]
    for prog, head, rows in parts:
        out += [f"**{prog}**", "", head, "|---|---|---|---|", *rows, ""]
        if conv_marks.get(prog):
            out += [f"- {n}" for n in conv_marks[prog]] + [""]
    if blocked:
        out.append("Not shown, matrix not approved or doesn't match the site's copy: " + ", ".join(blocked) + ".")
    if gov_missing:
        out.append(f"{', '.join(gov_missing)}: the approved copies have no LTV table yet (Re-read the PDF and "
                   "approve it on the admin page).")
    if parts:
        out.append("*The top number needs the FICO and loan amount shown. Overlays (condo, rural, credit events, "
                   "reserves, first-time buyer) can lower it. Give me FICO, loan amount, occupancy and purpose "
                   "for the exact number on a deal.*")
    return "\n".join(out).strip()
