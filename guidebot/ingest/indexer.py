"""Keep the vector DB in sync with the docs folder. Only new or changed files get re-indexed."""
import hashlib
import json
import threading
from pathlib import Path

import chromadb

from guidebot import config
from guidebot import library
from guidebot.ingest.chunker import chunk_blocks
from guidebot.ingest.loaders import load_file
from guidebot.ingest.section_chunker import has_toc, section_chunks
from guidebot.llm import embed_docs

BATCH = 64
# The admin Index button and the weekly agency check both run inside the server; they take turns.
_lock = threading.Lock()


def get_collection(db_dir: Path = config.DB_DIR):
    db_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db_dir))
    return client.get_or_create_collection("guidelines", metadata={"hnsw:space": "cosine"})


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def program_for(path: Path, docs_dir: Path) -> str:
    parts = path.relative_to(docs_dir).parts
    return parts[0] if len(parts) > 1 else "General"


def _agency_chunks(path: Path) -> list[dict]:
    from guidebot import agency
    return [{"text": f"=== {path.name} | p. {n} ===\n{t.strip()}", "loc": f"p. {n}", "kind": "page"}
            for n, t in enumerate(agency.pages(path), start=1) if len(t.strip()) > 40]


def sync_index(docs_dir: Path = config.DOCS_DIR, db_dir: Path = config.DB_DIR, embed=embed_docs, log=print,
               agency_dir: Path | None = None) -> int:
    """Agency guides (agency/<Section>/...) are indexed too, as program "<Section> Agency", one chunk per page.
    They come along by default only with the real docs folder, so tests with their own folders don't pull them in."""
    if agency_dir is None and docs_dir == config.DOCS_DIR:
        agency_dir = config.AGENCY_DIR
    with _lock:
        return _sync(docs_dir, db_dir, embed, log, agency_dir)


def _sync(docs_dir, db_dir, embed, log, agency_dir) -> int:
    docs_dir.mkdir(parents=True, exist_ok=True)
    col = get_collection(db_dir)
    manifest_path = db_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    files = {
        p.relative_to(docs_dir).as_posix(): p
        for p in docs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in config.SUPPORTED
    }
    if agency_dir and agency_dir.exists():
        from guidebot import agency
        files.update({"agency/" + p.relative_to(agency_dir).as_posix(): p for p in agency.files()})

    for rel in sorted(set(manifest) - set(files)):
        col.delete(where={"source": rel})
        del manifest[rel]
        log(f"Removed {rel}")

    seen = {}
    for rel, path in sorted(files.items()):
        file_digest = _hash(path)
        if file_digest in seen:
            col.delete(where={"source": rel})
            manifest.pop(rel, None)
            log(f"Skipped {rel}: exact copy of {seen[file_digest]}")
            continue
        seen[file_digest] = rel
        is_agency = rel.startswith("agency/")
        digest = file_digest + ("" if is_agency else library.verified_stamp(path, docs_dir))
        if manifest.get(rel) == digest:
            continue
        col.delete(where={"source": rel})
        program = f"{rel.split('/')[1]} Agency" if is_agency else program_for(path, docs_dir)
        try:
            if is_agency:
                chunks = _agency_chunks(path)
            elif path.suffix.lower() == ".pdf" and has_toc(path):
                chunks = section_chunks(path, path.name, program)
            elif path.suffix.lower() == ".pdf":
                doc = library.load(path, docs_dir)
                chunks = [{"text": library.render_page(path.name, p), "loc": f"p. {p['page']}", "kind": "page"}
                          for p in doc["pages"]]
            else:
                chunks = chunk_blocks(load_file(path))
        except Exception as e:
            log(f"Skipped {rel}: {e}")
            continue

        for start in range(0, len(chunks), BATCH):
            batch = chunks[start:start + BATCH]
            # section chunks already have their prefix baked in; page/block chunks get it added here
            documents = [
                c["text"] if c.get("kind") == "section"
                else f"[{program} | {path.name}]\n{c['text']}"
                for c in batch
            ]
            col.add(
                ids=[f"{rel}::{start + i}" for i in range(len(batch))],
                documents=documents,
                embeddings=embed(documents),
                metadatas=[
                    {"source": rel, "file": path.name, "program": program, "loc": c["loc"],
                     "kind": c["kind"], "section": c.get("section", ""), "section_title": c.get("section_title", "")}
                    for c in batch
                ],
            )
        manifest[rel] = digest
        manifest_path.write_text(json.dumps(manifest, indent=2))   # a long agency run that stops keeps what it did
        log(f"Indexed {rel}: {len(chunks)} chunks")

    manifest_path.write_text(json.dumps(manifest, indent=2))
    return col.count()
