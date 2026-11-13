"""LTV grids from the Revo site's built matrices (revo-funding-site/assets/matrices/*/*.html).

Those pages are real HTML tables, so a grid comes out cell for cell with no PDF parsing. Each table says which
program it is by class: pg-ex (Express), pg-ig (Ignite), pg-pm (Premier). They are a second source to check
guidebot's parsed grids against, never the source of a number on their own: the site's Express "originals" are
Revo-branded copies of NDM's sheets, and at least one cell differed from NDM's PDF (Express 1099).

A grid is only compared when the site's effective date for that program matches the date on guidebot's PDF, so a
newer sheet on either side shows as "no reference", not as a mismatch or a false match."""
import re
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from guidebot import config

# guidebot program -> [(file name pattern, site page, table class)]. A guidebot PDF takes the first entry whose
# pattern is in its file name; the page's tables of that class are its matrices, in order.
SOURCES = {
    "Express 1099": [("", "1099/1099-matrix.html", "pg-ex")],
    "Ignite 1099": [("", "1099/1099-matrix.html", "pg-ig")],
    "Express Bank Statements": [("", "bank-statement/bank-statement-matrix.html", "pg-ex")],
    "Ignite Bank Statements": [("", "bank-statement/bank-statement-matrix.html", "pg-ig")],
    "Express Full Doc": [("", "full-doc/full-doc-matrix.html", "pg-ex")],
    "Ignite Full Doc": [("", "full-doc/full-doc-matrix.html", "pg-ig")],
    "Express P&L": [("", "profit-loss/profit-loss-matrix.html", "pg-ex")],
    "Ignite P&L": [("", "profit-loss/profit-loss-matrix.html", "pg-ig")],
    "Express DSCR": [("units", "dscr/dscr-units-plus-matrix.html", "pg-ex"),
                     ("", "dscr/dscr-program-matrix.html", "pg-ex")],
    "Ignite Express DSCR": [("", "dscr/dscr-program-matrix.html", "pg-ig")],
}
# FHA and VA: one eligibility table per sheet (class "g", the first on the page), checked column by column.
GOV_SOURCES = {
    "VA": [("cash", "va/va-cashout-matrix.html"), ("irrrl", "va/va-irrrl-matrix.html"),
           ("standard", "va/va-standard-matrix.html")],
    "FHA": [("streamline", "fha/fha-streamline-matrix.html"), ("standard", "fha/fha-standard-matrix.html")],
}
GOV_COMPARED = ("Loan Purpose", "Minimum FICO", "Maximum Loan Amount", "Max LTV", "Max DTI", "AUS")


class _Tables(HTMLParser):
    """Every <table> as {"cls", "head": [[cell...]], "body": [[cell...]]}, cells as {"text", "rs", "cs"}."""

    def __init__(self, sup: bool = False):
        super().__init__()
        self.tables, self.stack, self.cell, self.section = [], [], None, "body"
        self.sup, self.in_sup = sup, False   # sup=True: <sup>1</sup> footnote markers come out as ¹

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.stack.append({"cls": a.get("class") or "", "head": [], "body": []})
        elif not self.stack:
            return
        elif tag == "thead":
            self.section = "head"
        elif tag == "tbody":
            self.section = "body"
        elif tag == "tr":
            self.stack[-1][self.section].append([])
        elif tag in ("td", "th"):
            self.cell = {"text": "", "rs": int(a.get("rowspan") or 1), "cs": int(a.get("colspan") or 1)}
        elif tag == "br" and self.cell is not None:
            self.cell["text"] += " "
        elif tag == "sup":
            self.in_sup = self.sup

    def handle_endtag(self, tag):
        if tag == "sup":
            self.in_sup = False
        if not self.stack:
            return
        if tag in ("td", "th") and self.cell is not None:
            rows = self.stack[-1][self.section]
            if not rows:
                rows.append([])
            self.cell["text"] = " ".join(unescape(self.cell["text"]).split())
            rows[-1].append(self.cell)
            self.cell = None
        elif tag == "thead":
            self.section = "body"
        elif tag == "table":
            self.tables.append(self.stack.pop())
            self.section = "body"

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data.translate(_SUP) if self.in_sup else data


_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _expand(rows: list[list[dict]]) -> list[list[str]]:
    """Rows of cells with rowspan/colspan -> a plain rectangle of strings (merged cells repeated)."""
    out, carry = [], {}   # carry: column -> (text, rows left)
    for row in rows:
        line, cells, col = [], iter(row), 0
        while True:
            if col in carry:
                text, left = carry[col]
                line.append(text)
                carry[col] = (text, left - 1)
                if left - 1 == 0:
                    del carry[col]
                col += 1
                continue
            c = next(cells, None)
            if c is None:
                break
            for k in range(c["cs"]):
                line.append(c["text"])
                if c["rs"] > 1:
                    carry[col + k] = (c["text"], c["rs"] - 1)
            col += c["cs"]
        while col in carry:   # merged cells still running past the last real cell
            text, left = carry.pop(col)
            line.append(text)
            if left > 1:
                carry[col] = (text, left - 1)
            col += 1
        out.append(line)
    return out


def table_frame(t: dict) -> pd.DataFrame:
    """A site grid as a DataFrame shaped like guidebot's: "Loan Amount", "FICO", then one column per header path
    ("Primary Residence / Purchase"). The corner title ("Program Maximum LTV/CLTVs") is dropped."""
    head, body = _expand(t["head"]), _expand(t["body"])
    width = max(len(r) for r in head + body)
    cols = []
    for i in range(width):
        parts = []
        for r in head:
            text = r[i] if i < len(r) else ""
            if text and not re.search(r"max\w*\s+ltv", text, re.I) and text not in parts:
                parts.append(text)
        cols.append(" / ".join(parts) or f"col{i}")
    df = pd.DataFrame([r + [""] * (width - len(r)) for r in body], columns=cols)
    df.attrs["dims"] = cols[:2]
    return df


@lru_cache(maxsize=None)
def _page(path: Path) -> tuple[list[dict], dict[str, str]]:
    """The page's tables and its effective dates by side ("ex"/"ig"/"pm" -> "3.2.2026")."""
    html = path.read_text(encoding="utf-8")
    p = _Tables()
    p.feed(html)
    dates = {}
    for side, date in re.findall(r'class="rh (\w+)"[^>]*>.*?Effective Date:\s*([\d.]+)', html, re.S):
        dates.setdefault(side, date)
    if not dates:   # one-program pages put the date in the header
        m = re.search(r"Effective Date:\s*([\d.]+)", html)
        if m:
            dates["ex"] = m.group(1)
    return p.tables, dates


def site_tables(program: str, filename: str, repo: Path | None = None) -> list[dict] | None:
    """[{"df", "source", "effective"}] for one guidebot PDF, in page order. None when the site has no page for it."""
    base = (repo or config.SITE_REPO) / "assets" / "matrices"
    entry = next((e for e in SOURCES.get(program, []) if e[0] in filename.lower()), None)
    if not entry or not (base / entry[1]).exists():
        return None
    _, page, cls = entry
    tables, dates = _page(base / page)
    side = cls.split("-")[1]
    return [{"df": table_frame(t), "source": f"{page} ({cls})", "effective": dates.get(side, "")}
            for t in tables if cls in t["cls"].split() and "grid" in t["cls"].split()]


def check(doc: dict, repo: Path | None = None) -> list[dict] | None:
    """One result per matrix in a guidebot doc, like crosscheck.check: {"status": "match" | "mismatch" | "none",
    "source", "diffs"}. None when the site has nothing for this doc (or the doc has no path to name it by)."""
    path = doc.get("path")
    if not path:
        return None
    if _gov_key(path):
        return gov_check(doc, repo)
    site = site_tables(path.parent.name, path.name, repo)
    if site is None:
        return None
    mats = [m for p in doc["pages"] for m in p["matrices"]]
    mine_date = effective_date(doc)
    if site and mine_date and site[0]["effective"] and mine_date != site[0]["effective"]:
        # different releases: nothing to compare, and not a reason to block
        return [{"status": "none", "source": f"{site[0]['source']}, effective {site[0]['effective']}", "diffs": []}
                for _ in mats]
    if len(site) != len(mats):
        return [{"status": "mismatch", "source": site[0]["source"] if site else "Revo site",
                 "diffs": [f"Parsed {len(mats)} matrices, the site page has {len(site)}"]} for _ in mats]
    return [{"status": "mismatch" if (d := compare(m, s["df"])) else "match", "source": s["source"], "diffs": d}
            for m, s in zip(mats, site)]


# ---------- FHA / VA ----------
def gov_table(program: str, filename: str, repo: Path | None = None) -> list[dict[str, str]] | None:
    """The site's eligibility table for one FHA/VA PDF as rows of {column name: cell}, named like gov_matrix's."""
    from guidebot.gov_matrix import _name
    base = (repo or config.SITE_REPO) / "assets" / "matrices"
    entry = next((e for e in GOV_SOURCES.get(program, []) if e[0] in filename.lower()), None)
    if not entry or not (base / entry[1]).exists():
        return None
    p = _Tables(sup=True)
    p.feed((base / entry[1]).read_text(encoding="utf-8"))
    t = next((t for t in p.tables if t["cls"].split() == ["g"]), None)
    if t is None:
        return None
    rows = _expand(t["head"]) + _expand(t["body"])
    at = next((i for i, r in enumerate(rows) if any(re.search(r"purpose", c, re.I) for c in r)), None)
    if at is None:
        return None
    names = [_name(c) for c in rows[at]]
    return [dict(zip(names, r)) for r in rows[at + 1:] if len(set(r)) > 1]   # the footnote row spans every column


def _gov_cell(s: str) -> str:
    s = s.translate(str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")).lower()
    return re.sub(r"[^a-z0-9%.]", "", s).rstrip(".")


def gov_compare(mine: pd.DataFrame, site: list[dict[str, str]]) -> list[str]:
    cols = [c for c in GOV_COMPARED if c in mine.columns]
    diffs = [f"Site table has no {c} column" for c in cols if site and c not in site[0]]
    if len(mine) != len(site):
        diffs.append(f"{len(mine)} rows parsed, site has {len(site)}")
    for i, (a, b) in enumerate(zip(mine.to_dict("records"), site), start=1):
        bad = [c for c in cols if c in b and _gov_cell(a[c]) != _gov_cell(b[c])]
        if bad:
            diffs.append(f"Row {i}: " + "; ".join(f"{c} parsed {a[c]} vs site {b[c]}" for c in bad))
    return diffs


def _gov_key(path: Path) -> str:
    """ "VA" for docs/VA Standard/..., "FHA" for docs/FHA Streamline/...: one folder per sheet, one site section."""
    first = path.parent.name.split()[0] if path.parent.name else ""
    return first if first in GOV_SOURCES else ""


def gov_check(doc: dict, repo: Path | None = None) -> list[dict] | None:
    mats = [m for p in doc["pages"] for m in p["matrices"]]
    site = gov_table(_gov_key(doc["path"]), doc["path"].name, repo)
    if site is None:
        return None
    src = next(e[1] for e in GOV_SOURCES[_gov_key(doc["path"])] if e[0] in doc["path"].name.lower())
    if len(mats) != 1:
        return [{"status": "mismatch", "source": src, "diffs": [f"Parsed {len(mats)} tables, expected 1"]}
                for _ in mats]
    d = gov_compare(mats[0], site)
    return [{"status": "mismatch" if d else "match", "source": src, "diffs": d}]


# ---------- comparing with guidebot's parsed grids ----------
def _nums(text: str) -> tuple:
    """A loan amount band or FICO as its numbers: "≤ $1,000,000" and "< $1,000,000" both -> (1000000,).
    The < / ≤ difference is real but is the open question already flagged in answers; numbers decide the match."""
    return tuple(int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", str(text)))


def _cell(v) -> str:
    return " ".join(str(v).split()).replace("N/A", "NA").rstrip("*")


def compare(mine: pd.DataFrame, site: pd.DataFrame) -> list[str]:
    """Differences between guidebot's parsed grid and the site's, row by row. Empty list = same numbers."""
    def rows(df):
        # by position: a site table can repeat a header over its value columns ("DSCR .75 to .99**" x3)
        dims = list(df.attrs.get("dims") or df.columns[:2])
        di = [list(df.columns).index(d) for d in dims]
        vi = [i for i in range(len(df.columns)) if i not in di]
        return [(_nums(r[di[0]]), _nums(r[di[1]]), tuple(_cell(r[i]) for i in vi))
                for r in df.itertuples(index=False)]
    a, b = rows(mine), rows(site)
    diffs = []
    if len(mine.columns) != len(site.columns):
        diffs.append(f"{len(mine.columns)} columns parsed, site has {len(site.columns)}")
    if len(a) != len(b):
        diffs.append(f"{len(a)} rows parsed, site has {len(b)}")
    for i, (x, y) in enumerate(zip(a, b), start=1):
        if x != y:
            band = " ".join(str(v) for v in mine.iloc[i - 1][list(mine.attrs.get("dims") or mine.columns[:2])])
            diffs.append(f"Row {i} ({band}): parsed {' | '.join(x[2])}  vs site  {' | '.join(y[2])}"
                         if x[:2] == y[:2] else f"Row {i}: parsed {x[:2]} vs site {y[:2]}")
    return diffs


def effective_date(doc: dict) -> str:
    """The effective date printed on a guidebot PDF ("3.2.2026"), from its extracted text."""
    for p in doc["pages"]:
        m = re.search(r"Effective(?: Date)?:?\s*([\d]+\.[\d]+\.[\d]{4})", p.get("text", ""))
        if m:
            return m.group(1)
    return ""
