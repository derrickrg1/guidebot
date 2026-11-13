"""The program library: what docs exist, their clean content, and whether you've verified them."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from guidebot import config, crosscheck
from guidebot.extract_pdf import extract_document, matrix_to_md
from guidebot.ingest.loaders import load_file

VERIFIED_DIR = config.ROOT / "verified"
_cache: dict = {}


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def programs(docs_dir: Path = config.DOCS_DIR) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for p in sorted(docs_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in config.SUPPORTED:
            parts = p.relative_to(docs_dir).parts
            out.setdefault(parts[0] if len(parts) > 1 else "General", []).append(p)
    return out


def unique_docs(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, Path]]]:
    """Drop byte-identical copies. Returns (keep, [(duplicate, original)])."""
    seen, keep, dups = {}, [], []
    for p in paths:
        h = file_hash(p)
        if h in seen:
            dups.append((p, seen[h]))
        else:
            seen[h] = p
            keep.append(p)
    return keep, dups


def _verified_path(path: Path, docs_dir: Path) -> Path:
    return VERIFIED_DIR / (path.relative_to(docs_dir).as_posix().replace("/", "__") + ".json")


def _to_json(pages):
    return [
        {
            "page": p["page"],
            "text": p["text"],
            "matrices": [
                {"title": m.attrs.get("title", ""), "dims": m.attrs.get("dims", []),
                 **{k: m.attrs[k] for k in ("kind", "notes", "limits", "high_label") if k in m.attrs},
                 "columns": list(m.columns), "rows": m.astype(str).values.tolist()}
                for m in p["matrices"]
            ],
        }
        for p in pages
    ]


def _from_json(data):
    pages = []
    for p in data:
        mats = []
        for m in p["matrices"]:
            df = pd.DataFrame(m["rows"], columns=m["columns"])
            df.attrs["title"], df.attrs["dims"] = m["title"], m["dims"]
            df.attrs.update({k: m[k] for k in ("kind", "notes", "limits", "high_label") if k in m})
            mats.append(df)
        pages.append({"page": p["page"], "text": p["text"], "matrices": mats})
    return pages


def _extract(path: Path):
    if path.suffix.lower() == ".pdf":
        return extract_document(path)
    return [{"page": i, "text": b.text, "matrices": []} for i, b in enumerate(load_file(path), start=1)]


def load(path: Path, docs_dir: Path = config.DOCS_DIR, fresh: bool = False) -> dict:
    """Verified content if you've approved this exact file, otherwise fresh extraction.
    fresh=True re-reads the PDF even if it's verified (status is still reported)."""
    h = file_hash(path)
    vp = _verified_path(path, docs_dir)
    stamp = vp.stat().st_mtime if vp.exists() else 0
    key = (str(path), h, stamp, fresh)
    if key in _cache:
        return _cache[key]

    status, pages = "unverified", None
    if vp.exists():
        data = json.loads(vp.read_text())
        if data["source_hash"] == h:
            status = "verified"
            if not fresh:
                pages = _from_json(data["pages"])
        else:
            status = "changed"
    if pages is None:
        pages = _extract(path)

    result = {"path": path, "status": status, "pages": pages, "hash": h}
    _cache[key] = result
    return result


def save_verified(path: Path, pages, docs_dir: Path = config.DOCS_DIR):
    VERIFIED_DIR.mkdir(parents=True, exist_ok=True)
    data = {"source": path.relative_to(docs_dir).as_posix(), "source_hash": file_hash(path),
            "approved_at": datetime.now().isoformat(timespec="seconds"), "pages": _to_json(pages)}
    _verified_path(path, docs_dir).write_text(json.dumps(data, indent=1))


def verified_stamp(path: Path, docs_dir: Path = config.DOCS_DIR) -> str:
    vp = _verified_path(path, docs_dir)
    return hashlib.sha256(vp.read_bytes()).hexdigest()[:12] if vp.exists() else ""


def render_page(name: str, page: dict) -> str:
    parts = [f"=== {name} | p. {page['page']} ===", page["text"]]
    for m in page["matrices"]:
        parts.append(f"[Matrix: {m.attrs.get('title') or 'LTV matrix'}]\n{matrix_to_md(m)}")
    return "\n\n".join(p for p in parts if p)


def render(doc: dict) -> str:
    return "\n\n".join(render_page(doc["path"].name, p) for p in doc["pages"])


def matrices(docs: list[dict]) -> list[dict]:
    out = []
    for d in docs:
        refs = iter(crosscheck.check(d))
        for p in d["pages"]:
            for m in p["matrices"]:
                out.append({"df": m, "file": d["path"].name, "page": p["page"],
                            "title": m.attrs.get("title") or "LTV matrix", "status": d["status"],
                            "reference": next(refs)})
    return out
