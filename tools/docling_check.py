"""A third, independent reading of every approved LTV grid: Docling's table model (IBM, MIT license, run on the GPU)
reads each grid page from the PDF, and every cell is compared with the approved grid. pdfplumber + parse_matrix is
the first reading and the Revo site's HTML the second (crosscheck.py), but the site only has Express/Ignite; this
covers every program sheet with a grid.

    .venv\\Scripts\\python.exe -m tools.docling_check            -> data/audit/docling.md

Read only. Docling's tables are cached per file hash and page (data/docling/), so a re-run only reads new PDFs.
Rows line up by their row labels (loan amount + FICO, or occupancy/purpose/units...), columns by order.
"""
import json
import re
import sys
from pathlib import Path

from guidebot import library
from guidebot.matrix_lookup import dimension_columns, value_columns

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "docling"
OUT = ROOT / "data" / "audit" / "docling.md"
_conv = None


def _converter():
    global _conv
    if _conv is None:
        import torch
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import AcceleratorOptions, PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True, accelerator_options=AcceleratorOptions(
            device="cuda" if torch.cuda.is_available() else "cpu"))
        _conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _conv


def tables(path: Path, page: int, file_hash: str) -> list[list[list[str]]]:
    """Docling's tables on one page, each a list of rows of cell strings (header rows included)."""
    cache = CACHE / f"{file_hash[:16]}-p{page}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    res = _converter().convert(path, page_range=(page, page))
    out = []
    for tb in res.document.tables:
        df = tb.export_to_dataframe(doc=res.document)
        out.append([[str(c) for c in df.columns]] + [[str(v) for v in row] for row in df.astype(str).values.tolist()])
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def _norm(s) -> str:
    """'90%', ' 90 % ', '90%*' all read as 90%; N/A, NA and blank as n/a."""
    s = re.sub(r"\s+", "", str(s or "")).lower().replace("nan", "")
    return "n/a" if s in ("", "na", "n/a", "-", "none") else s


def _row_key(cells: list[str]) -> str:
    return "|".join(_norm(c) for c in cells)


def compare(df, rows: list[list[str]]) -> dict:
    """Matches the grid's rows to Docling's by their label cells, then compares value cells by column order. When
    Docling reads two bands as one table (Express DSCR's 1.00+ and .75-.99 grids share loan amount + FICO labels),
    the same label shows up more than once: every offset into the repeats is tried and the best fit kept."""
    dims, vals = dimension_columns(df), value_columns(df)
    width = len(dims) + len(vals)
    theirs: dict = {}
    for r in rows[1:]:
        if len(r) >= width:
            theirs.setdefault(_row_key(r[:len(dims)]), []).append(r[len(dims):len(dims) + len(vals)])
    repeats = max((len(v) for v in theirs.values()), default=1)
    best = None
    for offset in range(repeats):
        seen: dict = {}
        diffs, checked, unmatched = [], 0, 0
        for _, row in df.iterrows():
            key = _row_key([row[d] for d in dims])
            k = seen.get(key, 0) + offset
            seen[key] = seen.get(key, 0) + 1
            if key not in theirs or k >= len(theirs[key]):
                unmatched += 1
                continue
            for col, v in zip(vals, theirs[key][k]):
                checked += 1
                if _norm(row[col]) != _norm(v):
                    diffs.append(f"{' / '.join(str(row[d]) for d in dims)} | {col}: approved {row[col]!r}, Docling {v!r}")
        r = {"checked": checked, "diffs": diffs, "unmatched_rows": unmatched, "rows": len(df)}
        if best is None or (checked - 5 * len(diffs)) > (best["checked"] - 5 * len(best["diffs"])):
            best = r
    return best


def best_table(df, found: list) -> dict | None:
    """The Docling table on the page that lines up with the grid best."""
    results = [compare(df, t) for t in found if t]
    results = [r for r in results if r["checked"]]
    return max(results, key=lambda r: r["checked"] - 5 * len(r["diffs"])) if results else None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    lines, total_cells, total_diffs = ["# Docling check: a third reading of every approved grid", ""], 0, 0
    for prog, paths in sorted(library.programs().items()):
        for path in library.unique_docs(paths)[0]:
            if path.suffix.lower() != ".pdf":
                continue
            doc = library.load(path)
            if doc["status"] != "verified":
                continue
            for page in doc["pages"]:
                for m in page["matrices"]:
                    if len(value_columns(m)) < 1 or len(m) < 2:
                        continue
                    name = f"{prog} / {path.name} p. {page['page']} {m.attrs.get('title') or ''}".strip()
                    try:
                        r = best_table(m, tables(path, page["page"], doc["hash"]))
                    except Exception as e:
                        lines.append(f"- {name}: Docling failed ({type(e).__name__}: {e})")
                        continue
                    if not r:
                        lines.append(f"- {name}: Docling found no table that lines up")
                        print(f"{name}: no matching table", flush=True)
                        continue
                    total_cells += r["checked"]
                    total_diffs += len(r["diffs"])
                    status = "✅ matches" if not r["diffs"] else f"❌ {len(r['diffs'])} cell(s) differ"
                    extra = f", {r['unmatched_rows']} of {r['rows']} rows not lined up" if r["unmatched_rows"] else ""
                    lines.append(f"- {name}: {status} ({r['checked']} cells{extra})")
                    lines += [f"    - {d}" for d in r["diffs"][:15]]
                    print(f"{name}: {status} ({r['checked']} cells{extra})", flush=True)
    lines[1:1] = [f"{total_cells} cells compared, {total_diffs} differ.", ""]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{total_cells} cells compared, {total_diffs} differ. {OUT}")


if __name__ == "__main__":
    main()
