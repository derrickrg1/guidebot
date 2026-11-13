"""Exact matrix lookup. Python finds the cell, never the AI."""
import re

import pandas as pd

MONEY = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*(mm|m|k)?", re.I)


def to_number(text) -> float | None:
    m = MONEY.search(str(text))
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return n * {"mm": 1e6, "m": 1e6, "k": 1e3}.get(unit, 1)


def parse_range(label: str):
    """'< $1,000,000' -> (None, 1000000, True)  '$1,000,001 - $1,500,000' -> (1000001, 1500000, False)"""
    s = label.replace("–", "-").strip()
    nums = [to_number(x) for x in re.findall(r"\$?[\d,]+(?:\.\d+)?", s)]
    if s.startswith(("<", "≤", "<=")) and nums:
        return None, nums[0], True
    if (s.startswith((">", "≥", ">=")) or s.endswith("+")) and nums:
        return nums[0], None, False
    if len(nums) == 2:
        return nums[0], nums[1], False
    return None


def _dim_kind(values: list[str]) -> str:
    if all(parse_range(v) for v in values) and any("$" in v or "-" in v or "<" in v for v in values):
        return "range"
    if all(to_number(v) is not None and "$" not in v for v in values):
        return "tier"
    return "text"


CELL = re.compile(r"^(\*?\d+(\.\d+)?%\*?|N/?A|n/a|-)$")


def dimension_columns(df: pd.DataFrame) -> list[str]:
    if df.attrs.get("dims"):
        return list(df.attrs["dims"])
    return [c for c in df.columns if not df[c].astype(str).str.match(CELL).all()]


def value_columns(df: pd.DataFrame) -> list[str]:
    dims = dimension_columns(df)
    return [c for c in df.columns if c not in dims]


HEADER_JUNK = re.compile(r"Program\s+Max|CLTV|Effective\s+Date|\d{1,2}\.\d{1,2}\.\d{4}", re.I)


def header_problems(df: pd.DataFrame) -> list[str]:
    """Signs the parser got the column headers wrong. Any of these means a lookup could
    return the right-looking number from the wrong column."""
    cols = [str(c) for c in df.columns]
    vals = value_columns(df)
    probs = []
    dups = sorted({c for c in cols if cols.count(c) > 1})
    if dups:
        probs.append(f"Duplicate column names: {', '.join(dups)}")
    for c in vals:
        if re.search(r"\(\d+\)$", c):
            probs.append(f"'{c}' has a number tacked on, so its occupancy/group header was lost")
        if HEADER_JUNK.search(c):
            probs.append(f"'{c}' has title or date text in it, so the headers likely shifted")
    groups: dict[str, list[str]] = {}
    for c in vals:
        if " / " in c:
            g, label = c.rsplit(" / ", 1)
            groups.setdefault(g, []).append(label)
    if len({tuple(v) for v in groups.values()}) > 1:
        probs.append("Column groups don't line up: "
                     + "; ".join(f"{g}: {', '.join(v)}" for g, v in groups.items()))
    return probs


def lookup(df: pd.DataFrame, dims: dict, column: str) -> dict:
    """dims: {"Loan Amount": "1200000", "FICO": "705"}; column: exact matrix column name."""
    dim_names = dimension_columns(df)
    rows = df
    trace, notes = [], []

    for dim in dim_names:
        if dim not in dims or dims[dim] in (None, ""):
            return {"ok": False, "error": f"Missing {dim}", "trace": trace, "notes": notes}
        given = str(dims[dim])
        values = list(dict.fromkeys(rows[dim].astype(str)))
        kind = _dim_kind(values)

        if kind == "range":
            x = to_number(given)
            match = None
            for v in values:
                lo, hi, strict = parse_range(v)
                if (lo is None or x >= lo) and (hi is None or x <= hi):
                    match = v
                    if strict and hi is not None and x == hi:
                        notes.append(f"{dim} {given} sits exactly on the '{v}' edge. Confirm which bracket applies.")
                    break
            if match is None:
                return {"ok": False, "error": f"{dim} {given} is outside the matrix", "trace": trace, "notes": notes}
            trace.append(f"{dim}: {match} (yours: {given})")

        elif kind == "tier":
            x = to_number(given)
            tiers = sorted((to_number(v), v) for v in values)
            eligible = [v for n, v in tiers if n <= x]
            if not eligible:
                # the lowest row it missed, so the chip can mark it
                low = rows[rows[dim].astype(str) == tiers[0][1]]
                return {"ok": False, "error": f"{dim} {given} is below the lowest tier ({tiers[0][1]}) for this row group",
                        "trace": trace, "notes": notes, "i": df.index.get_loc(low.index[0]), "col": column}
            match = eligible[-1]
            trace.append(f"{dim}: {match} row" + ("" if match == given else f" (yours: {given})"))

        else:
            match = next((v for v in values if v.lower() == given.lower()), None)
            if match is None:
                return {"ok": False, "error": f"{dim} '{given}' not found. Options: {', '.join(values)}", "trace": trace, "notes": notes}
            trace.append(f"{dim}: {match}")

        rows = rows[rows[dim].astype(str) == match]

    if column not in df.columns:
        return {"ok": False, "error": f"Column '{column}' not in matrix", "trace": trace, "notes": notes}
    cell = str(rows[column].iloc[0])
    i = df.index.get_loc(rows.index[0])   # the row's place in the grid, for the page mark
    trace.append(f"Column: {column}")
    if "*" in cell:
        notes.append(f"The matrix cell {cell} has a footnote marker (*). Check the footnote before quoting it.")
    if not cell.strip("*").endswith("%"):
        return {"ok": True, "eligible": False, "cell": cell, "ltv": None, "trace": trace, "notes": notes, "i": i}
    return {"ok": True, "eligible": True, "cell": cell, "ltv": float(cell.strip("*%")), "trace": trace, "notes": notes,
            "i": i}


def apply_adjustments(base: float, adjustments: list[dict]) -> dict:
    """Caps take the lower number. Reductions subtract. Any 'ineligible' wins."""
    if any(a["kind"] == "ineligible" for a in adjustments):
        return {"final": None, "eligible": False, "steps": []}
    value, steps = base, [f"Matrix max: {base:g}%"]
    for a in [a for a in adjustments if a["kind"] == "cap"]:
        if a["value"] < value:
            steps.append(f"Capped at {a['value']:g}% ({a['reason']})")
            value = a["value"]
        else:
            steps.append(f"Cap {a['value']:g}% doesn't lower it ({a['reason']})")
    reductions = [a for a in adjustments if a["kind"] == "reduction"]
    for a in reductions:
        value -= a["value"]
        steps.append(f"Minus {a['value']:g}% ({a['reason']}) = {value:g}%")
    return {"final": value, "eligible": value > 0, "steps": steps, "stacked": len(reductions) > 1}
