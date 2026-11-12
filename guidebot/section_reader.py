"""
Full-section loading for structured guidelines PDFs.

Instead of chunking + retrieval, this:
1. Routes the question to relevant mid-level sections using the TOC (one fast structured call)
2. Loads those complete sections from the PDF verbatim
3. Returns the full text for the LLM to read

Why this beats RAG for guidelines:
- No chunk boundary splits mid-rule
- No retrieval noise from unrelated sections
- Sibling sections (§5.3.1, §5.3.2, §5.3.3) stay together — the full context a human would read
"""
import re
from pathlib import Path

import pdfplumber
from pydantic import BaseModel

from guidebot.ingest.section_chunker import _detect_offset, _parse_toc
from guidebot.llm import ask_json

ROUTE_SYSTEM = """You are routing a mortgage guideline question to the correct sections of a guidelines document.

Given the table of contents, return the section numbers most likely to contain the complete answer.
Rules:
- Pick mid-level sections (e.g. "5.3" not "5.3.3") so all subsections load together.
- Return 1-3 sections. Fewer is better if one section covers the question.
- If the question touches multiple topics (NSFs AND reserves), include a section for each.
- Return an empty list only if the question clearly has nothing to do with guidelines."""


# Shorthand people type that the guides spell out. "NOD" never matched "Notice of Default" (a significant derogatory
# credit event, §7.5), so an NOD question got routed to condo HOA pages instead. Only terms with one meaning here.
TERMS = {
    "NOD": "Notice of Default, a pre-foreclosure credit event",
    "DIL": "Deed-in-Lieu",
    "BK": "bankruptcy",
    "FC": "foreclosure",
    "SS": None,   # short sale or social security: too ambiguous to expand
    "NSF": "non-sufficient funds",
    "NSFs": "non-sufficient funds",
    "DTI": "debt-to-income ratio",
    "DSCR": "debt service coverage ratio",
    "VOE": "verification of employment",
    "WVOE": "written verification of employment",
    "VOR": "verification of rent",
    "VOM": "verification of mortgage",
    "LOE": "letter of explanation",
    "FTHB": "first time homebuyer",
    "FTI": "first time investor",
    "STR": "short-term rental",
    "ADU": "accessory dwelling unit",
    "HOA": "homeowners association",
    "NOO": "non-owner occupied, investment property",
    "PITIA": "principal, interest, taxes, insurance and association dues",
    "ITIN": "Individual Taxpayer Identification Number",
}


def expand_terms(question: str) -> str:
    """The question with its shorthand spelled out after it, for routing, search and the answer prompt."""
    found = [f"{k} = {v}" for k, v in TERMS.items() if v and re.search(rf"\b{k}\b", question)]
    return f"{question}\n(Terms: {'; '.join(found)})" if found else question


def cited_pages(history: list[dict], filename: str) -> list[int]:
    """Pages of `filename` cited in earlier answers ("(NonQM-Underwriting-Guidelines.pdf, section 7.5, p. 54)").
    A follow-up keeps those sections loaded, so the bot can't call its own earlier citation wrong."""
    pages = []
    for m in history:
        if m["role"] != "assistant":
            continue
        for part in re.findall(r"\(([^()]*)\)", m["content"]):
            for cite in part.split(";"):
                if filename in cite:
                    pages += [int(n) for n in re.findall(r"\bp{1,2}\.\s*(\d+)", cite)]
    return list(dict.fromkeys(pages))


class SectionRoute(BaseModel):
    sections: list[str]  # e.g. ["5.3", "7.2"]


def _toc_summary(toc: list[dict]) -> str:
    mid = [e for e in toc if len(e["num"].split(".")) <= 2]
    return "\n".join(f"§{e['num']} {e['title']}" for e in mid)


def _section_span(section_num: str, toc: list[dict], n_pages: int, offset: int) -> tuple[int, int]:
    """Physical page index range (inclusive) for a section and all its children."""
    entry = next((e for e in toc if e["num"] == section_num), None)
    if not entry:
        return 0, 0

    start_idx = entry["page"] - 1 + offset
    depth = len(section_num.split("."))

    # Next entry at same or shallower depth that isn't a child of this section
    end_entry = None
    found = False
    for e in toc:
        if found:
            if len(e["num"].split(".")) <= depth and not e["num"].startswith(section_num + "."):
                end_entry = e
                break
        if e["num"] == section_num:
            found = True

    end_idx = (end_entry["page"] - 2 + offset) if end_entry else (n_pages - 1)
    end_idx = max(start_idx, min(end_idx, n_pages - 1))
    return start_idx, end_idx


def _section_of_page(page: int, toc: list[dict], n_pages: int, offset: int) -> str | None:
    """The mid-level section (e.g. "7.5") whose pages hold this 1-based physical page."""
    best = None
    for e in toc:
        if len(e["num"].split(".")) <= 2:
            start, end = _section_span(e["num"], toc, n_pages, offset)
            if start <= page - 1 <= end:
                best = e["num"]   # later (deeper or next) entries win over a chapter heading that spans it
    return best


def route_and_load(question: str, pdf_path: Path, doc_name: str, model: str,
                   keep_pages: list[int] = ()) -> dict | None:
    """
    Route question to relevant TOC sections, load their complete text.
    keep_pages: pages cited earlier in the chat; their sections load too (up to 3 more).
    Returns None if the PDF has no TOC or no relevant sections found.
    """
    with pdfplumber.open(pdf_path) as pdf:
        toc = _parse_toc(pdf)
        if len(toc) < 5:
            return None
        offset = _detect_offset(pdf, toc)
        n_pages = len(pdf.pages)

        try:
            route = ask_json(
                ROUTE_SYSTEM,
                f"Question: {question}\n\nTable of contents:\n{_toc_summary(toc)}",
                SectionRoute,
                model,
            )
        except Exception:
            return None

        wanted = list(route.sections[:3])
        for p in keep_pages:
            sec = _section_of_page(p, toc, n_pages, offset)
            if sec and sec not in wanted and len(wanted) < len(route.sections[:3]) + 3:
                wanted.append(sec)
        if not wanted:
            return None

        sections_loaded = []
        for sec_num in wanted:
            start, end = _section_span(sec_num, toc, n_pages, offset)
            if start > end:
                continue
            pages_text = [
                (pdf.pages[pi].extract_text() or "").strip()
                for pi in range(start, end + 1)
            ]
            pages_text = [t for t in pages_text if t]
            if not pages_text:
                continue
            entry = next((e for e in toc if e["num"] == sec_num), None)
            title = entry["title"] if entry else sec_num
            page_range = f"p. {start + 1}" if start == end else f"p. {start + 1}-{end + 1}"
            sections_loaded.append({
                "section": sec_num,
                "section_title": title,
                "loc": page_range,
                "file": doc_name,
                "text": "\n\n".join(pages_text),
                "kind": "section",
                "program": "NonQM Shared",
            })

    if not sections_loaded:
        return None

    combined = "\n\n---\n\n".join(
        f"[§{s['section']} {s['section_title']} | {s['file']} | {s['loc']}]\n\n{s['text']}"
        for s in sections_loaded
    )
    return {"text": combined, "sections": sections_loaded}
