"""Keep the vector DB in sync with the docs folder. Only new or changed files get re-indexed."""
import hashlib
import json
from pathlib import Path

import chromadb

from guidebot import config
from guidebot import library
from guidebot.ingest.chunker import chunk_blocks
from guidebot.ingest.loaders import load_file
from guidebot.ingest.section_chunker import has_toc, section_chunks
from guidebot.llm import embed_docs

BATCH = 64


def get_collection(db_dir: Path = config.DB_DIR):
    db_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db_dir))
    return client.get_or_create_collection("guidelines", metadata={"hnsw:space": "cosine"})


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def program_for(path: Path, docs_dir: Path) -> str:
    parts = path.relative_to(docs_dir).parts
    return parts[0] if len(parts) > 1 else "General"


def sync_index(docs_dir: Path = config.DOCS_DIR, db_dir: Path = config.DB_DIR, embed=embed_docs, log=print) -> int:
    docs_dir.mkdir(parents=True, exist_ok=True)
    col = get_collection(db_dir)
    manifest_path = db_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    files = {
        p.relative_to(docs_dir).as_posix(): p
        for p in docs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in config.SUPPORTED
    }

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
        digest = file_digest + library.verified_stamp(path, docs_dir)
        if manifest.get(rel) == digest:
            continue
        col.delete(where={"source": rel})
        program = program_for(path, docs_dir)
        try:
            if path.suffix.lower() == ".pdf" and has_toc(path):
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
        log(f"Indexed {rel}: {len(chunks)} chunks")

    manifest_path.write_text(json.dumps(manifest, indent=2))
    return col.count()
