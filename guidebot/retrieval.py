"""Hybrid search: meaning-based (vectors) plus exact terms (BM25), merged together.
Guideline questions often hinge on exact terms like DSCR, 1.25, or 80%, which pure vector search can miss."""
import re

from rank_bm25 import BM25Okapi

from guidebot import config
from guidebot.llm import embed_query


def tokenize(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?%?|[a-z]+", text.lower())


def rrf(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


class Retriever:
    def __init__(self, collection, embed=embed_query):
        self.col = collection
        self.embed = embed
        data = collection.get(include=["documents", "metadatas"])
        self.ids, self.docs, self.metas = data["ids"], data["documents"], data["metadatas"]
        self.by_id = {i: (d, m) for i, d, m in zip(self.ids, self.docs, self.metas)}
        self.bm25 = BM25Okapi([tokenize(d) for d in self.docs]) if self.docs else None

    def programs(self) -> list[str]:
        return sorted({m["program"] for m in self.metas})

    def search(self, query: str, k: int = config.TOP_K, programs=None) -> list[dict]:
        if not self.docs:
            return []
        pool = k * 3
        where = {"program": {"$in": list(programs)}} if programs else None
        vector_ids = self.col.query(
            query_embeddings=[self.embed(query)], n_results=min(pool, len(self.docs)), where=where
        )["ids"][0]

        scores = self.bm25.get_scores(tokenize(query))
        order = sorted(range(len(self.ids)), key=lambda i: scores[i], reverse=True)
        keyword_ids = [
            self.ids[i] for i in order
            if scores[i] > 0 and (not programs or self.metas[i]["program"] in programs)
        ][:pool]

        hits = []
        for doc_id in rrf([vector_ids, keyword_ids])[:k]:
            text, meta = self.by_id[doc_id]
            hits.append({"id": doc_id, "text": text, **meta})
        return hits
