"""FHA and VA eligibility tables, and the lookup on them. Python only, no model.

These sheets aren't loan amount x FICO grids like NonQM's. Each row is one way to qualify: loan purpose, a
minimum FICO, often a maximum loan amount, and the max LTV for it ("Purchase | 600 | $1,500,000 | 100%").
A deal can fit several rows; each one it fits is shown with its conditions (DTI, AUS), best LTV first.

The PDF cells come out of pdfplumber split and merged in every direction, so cells are placed by position:
the FICO column sets the rows, and any cell in another column whose box covers a row's middle belongs to that
row (a merged "Purchase" covers three). Footnote markers stay on as superscripts (100%¹) and every footnote on
the sheet goes out with the answer, since they change the number (funding fee, 203(h), AZ/UT)."""
import re

import pandas as pd

from guidebot.extract_pdf import _clean
from guidebot.matrix_lookup import to_number

# header text -> column name. Order matters: "Minimum Loan amount" is not the max loan amount.
COLUMNS = [
    ("Minimum Loan Amount", r"min\w*\s+loan"),
    ("Loan Purpose", r"purpose"),
    ("Minimum FICO", r"fico"),
    ("Maximum Loan Amount", r"loan\s*(amount|amt)|max\w*\s+loan"),
    ("Max LTV", r"ltv"),
    ("Units", r"units"),
    ("Max DTI", r"dti"),
    ("AUS", r"\baus\b"),
    ("Terms", r"terms"),
]
SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹"
NUMBER_CELL = re.compile(rf"^\d{{1,3}}[{SUP},\d]*$")
OCCUPANCY = {"Primary Residence": "primary", "Second Home": "second", "Investment Property": "invest"}


def _name(label: str) -> str:
    return next((n for n, rx in COLUMNS if re.search(rx, label, re.I)), label)


def _overlap(a, b) -> float:
    """How much two x-ranges share, as a share of the narrower one."""
    return max(0, min(a[1], b[1]) - max(a[0], b[0])) / max(1e-6, min(a[1] - a[0], b[1] - b[0]))


def parse_table(table) -> pd.DataFrame | None:
    """A pdfplumber table -> one row per FICO cell, columns named per COLUMNS. None if it isn't this kind of
    sheet (it needs a loan purpose, a FICO and an LTV column)."""
    cells = [(bb, _clean(txt)) for r, row in zip(table.extract(), table.rows)
             for txt, bb in zip(r, row.cells) if bb and _clean(txt)]
    if not cells:
        return None
    x0, _, x1, _ = table.bbox
    full = lambda bb: bb[2] - bb[0] > 0.6 * (x1 - x0)
    fico_head = [bb for bb, t in cells if not full(bb) and re.search(r"fico", t, re.I)]
    if not fico_head:
        return None
    fx = (min(b[0] for b in fico_head), max(b[2] for b in fico_head))
    head_bottom = max(b[3] for b in fico_head)
    keys = sorted([(bb, t) for bb, t in cells if bb[1] >= head_bottom - 1 and NUMBER_CELL.match(t)
                   and _overlap((bb[0], bb[2]), fx) > 0.5], key=lambda c: c[0][1])
    if len(keys) < 2:
        return None
    top, bottom = keys[0][0][1], max(bb[3] for bb, _ in keys)

    # columns from the body cells, left to right
    body = [(bb, t) for bb, t in cells if not full(bb) and bb[3] > top + 1 and bb[1] < bottom - 1]
    cols: list[list] = []
    for bb, t in sorted(body, key=lambda c: c[0][0]):
        col = next((c for c in cols if _overlap((bb[0], bb[2]), c[0]) > 0.5), None)
        if col:
            col[0] = (min(col[0][0], bb[0]), max(col[0][1], bb[2]))
            col[1].append((bb, t))
        else:
            cols.append([(bb[0], bb[2]), [(bb, t)]])
    titles = [t for bb, t in sorted(cells, key=lambda c: c[0][1]) if full(bb) and bb[3] <= head_bottom - 1]
    heads = [(bb, t) for bb, t in cells if not full(bb) and bb[3] <= top + 1]
    names = []
    for (cx0, cx1), _ in cols:
        parts = [t for bb, t in sorted(heads, key=lambda c: (c[0][1], c[0][0]))
                 if cx0 - 1 <= (bb[0] + bb[2]) / 2 <= cx1 + 1]
        names.append(_name(" ".join(dict.fromkeys(parts))))
    need = {"Loan Purpose", "Minimum FICO", "Max LTV"}
    if not need <= set(names) or len(set(names)) != len(names):
        return None

    rows = []
    for kbb, _ in keys:
        mid = (kbb[1] + kbb[3]) / 2
        row = []
        for _, members in cols:
            hit = [(bb, t) for bb, t in members if bb[1] - 0.5 <= mid <= bb[3] + 0.5]
            # the biggest box wins: a merged cell over split fragments of the same text
            row.append(max(hit, key=lambda c: (c[0][2] - c[0][0]) * (c[0][3] - c[0][1]))[1] if hit else "")
        rows.append(row)
    df = pd.DataFrame(rows, columns=names)
    # footnotes: the full-width lines right under the grid, up to the first gap or boxed section after them
    notes, edge = [], bottom
    for bb, t in sorted([c for c in cells if c[0][1] >= bottom - 1], key=lambda c: c[0][1]):
        if not full(bb) or bb[1] > edge + (3 if notes else 20):   # the grid's last merged cells run past the FICO
            break
        notes.append(t)
        edge = bb[3]
    df.attrs.update(kind="gov", title=" · ".join(titles[:2]),
                    dims=[c for c in ("Loan Purpose", "Minimum FICO", "Maximum Loan Amount") if c in names],
                    notes=" ".join(notes))
    return df


# ---------- lookup ----------
def _purpose(row_text: str) -> str:
    s = row_text.lower()
    if re.search(r"irrrl|streamline", s):
        return "Streamline"
    if re.search(r"no\s*cash", s):
        return "Rate/Term Refinance"
    if "cash" in s:
        return "Cash Out Refinance"
    if "purchase" in s:
        return "Purchase"
    return ""


def wants_streamline(text: str) -> bool:
    return bool(re.search(r"irrrl|streamline", text, re.I))


def _plain(s: str) -> str:
    return s.translate(str.maketrans(SUP, "0123456789"))


def _lead_number(s: str) -> float | None:
    """The cell's own number, footnote marker left off: "700³" -> 700, "1,500,000³" -> 1500000."""
    m = re.match(r"\$?\s*([\d,]+(?:\.\d+)?)", s.strip())
    return float(m.group(1).replace(",", "")) if m else None


def ltv_of(cell: str) -> tuple[float | None, list[str]]:
    """'100%¹' -> (100, []); '96.5%, 100%³' -> (96.5, ['100%³']), the others being footnoted special cases;
    '90.01- 100%¹,2' -> (100, []) (a range: this sheet covers 90.01 up to 100); 'NA¹' -> (None, [])."""
    vals = re.findall(rf"(\d+(?:\.\d+)?)\s*%([{SUP}][{SUP},\d]*)?", cell)
    if not vals:
        return None, []
    if re.search(r"\d\s*[-–]\s*\d", cell):
        return float(vals[-1][0]), []
    return float(vals[0][0]), [f"{v}%{m}" for v, m in vals[1:]]


def occupancy_ok(title: str, occupancy: str | None) -> bool:
    named = {k for k, w in OCCUPANCY.items() if w in title.lower()}
    return not occupancy or not named or occupancy in named


def lookup(df: pd.DataFrame, fico: int | None, loan: int | None, purpose: str, occupancy: str | None) -> dict:
    """Every row this deal fits: {"rows": [{"row", "ltv", "cell", "others"}], "why": reason none fit}."""
    title = df.attrs.get("title", "")
    if not occupancy_ok(title, occupancy):
        return {"rows": [], "why": f"this matrix is for {title.split(' · ')[-1]} only"}
    mine = df[df["Loan Purpose"].map(_purpose) == purpose]
    if mine.empty:
        have = ", ".join(dict.fromkeys(df["Loan Purpose"].str.rstrip(" .")))
        return {"rows": [], "why": f"no {purpose} rows (it has: {have})"}
    fits, why = [], []
    for _, r in mine.iterrows():
        need = _lead_number(r["Minimum FICO"])
        if fico is None or need is None or fico < need:
            why.append(f"FICO {fico} under {need:g}" if need is not None else "unreadable FICO cell")
            continue
        if "Maximum Loan Amount" in df.columns:
            cap = _lead_number(r["Maximum Loan Amount"])
            if loan is None:
                return {"rows": [], "why": "I need the loan amount for this matrix"}
            if cap is None or loan > cap:
                why.append(f"loan over ${cap:,.0f}" if cap else "unreadable loan amount cell")
                continue
        ltv, others = ltv_of(r["Max LTV"])
        fits.append({"row": {c: r[c] for c in df.columns}, "ltv": ltv, "cell": r["Max LTV"], "others": others})
    fits.sort(key=lambda f: -(f["ltv"] or 0))
    low = min((_lead_number(r["Minimum FICO"]) or 0 for _, r in mine.iterrows()), default=0)
    reason = (f"FICO {fico} is under the lowest {purpose} minimum ({low:g})" if fico is not None and fico < low
              else "the loan amount is over the max for this FICO" if why else "")
    return {"rows": fits, "why": "" if fits else reason}


def notes_md(notes: str) -> list[str]:
    """'¹Max LTV ... ²LTV may NOT ...' or '1 LTV ... 2 AZ and UT ...' -> one line per footnote."""
    parts = re.split(rf"(?:(?<=^)|(?<=[\s.]))([{SUP}]|[1-9](?= [A-Z]))\s*", notes.strip())
    out, i = [], 1
    if parts and parts[0].strip():
        out.append(parts[0].strip())
    while i + 1 < len(parts):
        out.append(f"{_plain(parts[i])}: {parts[i + 1].strip()}")
        i += 2
    return out


def is_gov(mats: list[dict]) -> bool:
    return bool(mats) and all(m["df"].attrs.get("kind") == "gov" for m in mats)


def result(raw, question: str, mats: list[dict]) -> dict:
    """One program's line for the side-by-side comparison: {eligible, max_ltv, cell, trace, error}."""
    purpose = "Streamline" if wants_streamline(question) else raw.purpose
    if any(m["status"] != "verified" for m in mats):
        return {"eligible": None, "error": "Matrix not verified"}
    if any((m.get("reference") or {}).get("status") == "mismatch" for m in mats):
        return {"eligible": None, "error": "Matrix disagrees with the Revo site matrix"}
    found, why = [], []
    for m in mats:
        res = lookup(m["df"], raw.fico, raw.loan_amount, purpose, raw.occupancy)
        found += [(m, f) for f in res["rows"]]
        why += [f"{m['file']}: {res['why']}"] if not res["rows"] else []
    if not found:
        return {"eligible": False, "error": "; ".join(why)}
    m, best = max(found, key=lambda x: x[1]["ltv"] or 0)
    if best["ltv"] is None:
        return {"eligible": None, "cell": best["cell"], "error": f"No max LTV on {m['file']} (NA). Pick this program."}
    return {"eligible": True, "max_ltv": best["ltv"], "cell": best["cell"], "trace": [m["file"]],
            "overlays": ["Table only. Check the sheet's credit, property and other pages."]}


def answer(raw, question: str, mats: list[dict]) -> str:
    """Markdown for a VA/FHA deal. raw: answer.RawScenario. Stops at the matrix: the sheets' other pages (credit,
    property, reserves) aren't applied, and the answer says so."""
    purpose = "Streamline" if wants_streamline(question) else raw.purpose
    missing = [f for f, v in (("fico", raw.fico), ("purpose", purpose)) if v in (None, "")]
    if missing:
        return "To pull the exact number I need: **" + ", ".join(missing) + "**."
    unverified = [m for m in mats if m["status"] != "verified"]
    if unverified:
        return ("These matrices haven't been verified yet, so I won't quote numbers from them: "
                + ", ".join(f"**{m['file']}**" for m in unverified)
                + ". Open the admin page, re-read each PDF, check the table against it, and approve it.")
    bad = [m for m in mats if (m.get("reference") or {}).get("status") == "mismatch"]
    if bad:
        return "\n".join([f"**{m['file']}** doesn't match the Revo site's copy, so I won't quote numbers from it:"
                          for m in bad[:1]] + [f"- {d}" for m in bad for d in m["reference"]["diffs"][:5]])

    found, misses = [], []
    for m in mats:
        res = lookup(m["df"], raw.fico, raw.loan_amount, purpose, raw.occupancy)
        if res["why"] and "loan amount" in res["why"] and "need" in res["why"]:
            return "To pull the exact number I need: **loan_amount**."
        found += [(m, f) for f in res["rows"]]
        if not res["rows"]:
            misses.append(f"{m['file']}: {res['why']}")
    deal = f"FICO {raw.fico}" + (f", ${raw.loan_amount:,}" if raw.loan_amount else "") + f", {purpose}" \
        + (f", {raw.occupancy}" if raw.occupancy else "")
    if not found:
        return "\n".join([f"**Not eligible on these matrices** ({deal})", ""] + [f"- {x}" for x in misses])

    best = found[0][1]["ltv"] if all(f["ltv"] is None for _, f in found) else max(
        (f["ltv"] for _, f in found if f["ltv"] is not None))
    head = (f"**Max LTV/CLTV: {best:g}%** (matrix, before the sheet's other requirements)" if best is not None
            else "**Eligible. No max LTV on this matrix (NA)**, see the footnote")
    lines = [head, "", f"*{deal}*", "", "| Matrix | Row | Max LTV | Conditions |", "|---|---|---|---|"]
    for m, f in sorted(found, key=lambda x: -(x[1]["ltv"] or 0)):
        r = f["row"]
        row = ", ".join(x for x in (r["Loan Purpose"].rstrip(" ."), f"FICO {r['Minimum FICO']}+",
                                     r.get("Maximum Loan Amount") and f"up to {r['Maximum Loan Amount']}") if x)
        cond = "; ".join(f"{k} {r[k]}" for k in ("Max DTI", "AUS") if r.get(k)) or "none listed"
        extra = f" ({', '.join(f['others'])} in special cases, see notes)" if f["others"] else ""
        lines.append(f"| {m['file']} p. {m['page']} | {row} | **{f['cell']}**{extra} | {cond} |".replace("\n", " "))
    if misses:
        lines += ["", "**Didn't fit**"] + [f"- {x}" for x in misses]
    if not raw.occupancy:
        lines += ["", "- Occupancy not given. These matrices cover: " + "; ".join(
            f"{m['file']}: {m['df'].attrs.get('title', '').split(' · ')[-1]}" for m in mats)]
    if any(m["df"].attrs.get("title", "").startswith("FHA") and "Maximum Loan Amount" not in m["df"].columns
           for m, _ in found):
        lines += ["", "- FHA county loan limits apply. This matrix has no loan amount tiers, so check the county limit."]
    for m in {id(m): m for m, _ in found}.values():
        notes = notes_md(m["df"].attrs.get("notes", ""))
        if notes:
            lines += ["", f"**Footnotes, {m['file']}**"] + [f"- {n}" for n in notes]
    if any((m.get("reference") or {}).get("status") == "match" for m, _ in found):
        lines += ["", "✅ Matches the Revo site's copy of the matrix cell for cell"]
    lines += ["", "*Table only. Check the sheet's credit, property and reserve rules on its other pages.*"]
    return "\n".join(lines)
