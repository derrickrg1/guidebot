"""Share links (2026-11-16): one answer, its question and its citation chips, frozen as they were shown, at
/s/<token>. Only people Cloudflare Access lets in can open one (the same people who can use AskNick).

Stored in data/chats.db, table shares. The token is random (16 bytes), so links can't be guessed.
"""
import json
import secrets
import time

from guidebot import history


def _table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS shares (token TEXT PRIMARY KEY, email TEXT NOT NULL,
        question TEXT NOT NULL, answer TEXT NOT NULL, sources TEXT NOT NULL, program TEXT NOT NULL DEFAULT '',
        ts REAL NOT NULL)""")


def create(email: str, question: str, answer: str, sources: list, program: str = "") -> str:
    token = secrets.token_urlsafe(16)
    with history._db() as con:
        _table(con)
        con.execute("INSERT INTO shares VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (token, email, question[:20000], answer[:100000], json.dumps(sources or [])[:50000],
                     program[:200], time.time()))
    return token


def get(token: str) -> dict | None:
    with history._db() as con:
        _table(con)
        r = con.execute("SELECT * FROM shares WHERE token = ?", (token,)).fetchone()
    if not r:
        return None
    return {"question": r["question"], "answer": r["answer"], "sources": json.loads(r["sources"]),
            "program": r["program"], "by": r["email"], "ts": r["ts"]}
