"""Answer cache (2026-11-16): the same first question on the same tab, program and mode gets the answer it got last
time, instantly and for free. Two LOs asking the same thing see the same answer.

Only a chat's first question is cached (no history, no attachments): a follow-up depends on the turns before it.
Stress and regression runs send no chat id and are never cached, so they always test the live bot.

An entry is only good while nothing it could depend on has changed: every source PDF, approval, overlay rule file,
Revo rule, agency guide and the bot's own code go into a fingerprint, and a new fingerprint misses every old entry.
"This is wrong" drops the entry for that question, so the next person gets a fresh answer.

SQLite at data/answer_cache.db (git-ignored with the rest of data/).
"""
import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager

from guidebot import config

DB = config.ROOT / "data" / "answer_cache.db"
MAX_AGE = 30 * 86400
WATCH = ("docs", "verified", "overlays", "agency", "guidebot")   # folders whose files change answers
WATCH_FILES = ("revo_overlays.json",)
_lock = threading.Lock()
_fp = {"at": 0.0, "value": ""}


@contextmanager
def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    con.executescript("""CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, question TEXT NOT NULL,
        events TEXT NOT NULL, ts REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0)""")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def fingerprint(ttl: float = 30) -> str:
    """Size and modified time of every file an answer could depend on. Rechecked at most every `ttl` seconds."""
    now = time.time()
    if now - _fp["at"] < ttl and _fp["value"]:
        return _fp["value"]
    h = hashlib.sha1()
    for folder in WATCH:
        base = config.ROOT / folder
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".log"):
                st = p.stat()
                h.update(f"{p.relative_to(config.ROOT).as_posix()}|{st.st_size}|{int(st.st_mtime)}\n".encode())
    for name in WATCH_FILES:
        p = config.ROOT / name
        if p.exists():
            st = p.stat()
            h.update(f"{name}|{st.st_size}|{int(st.st_mtime)}\n".encode())
    _fp.update(at=now, value=h.hexdigest())
    return _fp["value"]


def norm(question: str) -> str:
    """Case, spacing and end punctuation don't make a different question."""
    return re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.! ")


def key(question: str, program: str, section: str, mode_program: bool, mode_scenario: bool, model: str,
        fp: str | None = None) -> str:
    parts = [norm(question), program, section, str(mode_program), str(mode_scenario), model or config.CHAT_MODEL,
             fp or fingerprint()]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def get(k: str) -> list[dict] | None:
    with _lock, _db() as con:
        row = con.execute("SELECT events, ts FROM answers WHERE key = ?", (k,)).fetchone()
        if not row:
            return None
        if time.time() - row[1] > MAX_AGE:
            con.execute("DELETE FROM answers WHERE key = ?", (k,))
            return None
        con.execute("UPDATE answers SET hits = hits + 1 WHERE key = ?", (k,))
        return json.loads(row[0])


def put(k: str, question: str, events: list[dict]):
    with _lock, _db() as con:
        con.execute("INSERT OR REPLACE INTO answers (key, question, events, ts) VALUES (?, ?, ?, ?)",
                    (k, norm(question), json.dumps(events), time.time()))


def forget(question: str) -> int:
    """Drops every cached answer to this question (any tab or mode). Returns how many."""
    with _lock, _db() as con:
        return con.execute("DELETE FROM answers WHERE question = ?", (norm(question),)).rowcount


def stats() -> dict:
    with _lock, _db() as con:
        n, hits = con.execute("SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM answers").fetchone()
    return {"entries": n, "hits": hits}


KEEP = ("text", "replace", "sources", "choices")   # what's replayed; progress lines and flags aren't


def caching(events, k: str, question: str):
    """Passes the answer's events through and saves them once the answer finished cleanly. A backup model's answer
    or an error is never saved, so a cached answer is always one the main model wrote."""
    kept, ok = [], True
    for ev in events:
        if ev.get("fallback") or "error" in ev:
            ok = False
        if any(x in ev for x in KEEP):
            kept.append({x: ev[x] for x in KEEP if x in ev})
        yield ev
    if ok and kept:
        try:
            put(k, question, kept)
        except Exception:
            pass


def replay(events: list[dict], ts_note: bool = True):
    yield {"cached": True}
    yield from events
