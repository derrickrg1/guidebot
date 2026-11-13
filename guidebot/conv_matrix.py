"""Conventional eligibility tables (Fannie Mae, Freddie Mac, and their affordable and refi programs) and the lookup
on them. Python only, no model.

NDM's Conventional sheets put every occupancy in one table on page 1: a "Primary Residence" / "Second Home" /
"Investment Property" bar, a header row (Loan Purpose, Minimum Fico, sometimes Max DTI, Units, a Conforming and
often a High Balance or Super Conforming max LTV column), then rows by units. Loan purpose and FICO are merged
down over several unit rows. The loan limits by unit count sit further down the same table, and they decide which
LTV column a deal reads: up to the conforming limit it's Conforming, up to the high balance limit it's High
Balance, over that the program doesn't take it.

Rows are read with each cell placed under the header column it overlaps most, so a merged or shifted cell still
lands in the right column. Footnote markers stay on (97%¹, "95% 1, 2") and every footnote on the sheet goes out
with the answer, since they change the number (105% CLTV with a community second, conforming limits only)."""
import re

import pandas as pd

from guidebot.extract_pdf import _clean
from guidebot.gov_matrix import SUP, _lead_number, _plain, notes_md

OCCUPANCY = {"Primary Residence": "Primary Residence", "Second Home": "Second Home",
             "Investment Property": "Investment Property"}
HIGH = re.compile(r"high\s*balance|super", re.I)


def _col_name(label: str) -> str:
    s = label.lower()
    if "purpose" in s:
        return "Loan Purpose"
    if "fico" in s or s.strip() == "minimum":
        return "Minimum FICO"
    if "dti" in s or s.strip() == "max":
        return "Max DTI"
    if "unit" in s:
        return "Units"
    if HIGH.search(s):
        return "High Balance Max LTV"
    if "conforming" in s:
        return "Conforming Max LTV"
    return label


def _overlap(a, b) -> float:
    return max(0, min(a[1], b[1]) - max(a[0], b[0])) / max(1e-6, min(a[1] - a[0], b[1] - b[0]))


def parse_table(table) -> pd.DataFrame | None:
    """A pdfplumber table -> one row per (occupancy, purpose, units), or None if it isn't a Conventional sheet
    (it needs an occupancy bar and a Conforming max LTV column)."""
    x0, _, x1, _ = table.bbox
    rows = []
    for r, row in zip(table.extract(), table.rows):
        cells = sorted([(bb, _clean(t)) for t, bb in zip(r, row.cells) if bb and _clean(t)], key=lambda c: c[0][0])
        if cells:
            rows.append(cells)
    full = lambda bb: bb[2] - bb[0] > 0.6 * (x1 - x0)

    titles, notes, side, out, limits, marks, colx = [], [], [], [], {}, [], {}
    page_h = table.page.height
    occ, cols, high_label, carry, in_body, done = "", None, "", {}, False, False
    for cells in rows:
        texts = [t for _, t in cells]
        # a cell nested inside another repeats part of its text (Refi Now's footnote 2): keep the outer one
        joined = " ".join(t for i, t in enumerate(texts) if not any(t in o for j, o in enumerate(texts) if j != i
                                                                    and len(o) > len(t)))
        m = re.match(r"^([1-4]) Units?$", texts[0])
        amounts = list(dict.fromkeys(a for a in (_lead_number(t) for t in texts if t.startswith("$")) if a))
        if m and amounts:   # loan limits: "1 Unit | $845,000 | $1,267,500" (some sheets repeat each cell)
            limits.setdefault(m[1], amounts[:2])
            continue
        if "Matrix Updates" in joined:   # the color legend ends the grid and its footnotes
            done = True
        if done:
            continue
        if len(cells) == 1 and full(cells[0][0]) and texts[0] in OCCUPANCY:
            occ, cols, in_body = OCCUPANCY[texts[0]], None, False
            continue
        if not occ:
            if len(cells) == 1 and full(cells[0][0]):
                titles.append(texts[0])
            continue
        if re.search(r"loan purpose", joined, re.I):   # header: one column per x-range, duplicates merged
            cols = []
            for bb, t in cells:
                col = next((c for c in cols if _overlap((bb[0], bb[2]), c["x"]) > 0.5), None)
                if col:
                    col["x"] = (min(col["x"][0], bb[0]), max(col["x"][1], bb[2]))
                    col["label"] += "" if t in col["label"] else " " + t
                else:
                    cols.append({"x": (bb[0], bb[2]), "label": t})
            carry = {}
            continue
        if cols is None:
            continue
        if not in_body and not re.search(r"\d|NA\b", joined):   # second header line: "Fico* | Max LTV/CLTV"
            for bb, t in cells:
                col = max(cols, key=lambda c: _overlap((bb[0], bb[2]), c["x"]))
                if t not in col["label"]:
                    col["label"] += " " + t
            continue
        note = (texts[0].startswith("*") or re.match(rf"^[1-9{SUP}]\s*[A-Z]", texts[0])
                and not re.match(r"^[1-4]\s*units?\b", texts[0], re.I) and (full(cells[0][0]) or len(texts[0]) > 25))
        if note or notes or side:   # footnotes under the grid, up to the "Matrix Updates in Yellow" legend
            # The refi sheets put the "*Minimum Fico" note in a right-hand box beside footnote 2's two lines:
            # read that box on its own so it doesn't land in the middle of footnote 2.
            left = lambda bb: full(bb) or bb[0] + bb[2] < x0 + x1   # full width, or centered left of the middle
            for bucket, part in ((notes, [t for (bb, t) in cells if left(bb)]),
                                 (side, [t for (bb, t) in cells if not left(bb)])):
                part = [t for i, t in enumerate(part) if not any(t in o for j, o in enumerate(part) if j != i
                                                                 and len(o) > len(t))]
                text = " ".join(part)
                if text and text not in " ".join(notes + side):   # a wrapped line also handed over on its own
                    bucket.append(text)
            in_body = False
            continue
        names = [_col_name(c["label"]) for c in cols]
        if "Conforming Max LTV" not in names:
            continue
        high_label = high_label or next((c["label"] for c in cols if HIGH.search(c["label"])), "")
        rec, ubb = {}, None
        for bb, t in cells:
            i = max(range(len(cols)), key=lambda k: _overlap((bb[0], bb[2]), cols[k]["x"]))
            if _overlap((bb[0], bb[2]), cols[i]["x"]) > 0.3:
                rec.setdefault(names[i], t.lstrip(". ").strip())
                ubb = bb if names[i] == "Units" and ubb is None else ubb
        if not rec.get("Units") and not any(k.endswith("Max LTV") for k in rec):
            continue
        in_body = True
        for k in ("Loan Purpose", "Minimum FICO", "Max DTI"):   # merged down over the unit rows
            if rec.get(k):
                carry[k] = rec[k]
            elif k in carry:
                rec[k] = carry[k]
        if "Loan Purpose" in rec and "Minimum FICO" in rec and "Units" in rec:
            out.append({"Occupancy": occ, **rec})
            # where the row sits on the page (PDF units, y up), so the viewer marks this row and no other
            ltv = [c["x"] for c, n in zip(cols, names) if n.endswith("Max LTV")]
            ux = next(c["x"] for c, n in zip(cols, names) if n == "Units")
            marks.append([round(v, 1) for v in (ux[0], page_h - ubb[3], max(x[1] for x in ltv), page_h - ubb[1])])
            colx.update({n: [round(c["x"][0], 1), round(c["x"][1], 1)] for c, n in zip(cols, names)
                         if n.endswith("Max LTV")})
    if not out:
        return None
    columns = ["Occupancy", "Loan Purpose", "Minimum FICO", "Max DTI", "Units", "Conforming Max LTV",
               "High Balance Max LTV"]
    columns = [c for c in columns if any(c in r for r in out)]
    df = pd.DataFrame([[r.get(c, "") for c in columns] for r in out], columns=columns)
    df.attrs.update(kind="conv", title=" · ".join(titles[:2]), dims=["Occupancy", "Loan Purpose", "Units"],
                    notes=" ".join(notes + side), limits={k: v for k, v in sorted(limits.items())},
                    high_label=re.sub(r"\s*Max.*", "", high_label, flags=re.I).strip(), marks=marks, colx=colx)
    return df


# ---------- lookup ----------
def purposes(cell: str) -> set[str]:
    """'Purchase or No Cash-out Refinance' -> {Purchase, Rate/Term Refinance}; 'Limited Cash-Out' -> Rate/Term
    (Fannie's name for no cash-out); 'Cash-Out' -> Cash Out."""
    s = cell.lower()
    out = set()
    if "purchase" in s:
        out.add("Purchase")
    if re.search(r"(no|limited)\s*cash", s):
        out.add("Rate/Term Refinance")
    elif "cash" in s:
        out.add("Cash Out Refinance")
    return out


def units_fit(cell: str, units: int, manufactured: bool) -> bool:
    s = cell.lower()
    if "manufactured" in s:
        return manufactured
    if manufactured:
        return False
    nums = [int(n) for n in re.findall(r"\d", _plain(s))[:2]]
    if not nums:
        return False
    lo, hi = (nums[0], nums[1]) if len(nums) == 2 and re.search(r"\d\s*-\s*\d", s) else (nums[0], nums[0])
    return lo <= units <= hi


def ltv_of(cell: str) -> tuple[float | None, list[str], str]:
    """'Fixed 97%¹' -> (97, ['1'], 'fixed rate only'); '95% 1, 2' -> (95, ['1', '2'], '');
    'Fixed 97%/105%¹,²' -> (97, ['1', '2'], 'fixed rate only; 105% TLTV'); 'NA' -> (None, [], '')."""
    m = re.search(rf"(\d+(?:\.\d+)?)[{SUP}]*\s*%", cell)   # "95¹%": the marker can sit before the %
    if not m:
        return None, [], ""
    marks = re.findall(rf"[{SUP}]", cell) + re.findall(r"(?<=[%\s,])([1-9])(?![\d%])", cell[m.end():])
    marks = list(dict.fromkeys(_plain(x) for x in marks))
    extra = []
    if re.search(r"fixed", cell, re.I):
        extra.append("fixed rate only")
    t = re.search(r"/\s*(\d+(?:\.\d+)?)\s*%", cell)
    if t:
        extra.append(f"{t[1]}% TLTV")
    return float(m[1]), marks, "; ".join(extra)


def lookup(df: pd.DataFrame, fico: int | None, loan: int | None, purpose: str, occupancy: str | None,
           units: int, manufactured: bool = False) -> dict:
    """{"row", "ltv", "cell", "marks", "extra", "tier", "why"}. why is set when nothing fits."""
    limits = df.attrs.get("limits") or {}
    lim = limits.get(str(1 if manufactured else units)) or []
    if loan is None:
        return {"why": "I need the loan amount (it decides conforming or high balance)"}
    if not lim:
        return {"why": f"no {units}-unit loan limit on this sheet"}
    has_high = "High Balance Max LTV" in df.columns
    label = df.attrs.get("high_label") or "High Balance"
    if loan <= lim[0]:
        tier, col = "Conforming", "Conforming Max LTV"
    elif has_high and len(lim) > 1 and loan <= lim[1]:
        tier, col = label, "High Balance Max LTV"
    else:
        top = lim[1] if has_high and len(lim) > 1 else lim[0]
        return {"why": f"${loan:,} is over the {units}-unit limit of ${top:,.0f}"
                       + ("" if has_high else " (conforming loan limits only on this sheet)")}
    mine = df[df["Occupancy"] == occupancy] if occupancy else df.iloc[0:0]
    if mine.empty:
        have = ", ".join(dict.fromkeys(df["Occupancy"]))
        return {"why": f"no {occupancy} rows (this sheet covers: {have})"}
    mine = mine[mine["Loan Purpose"].map(lambda c: purpose in purposes(c))]
    if mine.empty:
        return {"why": f"no {purpose} rows for {occupancy}"}
    mine = mine[mine["Units"].map(lambda c: units_fit(c, units, manufactured))]
    if mine.empty:
        what = "manufactured homes" if manufactured else f"{units}-unit properties"
        return {"why": f"no {purpose} row for {what} ({occupancy})"}
    r = mine.iloc[0]
    need = _lead_number(r["Minimum FICO"])
    if fico is None or need is None or fico < need:
        return {"why": f"FICO {fico} is under the {need:g} minimum" if need else "unreadable FICO cell"}
    ltv, marks, extra = ltv_of(r[col])
    if ltv is None:
        return {"why": f"{r[col] or 'blank'}: not allowed ({tier}, {r['Loan Purpose']}, {r['Units']})"}
    return {"row": {c: r[c] for c in df.columns}, "i": int(r.name), "col": col, "ltv": ltv, "cell": r[col], "marks": marks, "extra": extra,
            "tier": tier, "limit": lim[0] if tier == "Conforming" else lim[1]}


def conv_mats(mats: list[dict]) -> list[dict]:
    """The sheet's LTV table, without the other grids on its pages (page 2's ARM caps table)."""
    return [m for m in mats if m["df"].attrs.get("kind") == "conv"]


def still_check(m: dict, res: dict) -> str:
    """What the LTV table doesn't cover, for this deal. Python reads the table only; the sheet's other rules
    can still lower the number or rule the deal out."""
    items = []
    if res["ltv"] > 95:
        items.append("the 95.01-97% rules (fixed rate, 1-unit primary, first-time buyer on a purchase)")
    if re.search(r"home\s*ready|home\s*possible", m["df"].attrs.get("title", ""), re.I):
        items.append("the income limit")
    items.append("the sheet's other pages (MI, ARMs, buydowns, property)")
    return "Table only. Check " + (", ".join(items[:-1]) + " and " if len(items) > 1 else "") + items[-1] + "."


def cite(m: dict, res: dict) -> str:
    """The matrix as a citation chip. "mark" carries the row's and the cell's place on the page, so the viewer
    highlights them instead of guessing by words ("1 | 75% | 75%" is on the page twice)."""
    marks, colx = m["df"].attrs.get("marks") or [], m["df"].attrs.get("colx") or {}
    if res.get("i") is None or res["i"] >= len(marks):
        return f"({m['file']}, p. {m['page']})"
    x0, y0, x1, y1 = marks[res["i"]]
    cx = colx.get(res["col"]) or [x0, x1]
    nums = [x0, y0, x1, y1, cx[0], y0, cx[1], y1]
    return f"({m['file']}, p. {m['page']}, mark {' '.join(f'{v:g}' for v in nums)})"


def footnotes(notes: str) -> list[str]:
    """One line per footnote. "ARM Max LTV 1 Unit is 95%" reads like a new footnote 1 after footnote 3, so a number
    that doesn't count up goes back on the line before it; the "*Minimum Fico" note gets its own line."""
    out = []
    for n in notes_md(notes):
        for part in re.split(r"\s(?=\*\s*[A-Z])", n):
            m = re.match(r"^(\d): (.*)", part)
            if m and out and re.match(r"^\d:", out[-1]) and int(m[1]) <= int(out[-1][0]):
                out[-1] += f" {m[1]} {m[2]}"
            else:
                out.append(part.strip())
    return out


def _county(question: str, loan: int, units: int, m: dict) -> dict:
    """The deal's county (named or by ZIP) against FHFA's county limits, with the sheet's own limits winning
    where NDM runs ahead of FHFA (loan_limits.conforming_check)."""
    from guidebot import loan_limits
    lim = (m["df"].attrs.get("limits") or {}).get(str(units))
    return loan_limits.conforming_check(question, loan, units, lim)


def is_conv(mats: list[dict]) -> bool:
    return bool(conv_mats(mats))


def deal_units(raw, question: str) -> tuple[int, bool]:
    """Unit count and manufactured flag from the scenario: '2 units', 'duplex', 'fourplex', 'manufactured'."""
    q = question.lower()
    manufactured = bool(re.search(r"manufactured|mobile home|double[- ]?wide|multi[- ]?wide", q))
    if raw.units:
        return int(raw.units), manufactured
    m = re.search(r"\b([1-4])\s*[- ]?(?:units?|unit property)\b", q)
    if m:
        return int(m[1]), manufactured
    for n, words in ((2, "duplex|2-family"), (3, "triplex|3-family"), (4, "fourplex|quadplex|4-family")):
        if re.search(words, q):
            return n, manufactured
    return 1, manufactured


def _checks(raw, mats: list[dict]) -> str:
    if any(m["status"] != "verified" for m in mats):
        return "Matrix not verified"
    if any((m.get("reference") or {}).get("status") == "mismatch" for m in mats):
        return "Matrix disagrees with the Revo site matrix"
    return ""


def result(raw, question: str, mats: list[dict]) -> dict:
    """One program's line for the side-by-side comparison: {eligible, max_ltv, cell, trace, error}."""
    mats = conv_mats(mats)
    bad = _checks(raw, mats)
    if bad:
        return {"eligible": None, "error": bad}
    units, mh = deal_units(raw, question)
    best, why = None, []
    for m in mats:
        res = lookup(m["df"], raw.fico, raw.loan_amount, raw.purpose, raw.occupancy, units, mh)
        if "ltv" in res and (best is None or res["ltv"] > best[1]["ltv"]):
            best = (m, res)
        elif "why" in res:
            why.append(res["why"])
    if not best:
        return {"eligible": False, "error": "; ".join(dict.fromkeys(why))}
    m, res = best
    county = _county(question, raw.loan_amount, units, m)
    if county["over"]:
        return {"eligible": False, "error": county["lines"][0]}
    note = ", ".join(x for x in (res["tier"], res["extra"]) if x)
    return {"eligible": True, "max_ltv": res["ltv"], "cell": res["cell"], "cite": cite(m, res), "trace": [m["file"]],
            "overlays": [f"{note}. {still_check(m, res)}"] + county["lines"][:1]}


def answer(raw, question: str, mats: list[dict]) -> str:
    """Markdown for one Conventional program's deal. Stops at the matrix: the sheet's other pages (95.01-97%
    first-time buyer rules, MI, property) aren't applied, and the answer says so."""
    missing = [f for f, v in (("FICO", raw.fico), ("loan amount", raw.loan_amount), ("purpose", raw.purpose),
                              ("occupancy", raw.occupancy)) if v in (None, "")]
    if missing:
        return "To pull the exact number I need: **" + ", ".join(missing) + "**."
    mats = conv_mats(mats)
    unverified = [m for m in mats if m["status"] != "verified"]
    if unverified:
        return ("These matrices haven't been verified yet, so I won't quote numbers from them: "
                + ", ".join(f"**{m['file']}**" for m in unverified)
                + ". Open the admin page, re-read each PDF, check the table against it, and approve it.")
    units, mh = deal_units(raw, question)
    deal = (f"FICO {raw.fico}, ${raw.loan_amount:,}, {raw.purpose}, {raw.occupancy}, "
            + ("manufactured home" if mh else f"{units} unit" + ("s" if units > 1 else "")))
    found, misses = [], []
    for m in mats:
        res = lookup(m["df"], raw.fico, raw.loan_amount, raw.purpose, raw.occupancy, units, mh)
        (found if "ltv" in res else misses).append((m, res))
    if not found:
        return "\n".join([f"**Not eligible on this matrix** ({deal})", ""]
                         + [f"- {m['file']}: {res['why']}" for m, res in misses])
    m, res = max(found, key=lambda x: x[1]["ltv"])
    county = _county(question, raw.loan_amount, units, m)
    if county["over"]:
        return "\n".join([f"**Not eligible: over the county's conforming loan limit** ({deal})", ""]
                         + [f"- {line}" for line in county["lines"]])
    r = res["row"]
    lines = [f"**Max LTV/CLTV: {res['ltv']:g}%** ({res['tier']}, matrix, before the sheet's other requirements)",
             "", f"*{deal}*", "",
             "| Matrix | Row | Column | Cell | Conditions |", "|---|---|---|---|---|"]
    row = ", ".join(x for x in (r["Occupancy"], r["Loan Purpose"], f"{r['Units']} unit(s)" if re.fullmatch(r"[\d-]+", r["Units"])
                                else r["Units"], f"FICO {r['Minimum FICO']}+") if x)
    cond = "; ".join(x for x in (res["extra"], r.get("Max DTI") and f"max DTI {r['Max DTI']}",
                                 f"loan up to ${res['limit']:,.0f}") if x)
    lines.append(f"| {cite(m, res)} | {row} | {res['tier']} | **{res['cell']}** | {cond} |")
    # "* Minimum Fico requirements determined by DU" sits beside footnote 2 on the refi sheets: its own line
    notes = footnotes(m["df"].attrs.get("notes", ""))
    if notes:
        cited = [n for n in notes if n[:1] in res["marks"] and n[1:2] == ":"]
        lines += ["", f"**Footnotes on this cell ({', '.join(res['marks'])})**" if cited else "**Footnotes**"]
        lines += [f"- {n}" for n in (cited or notes)]
        others = [n for n in notes if n not in cited] if cited else []
        if others:
            lines += ["", "**Other footnotes on the sheet**"] + [f"- {n}" for n in others]
    lines += ["", "**County loan limit**"] + [f"- {line}" for line in county["lines"] or [
        f"Under the conforming limit in every county (${res['limit']:,.0f} max on the sheet for this column)."
        if res["tier"] == "Conforming" else "Name the county or ZIP to check it against FHFA's county limit."]]
    lines += ["", f"*{still_check(m, res)}*"]
    return "\n".join(lines)
