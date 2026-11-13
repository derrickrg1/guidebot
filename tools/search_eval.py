"""Scores agency guide search against tools/search_cases.py, outside the server and without touching ChromaDB.

    .venv\\Scripts\\python.exe -m tools.search_eval nomic-embed-text bge-m3 qwen3-embedding:4b
    .venv\\Scripts\\python.exe -m tools.search_eval nomic-embed-text --rerank

Builds the same one-page chunks the indexer does, embeds them with each model (cached in data/search_eval/), and runs
the same hybrid search (vectors + BM25, merged) and the same pick as chat._agency_hits (6 handbook pages, each with
its next page, the top 3 with the page before, plus 3 letter/circular pages). A case passes when a gold page is
among the pages the answer would actually read. Also: was the best hit a gold page (hit@1).
"""
import re
import sys
import time
from pathlib import Path

import numpy as np
import ollama

from guidebot import agency
from guidebot.retrieval import rrf, tokenize
from tools.search_cases import CASES

CACHE = Path(__file__).resolve().parents[1] / "data" / "search_eval"
QWEN_TASK = "Instruct: Given a mortgage underwriting question, retrieve the guideline page that answers it\nQuery: "
PREFIX = {   # (document, query) prefixes each model was trained with
    "nomic-embed-text": ("search_document: ", "search_query: "),
    "qwen3-embedding": ("", QWEN_TASK),
}


def prefixes(model: str):
    return PREFIX.get(model.split(":")[0], ("", ""))


def corpus() -> list[dict]:
    out = []
    for p in agency.files():
        rec = agency.info(p.name) or {}
        for n, t in enumerate(agency.pages(p), start=1):
            if len(t.strip()) > 40:
                program = f"{agency.section_of(p)} Agency"
                out.append({"file": p.name, "rel": rec.get("rel", ""), "page": n, "program": program,
                            "text": f"[{program} | {p.name}]\n=== {p.name} | p. {n} ===\n{t.strip()}",
                            "flat": re.sub(r"\s+", " ", t)})
    return out


def embeddings(model: str, docs: list[dict]) -> np.ndarray:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{model.replace(':', '_')}-{len(docs)}.npy"
    if path.exists():
        return np.load(path)
    pre, vecs = prefixes(model)[0], []
    for i in range(0, len(docs), 32):
        batch = [pre + d["text"][:6000] for d in docs[i:i + 32]]
        for attempt in range(5):   # Windows ran out of sockets once mid-run; a pause clears it
            try:
                vecs += ollama.embed(model=model, input=batch, options={"num_ctx": 8192})["embeddings"]
                break
            except ollama.ResponseError:
                if attempt == 4:
                    raise
                time.sleep(10)
        print(f"  {model}: {i + len(batch)}/{len(docs)}", end="\r", flush=True)
    arr = np.array(vecs, dtype=np.float32)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    np.save(path, arr)
    print()
    return arr


class Search:
    def __init__(self, model: str, docs: list[dict], reranker=None):
        from rank_bm25 import BM25Okapi
        self.model, self.docs, self.reranker = model, docs, reranker
        self.vecs = embeddings(model, docs)
        self.bm25 = BM25Okapi([tokenize(d["text"]) for d in docs])

    def pool(self, q: str, sec: str, n: int) -> list[int]:
        allowed = np.array([d["program"] == f"{sec} Agency" for d in self.docs])
        qv = np.array(ollama.embed(model=self.model, input=[prefixes(self.model)[1] + q])["embeddings"][0])
        sims = np.where(allowed, self.vecs @ (qv / np.linalg.norm(qv)), -9)
        vec = [int(i) for i in np.argsort(-sims)[:n]]
        bm = self.bm25.get_scores(tokenize(q))
        bm = np.where(allowed, bm, 0)
        kw = [int(i) for i in np.argsort(-bm)[:n] if bm[i] > 0]
        return [int(i) for i in rrf([[str(i) for i in vec], [str(i) for i in kw]])][:n]

    def read(self, q: str, sec: str, folder: str) -> tuple[list[tuple], tuple | None]:
        """(file, page) pairs the answer would read, and the single best hit."""
        pool = self.pool(q, sec, 80 if folder else 40)
        if folder:
            pool = [i for i in pool if self.docs[i]["rel"].startswith(f"{sec}/{folder}/")][:40]
        if self.reranker:
            pool = self.reranker(q, [self.docs[i]["text"] for i in pool], pool)
        letter = lambda i: re.search(r"Mortgagee Letter|Circular", self.docs[i]["file"]) is not None
        books = sorted((i for i in pool if not letter(i)), key=lambda i: agency.off_topic(self.docs[i]["text"]))
        found = books[:6] + [i for i in pool if letter(i)][:3]
        seen = []
        for rank, i in enumerate(found):
            d = self.docs[i]
            seen.append((d["file"], d["page"]))
            if not letter(i):
                seen.append((d["file"], d["page"] + 1))
                if rank < 3:
                    seen.append((d["file"], d["page"] - 1))
        best = (self.docs[found[0]]["file"], self.docs[found[0]]["page"]) if found else None
        self.last_pool = [(self.docs[i]["file"], self.docs[i]["page"]) for i in found]
        return seen, best


def gold(docs: list[dict]) -> list[set]:
    out = []
    for sec, folder, fc, _q, rx in CASES:
        out.append({(d["file"], d["page"]) for d in docs if d["program"] == f"{sec} Agency" and fc in d["file"]
                    and (not folder or d["rel"].startswith(f"{sec}/{folder}/")) and re.search(rx, d["flat"], re.I)})
    return out


def score(search: Search, golds: list[set], show: bool = False) -> dict:
    read = top1 = top3 = 0
    rr = 0.0
    misses = []
    for (sec, folder, _fc, q, _rx), g in zip(CASES, golds):
        seen, best = search.read(q, sec, folder)
        read += bool(g & set(seen))
        top1 += best in g
        ranks = [k for k, hit in enumerate(search.last_pool) if hit in g]
        top3 += bool(ranks and ranks[0] < 3)
        rr += 1 / (ranks[0] + 1) if ranks else 0
        if not g & set(seen):
            misses.append(q)
    if show:
        for q in misses:
            print("   miss:", q)
    n = sum(bool(g) for g in golds)
    return {"cases": n, "answer reads a gold page": f"{read}/{n}", "best hit is gold": f"{top1}/{n}",
            "gold in top 3": f"{top3}/{n}", "MRR": round(rr / n, 3)}


def main(args: list[str]):
    rerank_name = None
    if "--rerank" in args:
        i = args.index("--rerank")
        rerank_name = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("-") and "/" in args[i + 1] else None
        args = [a for j, a in enumerate(args) if j != i and a != rerank_name]
        from guidebot import rerank
        rerank_name = rerank_name or rerank.MODEL
    docs = corpus()
    golds = gold(docs)
    empty = [c[3] for c, g in zip(CASES, golds) if not g]
    if empty:
        print("cases with no gold page (fix the regex):", *empty, sep="\n  ")
    for model in args or ["nomic-embed-text"]:
        print(f"{model}: {score(Search(model, docs), golds, show=True)}")
        if rerank_name:
            from guidebot import rerank
            r = lambda q, texts, ids: [ids[j] for j in rerank.order(q, texts, name=rerank_name)]
            print(f"{model} + {rerank_name}: {score(Search(model, docs, r), golds, show=True)}")


if __name__ == "__main__":
    main(sys.argv[1:])
