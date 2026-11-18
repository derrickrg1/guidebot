"""Whether Claude can answer right now and, if not, who answers instead: the sidebar's model line.

A token count is free and fails the same way an answer does (out of credits, bad key, unreachable), so status()
asks for one at most every 5 minutes. While it says Claude is down, answers skip Claude and go straight to the next
in line (the local model), so no question pays for a failed Claude call first. Any answer that falls
back makes the next status() check again (stale). Same as AppraisAI's review.status().

This replaced (2026-11-14) the 9 AM / 12 PM / 3 PM re-checks and the hand-entered balance estimate: the API has no
balance endpoint, and the "This month" line (usage.py) shows real spend instead.
"""
import threading
import time

from guidebot import config

MAX_AGE = 300   # seconds
_status = {"at": 0.0}
_lock = threading.Lock()


def is_credit_error(e: Exception) -> bool:
    return "credit balance" in str(e).lower()


def backup() -> tuple[str, str]:
    return "local", config.LOCAL_MODEL


def status(max_age: float = MAX_AGE) -> dict:
    """{"claude": {"ok", "out", "why"}, "backup", "backup_model", "model"}, cached max_age seconds."""
    from guidebot.llm import _anthropic, _down
    with _lock:
        if time.time() - _status["at"] > max_age:
            if not config.CHAT_MODEL.startswith("claude"):
                claude = {"ok": False, "out": False, "why": "not set up"}
            else:
                try:
                    _anthropic().messages.count_tokens(model=config.CHAT_MODEL,
                                                       messages=[{"role": "user", "content": "ok"}])
                    claude = {"ok": True, "out": False, "why": ""}
                except Exception as e:
                    out = is_credit_error(e)
                    claude = {"ok": not (out or _down(e)), "out": out,
                              "why": "credits out" if out else type(e).__name__}
            kind, model = backup()
            _status.update(at=time.time(), claude=claude, backup=kind, backup_model=model, model=config.CHAT_MODEL)
        return {k: v for k, v in _status.items() if k != "at"}


def stale():
    """Next status() checks Claude again (an answer just fell back, or admin asked)."""
    _status["at"] = 0.0


def out() -> bool:
    """Skip Claude: the last check (at most 5 minutes old) said it can't answer."""
    return not status()["claude"]["ok"]
