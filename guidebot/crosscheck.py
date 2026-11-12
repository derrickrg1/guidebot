"""Second source of truth: the LTV grids hand-typed in the Revo site repo (site/tools/matrices/build_*.py),
which that repo's verify.py checks against the same NDM PDFs. If guidebot's parsed matrix disagrees with them,
something is wrong and no numbers go out.

Matched by file hash: a reference only applies when guidebot has the exact PDF the site was built from.
A newer NDM sheet in either repo shows as "no reference", never as a false match."""
import hashlib
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from guidebot import config, site_grids

# site original -> [(builder file, grid variable)], one per matrix, in page order
REFERENCES = {
    "dscr-ignite.pdf": [("build_dscr.py", "IGNITE_GRID")],
    "dscr-express.pdf": [("build_dscr.py", "EX_GE"), ("build_dscr.py", "EX_LT")],
    "bank-statement-ignite.pdf": [("build_bank_statement.py", "IGNITE")],
    "bank-statement-express.pdf": [("build_bank_statement.py", "EXPRESS")],
    "full-doc-ignite.pdf": [("build_full_doc.py", "IGNITE")],
    "full-doc-express.pdf": [("build_full_doc.py", "EXPRESS")],
}


@lru_cache(maxsize=None)
def _original_hashes(site_dir: Path) -> dict[str, str]:
    """{sha256: original filename} for the site originals we have grids for."""
    out = {}
    for name in REFERENCES:
        p = site_dir / "originals" / name
        if p.exists():
            out[hashlib.sha256(p.read_bytes()).hexdigest()] = name
    return out


def _literal_source(src: str, name: str) -> str | None:
    """Source text of `NAME = [...]` at the top level, found by bracket matching (the builders
    use f-strings Python 3.11 can't parse, so the whole file can't go through ast)."""
    m = re.search(rf"^{name} = ", src, re.M)
    if not m:
        return None
    i, depth, quote = m.end(), 0, None
    start = i
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    return None


@lru_cache(maxsize=None)
def site_grid(site_dir: Path, builder: str, name: str):
    src = (site_dir / builder).read_text(encoding="utf-8")
    lit = _literal_source(src, name)
    if lit is None:
        return None
    return eval(lit, {"__builtins__": {}}, {"NA": "N/A"})  # plain tuples/strings from Derrick's own repo


def _rows_from_grid(grid) -> list[tuple]:
    return [(" ".join(band.split()), r[0].rstrip("+"), *[str(v).strip() for v in r[1:]])
            for band, rows in grid for r in rows]


def _rows_from_df(df: pd.DataFrame) -> list[tuple]:
    dims = list(df.attrs.get("dims") or [])
    if len(dims) != 2:
        return []
    return [(" ".join(str(r[0]).split()), str(r[1]).rstrip("+"), *[str(v).strip() for v in r[2:]])
            for r in df[dims + [c for c in df.columns if c not in dims]].values.tolist()]


def compare(df: pd.DataFrame, grid) -> list[str]:
    """Differences between a parsed matrix and a site grid. Empty list = identical."""
    mine, ref = _rows_from_df(df), _rows_from_grid(grid)
    diffs = []
    if len(mine) != len(ref):
        diffs.append(f"{len(mine)} rows parsed, site matrix has {len(ref)}")
    for i, (a, b) in enumerate(zip(mine, ref), start=1):
        if a != b:
            diffs.append(f"Row {i}: parsed {' | '.join(a)}  vs site  {' | '.join(b)}")
    return diffs


def check(doc: dict, site_dir: Path | None = None, repo: Path | None = None) -> list[dict]:
    """One result per matrix in the doc, in page order:
    {"status": "match" | "mismatch" | "none", "source": "build_x.py NAME", "diffs": [...]}
    The site's built HTML matrices (site_grids) are checked first: they cover every Express and Ignite grid. The old
    hand-typed grids answer only where the HTML has nothing."""
    old = _check_builders(doc, site_dir)
    new = site_grids.check(doc, repo)
    if not new:
        return old
    return [n if n["status"] != "none" or o["status"] == "none" else o for n, o in zip(new, old)]


def _check_builders(doc: dict, site_dir: Path | None = None) -> list[dict]:
    site_dir = site_dir or config.SITE_MATRICES
    mats = [m for p in doc["pages"] for m in p["matrices"]]
    none = [{"status": "none", "source": "", "diffs": []} for _ in mats]
    if not site_dir.exists():
        return none
    original = _original_hashes(site_dir).get(doc["hash"])
    if not original:
        return none
    refs = REFERENCES[original]
    if len(refs) != len(mats):
        return [{"status": "mismatch", "source": original,
                 "diffs": [f"Parsed {len(mats)} matrices, the site has {len(refs)} for {original}"]} for _ in mats]
    out = []
    for m, (builder, name) in zip(mats, refs):
        grid = site_grid(site_dir, builder, name)
        if grid is None:
            out.append({"status": "none", "source": f"{builder} {name}", "diffs": []})
            continue
        diffs = compare(m, grid)
        out.append({"status": "mismatch" if diffs else "match", "source": f"{builder} {name}", "diffs": diffs})
    return out
