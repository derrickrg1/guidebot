"""Chat history, one SQLite file on this PC (data/chats.db, git-ignored).

Each chat belongs to the person signed in through Cloudflare Access ("local" when the page is opened on this PC).
People only see their own chats; the admin console sees everyone's. Turns are saved by the server after the
answer finishes, so what's stored is exactly what was shown.
"""
import json
import sqlite3
import time
from contextlib import contextmanager

from guidebot import config

DB = config.ROOT / "data" / "chats.db"


def user(headers) -> str:
    return (headers.get("Cf-Access-Authenticated-User-Email") or "").strip().lower() or "local"


@contextmanager
def _db():
    """A connection that commits on success and always closes (sqlite3's own `with` only commits)."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, email TEXT NOT NULL, title TEXT NOT NULL,
            program TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (chat_id TEXT NOT NULL, n INTEGER NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]', ts REAL NOT NULL, PRIMARY KEY (chat_id, n));
        CREATE INDEX IF NOT EXISTS chats_by_user ON chats (email, updated);
        CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY, ts REAL NOT NULL, email TEXT NOT NULL,
            chat_id TEXT NOT NULL DEFAULT '', question TEXT NOT NULL, answer TEXT NOT NULL, note TEXT NOT NULL,
            program TEXT NOT NULL DEFAULT '', section TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS traces (ts REAL NOT NULL, email TEXT NOT NULL, chat_id TEXT NOT NULL,
            question TEXT NOT NULL, program TEXT NOT NULL, mode TEXT NOT NULL, model TEXT NOT NULL, secs REAL NOT NULL,
            fallback INTEGER NOT NULL, sources TEXT NOT NULL, answer_chars INTEGER NOT NULL);
    """)
    try:
        with con:
            yield con
    finally:
        con.close()


def save_turn(chat_id: str, email: str, question: str, answer: str, program: str = "", mode: str = "",
              sources: list | None = None) -> bool:
    """Adds one question and its answer. False if the id belongs to someone else (nothing saved)."""
    if not chat_id or not answer:
        return False
    now = time.time()
    with _db() as con:
        row = con.execute("SELECT email FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if row and row["email"] != email:
            return False
        if row:
            con.execute("UPDATE chats SET updated = ?, program = ?, mode = ? WHERE id = ?", (now, program, mode, chat_id))
        else:
            con.execute("INSERT INTO chats VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (chat_id, email, " ".join(question.split())[:90], program, mode, now, now))
        n = con.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,)).fetchone()[0]
        con.execute("INSERT INTO messages VALUES (?, ?, 'user', ?, '[]', ?)", (chat_id, n, question, now))
        con.execute("INSERT INTO messages VALUES (?, ?, 'assistant', ?, ?, ?)",
                    (chat_id, n + 1, answer, json.dumps(sources or []), now))
    return True


def list_chats(email: str | None = None, limit: int = 60) -> list[dict]:
    """A person's chats, newest first. email=None lists everyone's (admin)."""
    sql = ("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.id) / 2 AS turns FROM chats c"
           + (" WHERE email = ?" if email is not None else "") + " ORDER BY updated DESC LIMIT ?")
    with _db() as con:
        return [dict(r) for r in con.execute(sql, ((email,) if email is not None else ()) + (limit,))]


def get_chat(chat_id: str, email: str | None = None) -> dict | None:
    """One chat with its messages. With an email, only if it's that person's."""
    with _db() as con:
        row = con.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if not row or (email is not None and row["email"] != email):
            return None
        msgs = [{"role": m["role"], "content": m["content"], "sources": json.loads(m["sources"])}
                for m in con.execute("SELECT * FROM messages WHERE chat_id = ? ORDER BY n", (chat_id,))]
    return {**dict(row), "messages": msgs}


def delete_chat(chat_id: str, email: str) -> bool:
    with _db() as con:
        if not con.execute("SELECT 1 FROM chats WHERE id = ? AND email = ?", (chat_id, email)).fetchone():
            return False
        con.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        con.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return True


def save_feedback(email: str, question: str, answer: str, note: str, chat_id: str = "", program: str = "",
                  section: str = "", mode: str = "") -> int:
    """A "This is wrong" report. tools/stress_bank.py turns each one into a test case, so a fixed mistake stays
    fixed."""
    with _db() as con:
        cur = con.execute("INSERT INTO feedback (ts, email, chat_id, question, answer, note, program, section, mode) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (time.time(), email, chat_id[:64], question[:4000], answer[:40000], note[:2000],
                           program[:200], section[:40], mode[:20]))
        return cur.lastrowid


def list_feedback(limit: int = 500) -> list[dict]:
    with _db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM feedback ORDER BY ts DESC LIMIT ?", (limit,))]


def save_trace(email: str, chat_id: str, question: str, program: str, mode: str, model: str, secs: float,
               fallback: bool, sources: list, answer_chars: int):
    """What each answer read and how long it took, so a bad answer can be traced back to its sources."""
    slim = [{k: s.get(k) for k in ("file", "loc", "program", "section") if s.get(k)} for s in sources or []]
    with _db() as con:
        con.execute("INSERT INTO traces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (time.time(), email, chat_id, question[:4000], program, mode, model, round(secs, 2),
                     int(fallback), json.dumps(slim), answer_chars))


def recent_traces(limit: int = 200) -> list[dict]:
    with _db() as con:
        return [{**dict(r), "sources": json.loads(r["sources"])}
                for r in con.execute("SELECT * FROM traces ORDER BY ts DESC LIMIT ?", (limit,))]
