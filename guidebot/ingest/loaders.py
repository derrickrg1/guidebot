"""Read each file type into blocks of text. Tables become markdown so the model can read rows and columns."""
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class Block:
    text: str
    loc: str            # "p. 4" or "sheet DSCR"
    kind: str           # "text" or "table"
    heading: str = ""


def _cell(c) -> str:
    if c is None or (isinstance(c, float) and pd.isna(c)):
        return ""
    return str(c).replace("\n", " ").replace("|", "/").strip()


def table_to_md(rows) -> str:
    rows = [[_cell(c) for c in r] for r in rows if r is not None]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _page_heading(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return " / ".join(lines[:2])


def load_pdf(path: Path) -> list[Block]:
    import pdfplumber

    blocks = []
    with pdfplumber.open(path) as pdf:
        for num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                blocks.append(Block(text, f"p. {num}", "text"))
            for table in page.extract_tables():
                md = table_to_md(table)
                if md:
                    blocks.append(Block(md, f"p. {num}", "table", heading=_page_heading(text)))
    return blocks


def load_docx(path: Path) -> list[Block]:
    import docx

    doc = docx.Document(path)
    blocks, heading, buf = [], "", []

    def flush():
        if buf:
            blocks.append(Block("\n".join(buf), heading or "document", "text", heading))
            buf.clear()

    for p in doc.paragraphs:
        if not p.text.strip():
            continue
        if p.style.name.lower().startswith("heading"):
            flush()
            heading = p.text.strip()
        buf.append(p.text.strip())
    flush()

    for i, t in enumerate(doc.tables, start=1):
        md = table_to_md([[c.text for c in row.cells] for row in t.rows])
        if md:
            blocks.append(Block(md, f"table {i}", "table"))
    return blocks


def _frame_to_block(df: pd.DataFrame, loc: str, heading: str) -> Block | None:
    df = df.dropna(how="all").dropna(axis=1, how="all")
    md = table_to_md(df.values.tolist())
    return Block(md, loc, "table", heading) if md else None


def load_excel(path: Path) -> list[Block]:
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    blocks = [_frame_to_block(df, f"sheet {name}", f"Sheet: {name}") for name, df in sheets.items()]
    return [b for b in blocks if b]


def load_csv(path: Path) -> list[Block]:
    block = _frame_to_block(pd.read_csv(path, header=None, dtype=str), "csv", path.stem)
    return [block] if block else []


def load_text(path: Path) -> list[Block]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [Block(text, "document", "text")] if text.strip() else []


LOADERS = {
    ".pdf": load_pdf,
    ".docx": load_docx,
    ".xlsx": load_excel,
    ".csv": load_csv,
    ".txt": load_text,
    ".md": load_text,
}


def load_file(path: Path) -> list[Block]:
    return LOADERS[path.suffix.lower()](path)
