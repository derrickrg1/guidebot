"""Page audit: every program PDF's approved copy against a fresh read of the PDF, and every page (program docs and
agency guides) that has images but no text. Read only.

    .venv\\Scripts\\python.exe -m tools.page_audit        -> data/audit/pages.md

Flags:
- approved copy is missing text a fresh read has (Express Bank Statements p. 1 had none, 2026-11-12)
- approved and fresh text differ a lot (the PDF reads differently now: re-read and re-approve)
- a page has images but no text: it's a scan or a picture of a table, and only OCR or a vision read gets it
"""
import re
import sys
from collections import Counter
from pathlib import Path

import pdfplumber

from guidebot import agency, library
from guidebot.extract_pdf import PDFIUM_LOCK

OUT = Path(__file__).resolve().parents[1] / "data" / "audit" / "pages.md"


def _text(page: dict) -> str:
    return " ".join((page.get("text") or "").split())


def _same_words(a: str, b: str) -> float:
    """Share of words the two texts have in common, ignoring order and layout (a table read as rows vs as
    "Label: value" lines is the same content)."""
    ca, cb = Counter(re.findall(r"[a-z0-9]+", a.lower())), Counter(re.findall(r"[a-z0-9]+", b.lower()))
    return sum((ca & cb).values()) / max(sum(ca.values()), sum(cb.values()), 1)


def image_only_pages(path: Path, texts: list[str]) -> list[int]:
    out = []
    with PDFIUM_LOCK, pdfplumber.open(path) as pdf:
        for n, (pg, t) in enumerate(zip(pdf.pages, texts), start=1):
            # a logo on a blank page isn't content: the images must cover a fifth of the page
            covered = sum(abs((i["x1"] - i["x0"]) * (i["bottom"] - i["top"])) for i in pg.images)
            if len(t.strip()) < 40 and covered > 0.2 * pg.width * pg.height:
                out.append(n)
    return out


def audit_programs(log=print) -> list[str]:
    lines = []
    for prog, paths in sorted(library.programs().items()):
        for path in library.unique_docs(paths)[0]:
            if path.suffix.lower() != ".pdf":
                continue
            fresh = library.load(path, fresh=True)
            approved = library.load(path)
            probs = []
            if approved["status"] == "verified":
                for a, f in zip(approved["pages"], fresh["pages"]):
                    at, ft = _text(a), _text(f)
                    if len(ft) > 200 and len(at) < 0.2 * len(ft):
                        probs.append(f"p. {a['page']}: approved copy has {len(at)} characters of text, a fresh read has "
                                     f"{len(ft)}. Re-read and approve.")
                    elif at and ft and _same_words(at, ft) < 0.9:
                        probs.append(f"p. {a['page']}: approved text and a fresh read have different words. Re-read and compare.")
                    if len(a["matrices"]) < len(f["matrices"]):
                        probs.append(f"p. {a['page']}: a fresh read finds {len(f['matrices'])} table(s), the approved copy "
                                     f"{len(a['matrices'])}.")
            imgs = image_only_pages(path, [_text(p) for p in fresh["pages"]])
            if imgs:
                probs.append(f"images but no text on p. {', '.join(map(str, imgs))}: needs OCR or a vision read")
            if probs:
                lines += [f"### {prog} / {path.name} ({approved['status']})"] + [f"- {p}" for p in probs] + [""]
            log(f"{prog}/{path.name}: {len(probs)} flag(s)")
    return lines


def audit_agency(log=print) -> list[str]:
    lines = []
    for path in agency.files():
        imgs = image_only_pages(path, agency.pages(path))
        if imgs:
            shown = ", ".join(map(str, imgs[:30])) + (f" (+{len(imgs) - 30} more)" if len(imgs) > 30 else "")
            lines.append(f"- {path.name}: images but no text on p. {shown}")
            log(f"{path.name}: {len(imgs)} image-only page(s)")
    return lines


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    prog = audit_programs()
    ag = audit_agency()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(["# Page audit", "", "## Program documents", ""] + (prog or ["Nothing flagged.", ""])
                             + ["## Agency guides", ""] + (ag or ["Nothing flagged."])) + "\n", encoding="utf-8")
    print(f"\n{OUT}")


if __name__ == "__main__":
    main()
