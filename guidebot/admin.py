"""Admin console backend: the Verify Documents page without Streamlit, served at /admin by server.py.

Only the admin can reach it (see allowed()). Approving is always a person clicking Approve; nothing here
approves on its own. Same checks as the Streamlit page: header problems and Revo site mismatches block
approval, and overlay rules need an approved source document with every quote found word for word.
"""
import io

import pandas as pd

from guidebot import crosscheck, library, overlay_rules
from guidebot.extract_pdf import page_image
from guidebot.matrix_lookup import header_problems

ADMIN_EMAILS = {"derrick.garcia.r@gmail.com"}


def allowed(headers) -> bool:
    """Through the Cloudflare tunnel, Access names the signed-in user; only the admin gets in. A request
    with no Cloudflare headers came straight from this PC (the server only listens on 127.0.0.1)."""
    email = (headers.get("Cf-Access-Authenticated-User-Email") or "").strip().lower()
    via_cloudflare = any(headers.get(h) for h in ("Cf-Connecting-Ip", "Cf-Ray", "Cf-Access-Jwt-Assertion"))
    return email in ADMIN_EMAILS if (via_cloudflare or email) else True


def _doc_path(program: str, file: str):
    return next((p for p in library.programs().get(program, []) if p.name == file), None)


def _problems(pages, file_hash: str) -> list[str]:
    out = []
    refs = iter(crosscheck.check({"hash": file_hash, "pages": pages}))
    for page in pages:
        for m in page["matrices"]:
            name = f"p. {page['page']} {m.attrs.get('title') or 'LTV matrix'}"
            out += [f"{name}: {p}" for p in header_problems(m)]
            ref = next(refs)
            if ref["status"] == "mismatch":
                out += [f"{name}: differs from Revo site ({ref['source']}): {d}" for d in ref["diffs"][:5]]
    return out


def _rules_info(program: str, doc: dict) -> dict | None:
    rules = overlay_rules.for_program(program, [doc])
    if not rules or rules["source"] != doc["path"].name:
        return None
    return {"status": rules["status"], "bad_quotes": rules["bad_quotes"], "built_by": rules.get("built_by", ""),
            "rows": [dict(zip(["label", "when", "effect", "quote", "page"], r)) for r in overlay_rules.rules_text(rules)]}


def overview() -> list[dict]:
    """Every program and document with its status, worst first within each program."""
    out = []
    for prog, paths in library.programs().items():
        keep, dups = library.unique_docs(paths)
        docs = []
        for path in keep:
            doc = library.load(path)
            rules = _rules_info(prog, doc)
            problems = _problems(doc["pages"], doc["hash"]) if doc["status"] == "verified" else []
            docs.append({"file": path.name, "status": doc["status"], "problems": len(problems),
                         "matrices": sum(len(p["matrices"]) for p in doc["pages"]),
                         "rules": rules and {"status": rules["status"], "count": len(rules["rows"])}})
        out.append({"program": prog, "docs": docs, "duplicates": [[a.name, b.name] for a, b in dups]})
    return out


def document(program: str, file: str, fresh: bool = False) -> dict | None:
    path = _doc_path(program, file)
    if not path:
        return None
    doc = library.load(path)
    saved_problems = _problems(doc["pages"], doc["hash"]) if doc["status"] == "verified" else []
    view = library.load(path, fresh=True) if fresh else doc
    refs = iter(crosscheck.check(view))
    pages = []
    for page in view["pages"]:
        mats = []
        for m in page["matrices"]:
            ref = next(refs)
            mats.append({"title": m.attrs.get("title") or "LTV matrix", "columns": [str(c) for c in m.columns],
                         "rows": m.astype(str).values.tolist(), "problems": header_problems(m),
                         "crosscheck": ref})
        pages.append({"page": page["page"], "text": page["text"], "matrices": mats})
    return {"program": program, "file": file, "status": doc["status"], "fresh": fresh,
            "is_pdf": path.suffix.lower() == ".pdf", "saved_problems": saved_problems,
            "blocked": _problems(view["pages"], view["hash"]), "pages": pages, "rules": _rules_info(program, doc)}


def page_png(program: str, file: str, page: int) -> bytes | None:
    path = _doc_path(program, file)
    if not path or path.suffix.lower() != ".pdf":
        return None
    buf = io.BytesIO()
    page_image(str(path), page).save(buf, format="PNG")
    return buf.getvalue()


def approve_document(program: str, file: str, fresh: bool, edits: list[dict]) -> dict:
    """Saves the document as verified with the person's edits (cell values and page text) applied on top of
    what they were shown. Headers and matrix shape can't change here; wrong headers need a parser fix."""
    path = _doc_path(program, file)
    if not path:
        return {"error": "Document not found."}
    base = library.load(path, fresh=fresh)
    by_page = {e["page"]: e for e in edits}
    pages = []
    for page in base["pages"]:
        e = by_page.get(page["page"], {})
        mats = []
        for i, m in enumerate(page["matrices"]):
            rows = (e.get("matrices") or [None] * len(page["matrices"]))[i]
            if rows is not None:
                if len(rows) != len(m) or any(len(r) != len(m.columns) for r in rows):
                    return {"error": f"Page {page['page']}: matrix shape changed. Reload and try again."}
                new = pd.DataFrame(rows, columns=m.columns)
                new.attrs = dict(m.attrs)
                m = new
            mats.append(m)
        pages.append({"page": page["page"], "text": e.get("text", page["text"]), "matrices": mats})
    blocked = _problems(pages, base["hash"])
    if blocked:
        return {"error": "Can't approve yet.", "blocked": blocked}
    library.save_verified(path, pages)
    return {"ok": True}


def approve_rules(program: str, file: str) -> dict:
    path = _doc_path(program, file)
    if not path:
        return {"error": "Document not found."}
    doc = library.load(path)
    if doc["status"] != "verified":
        return {"error": "Approve the document itself first. Rule quotes are checked against its approved text."}
    rules = overlay_rules.for_program(program, [doc])
    if not rules or rules["source"] != path.name:
        return {"error": "This document has no overlay rules."}
    if rules["bad_quotes"]:
        return {"error": "These quotes aren't in the document word for word: " + ", ".join(rules["bad_quotes"])}
    overlay_rules.approve(program, rules)
    return {"ok": True}
