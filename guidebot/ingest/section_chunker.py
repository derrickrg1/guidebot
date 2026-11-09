"""Section-level chunking for guidelines PDFs that have a table of contents.

Each chunk represents one guideline subsection with its full section ancestry as a prefix:
  [NonQM Shared | doc.pdf | §5 Income > §5.3 Bank Statements > §5.3.3 Non-Sufficient Funds]

This beats page-level chunking because:
- BM25 hits the exact section name, not "page 37 of something"
- The embedding captures the full topic hierarchy (Income > Bank Statements > NSFs)
- A section that spans two pages stays in one chunk instead of being split mid-rule
"""
import re
from pathlib import Path

import pdfplumber

_TOC_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)\s{1,8}(.+?)\s*\.{2,}\s*(\d+)\s*$")
MAX_WORDS = 350


def _parse_toc(pdf) -> list[dict]:
    """Return TOC entries from the first 6 pages, sorted by (page, section_num)."""
    entries, seen = [], set()
    for page in pdf.pages[:6]:
        for line in (page.extract_text() or "").splitlines():
            m = _TOC_RE.search(line)
            if m:
                num = m.group(1)
                title = m.group(2).strip().rstrip(".")
                page_num = int(m.group(3))
                if num not in seen:
                    entries.append({"num": num, "title": title, "page": page_num})
                    seen.add(num)
    return sorted(entries, key=lambda e: (e["page"], e["num"]))


def _detect_offset(pdf, toc: list[dict]) -> int:
    """
    Find the physical-to-logical page offset.
    The TOC says section 1.0 is on 'page X', but the PDF might have un-numbered
    cover/TOC pages before that. Scan pages near the expected start to find where
    the first section's content actually lives.
    Returns offset such that: physical_index = toc_page + offset - 1
    """
    if not toc:
        return 0
    first = toc[0]
    expected = first["page"] - 1  # 0-indexed if offset=0
    # Search a window of pages for the first section number as a heading
    pattern = re.compile(r"^\s*" + re.escape(first["num"]) + r"\b", re.MULTILINE)
    for delta in range(0, 6):
        idx = expected + delta
        if idx >= len(pdf.pages):
            break
        t = pdf.pages[idx].extract_text() or ""
        if pattern.search(t) or first["title"][:20].lower() in t.lower():
            return delta  # physical index = toc_page - 1 + delta
    return 0  # best guess


def _parent_map(entries: list[dict]) -> dict[str, dict]:
    lookup = {e["num"]: e for e in entries}
    parents = {}
    for num in lookup:
        parts = num.split(".")
        for depth in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:depth])
            if candidate in lookup:
                parents[num] = lookup[candidate]
                break
    return parents


def _breadcrumb(num: str, title: str, entries: list[dict]) -> str:
    """Build '§5 Income > §5.3 Bank Statements > §5.3.3 NSFs' ancestry string."""
    lookup = {e["num"]: e for e in entries}
    parents = _parent_map(entries)
    chain = []
    cur = num
    while cur in parents:
        cur = parents[cur]["num"]
        chain.insert(0, f"§{cur} {lookup[cur]['title']}")
    chain.append(f"§{num} {title}")
    return " > ".join(chain)


def _split_at_paragraphs(text: str, prefix: str, max_words: int) -> list[str]:
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks, buf, count = [], [], 0
    for para in paragraphs:
        w = len(para.split())
        if buf and count + w > max_words:
            chunks.append(prefix + "\n\n" + "\n\n".join(buf))
            buf, count = [], 0
        buf.append(para)
        count += w
    if buf:
        chunks.append(prefix + "\n\n" + "\n\n".join(buf))
    return chunks or [prefix]


def has_toc(pdf_path: Path) -> bool:
    with pdfplumber.open(pdf_path) as pdf:
        return len(_parse_toc(pdf)) >= 5


def section_chunks(pdf_path: Path, doc_name: str, program: str) -> list[dict]:
    """
    Chunk a guidelines PDF by section, one chunk per subsection with full ancestry header.
    Falls back to page-level if no TOC is found.
    """
    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        toc = _parse_toc(pdf)

        if len(toc) < 5:
            return [
                {"text": (pdf.pages[i].extract_text() or "").strip(),
                 "loc": f"p. {i+1}", "kind": "page", "section": "", "section_title": ""}
                for i in range(n_pages)
                if (pdf.pages[i].extract_text() or "").strip()
            ]

        offset = _detect_offset(pdf, toc)

        # Build (section, title, start_idx, end_idx) spans using calibrated offset
        spans = []
        for j, entry in enumerate(toc):
            start = entry["page"] - 1 + offset  # physical 0-indexed
            if j + 1 < len(toc):
                end = toc[j + 1]["page"] - 2 + offset
            else:
                end = n_pages - 1
            end = max(start, min(end, n_pages - 1))
            spans.append((entry["num"], entry["title"], start, end))

        chunks = []
        for num, title, start, end in spans:
            pages_text = [
                (pdf.pages[pi].extract_text() or "").strip()
                for pi in range(start, end + 1)
            ]
            pages_text = [t for t in pages_text if t]
            if not pages_text:
                continue

            full_text = "\n\n".join(pages_text)
            page_range = f"p. {start+1}" if start == end else f"p. {start+1}-{end+1}"
            breadcrumb = _breadcrumb(num, title, toc)
            prefix = f"[{program} | {doc_name} | {breadcrumb}]"

            if len(full_text.split()) <= MAX_WORDS:
                chunks.append({
                    "text": f"{prefix}\n\n{full_text}",
                    "loc": f"§{num} {page_range}",
                    "kind": "section",
                    "section": num,
                    "section_title": title,
                })
            else:
                for part_i, part_text in enumerate(_split_at_paragraphs(full_text, prefix, MAX_WORDS)):
                    chunks.append({
                        "text": part_text,
                        "loc": f"§{num} {page_range}" + (f" pt.{part_i+1}" if part_i else ""),
                        "kind": "section",
                        "section": num,
                        "section_title": title,
                    })

    return chunks
