"""What each answer cost and which engine wrote it. One JSON line per answer in data/usage.jsonl (git-ignored): when,
who (Access email), engine (claude or local; gemini on rows before 2026-11-18), model, tokens and dollars. No questions, no answers.

An answer is several model calls (routing, the yes/no check, the answer itself). llm.py adds each call's cost to the
answer in progress on this thread (begin/add/end); the server records it when the answer is done. The engine is the
furthest down the fallback line any call went: claude, then local.

Prices are list prices per million tokens.
"""
import json
import threading
import time

from guidebot import config

LOG = config.ROOT / "data" / "usage.jsonl"
PRICES = {   # $ per million tokens: input, output, 5-minute cache write, cache read
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "write": 5.00, "read": 0.20},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "write": 2.50, "read": 0.20},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "write": 0.125, "read": 0.01},
}
ENGINES = ("claude", "gemini", "local")
_lock = threading.Lock()
_run = threading.local()


def cost(t: dict, model: str) -> dict:
    """t: {"in", "cached", "wrote", "out"} token counts. A model not in PRICES costs nothing (local)."""
    p = PRICES.get(model, {"in": 0, "out": 0, "write": 0, "read": 0})
    usd = (t["in"] * p["in"] + t["out"] * p["out"] + t["wrote"] * p["write"] + t["cached"] * p["read"]) / 1_000_000
    return t | {"model": model, "usd": round(usd, 6)}


def claude_tokens(u) -> dict:
    get = lambda k: getattr(u, k, 0) or 0
    return {"in": get("input_tokens"), "cached": get("cache_read_input_tokens"),
            "wrote": get("cache_creation_input_tokens"), "out": get("output_tokens")}


def begin():
    _run.cur = {"engine": "claude", "model": config.CHAT_MODEL, "in": 0, "cached": 0, "wrote": 0, "out": 0,
                "usd": 0.0, "calls": 0}


def add(engine: str, spent: dict | None = None):
    """One model call's cost, on the answer in progress (no-op outside one)."""
    cur = getattr(_run, "cur", None)
    if cur is None:
        return
    if ENGINES.index(engine) >= ENGINES.index(cur["engine"]):
        cur["engine"] = engine
        cur["model"] = (spent or {}).get("model") or {"local": config.LOCAL_MODEL}.get(engine, cur["model"])
    for k in ("in", "cached", "wrote", "out", "usd"):
        cur[k] += (spent or {}).get(k, 0)
    cur["calls"] += 1


def end() -> dict | None:
    cur, _run.cur = getattr(_run, "cur", None), None
    if cur:
        cur["usd"] = round(cur["usd"], 6)
    return cur


def record(email: str, run: dict):
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": email} | run
    with _lock:
        LOG.parent.mkdir(exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as out:
            out.write(json.dumps(row) + "\n")


def summary(email: str | None = None) -> dict:
    """This month's and all-time totals, for one person or everyone."""
    month = time.strftime("%Y-%m")
    tot = {"month": {"usd": 0.0, "runs": 0}, "all": {"usd": 0.0, "runs": 0}}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if email is not None and r.get("user") != email:
                continue
            for key in ("all",) + (("month",) if r["at"].startswith(month) else ()):
                tot[key]["usd"] += r.get("usd", 0)
                tot[key]["runs"] += 1
    for v in tot.values():
        v["usd"] = round(v["usd"], 2)
    return tot
