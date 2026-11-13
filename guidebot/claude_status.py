"""Whether Claude can answer right now, and an estimate of the API credit left.

When Anthropic says the credit balance is too low, AskNick stops calling Claude and answers from the local model
until a check succeeds: at 9 AM, 12 PM and 3 PM (server's clock), or when Derrick clicks "Check now" in admin.
Before this (2026-11-13) every question called Claude first, failed, then fell back.

The API has no balance endpoint, so the balance is an estimate: the amount Derrick says he added, less each
answer's cost from the token counts the API reports (prices per million tokens below). It goes to $0 the moment
Anthropic says the credit is out. State lives in data/claude_status.json.
"""
import datetime as dt
import json
import sys
import threading
import time

from guidebot import config

FILE = config.ROOT / "data" / "claude_status.json"
CHECK_HOURS = (9, 12, 15)
# $ per million tokens: input, output, cache read, cache write (5 minute). Sonnet 5.5 per Anthropic's model list.
PRICES = {"claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50), "claude-opus-5-5": (5.0, 25.0, 0.50, 6.25),
          "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125)}
_lock = threading.Lock()


def load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"out": False}


def _save(d: dict):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def _update(**kw) -> dict:
    with _lock:
        d = load()
        d.update(kw)
        _save(d)
        return d


def out() -> bool:
    return bool(load().get("out"))


def is_credit_error(e: Exception) -> bool:
    import anthropic
    return isinstance(e, anthropic.BadRequestError) and "credit balance" in str(e)


def mark_out(reason: str):
    if not out():
        sys.stderr.write(f"Claude credits out, answering from {config.LOCAL_MODEL} until a check succeeds\n")
        _update(out=True, since=time.time(), reason=reason[:300], balance=0.0)


def spend(model: str, usage) -> None:
    """Takes the answer's cost off the estimated balance. usage: the API's usage object."""
    if usage is None:
        return
    p = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    get = lambda k: getattr(usage, k, 0) or 0
    cost = (get("input_tokens") * p[0] + get("output_tokens") * p[1] + get("cache_read_input_tokens") * p[2]
            + get("cache_creation_input_tokens") * p[3]) / 1e6
    with _lock:
        d = load()
        d["spent"] = d.get("spent", 0.0) + cost
        if d.get("balance") is not None:
            d["balance"] = max(0.0, d["balance"] - cost)
        _save(d)


def set_balance(amount: float) -> dict:
    """Derrick added credits: the estimate starts again from this amount."""
    return _update(balance=float(amount), balance_set=time.time())


def check() -> dict:
    """One tiny Claude call. Success turns Claude back on; a credit error keeps it off."""
    from guidebot.llm import _anthropic
    now = time.time()
    try:
        resp = _anthropic().messages.create(model=config.CLAUDE_MODEL, max_tokens=16,
                                            output_config={"effort": "low"},
                                            messages=[{"role": "user", "content": "Reply OK."}])
        spend(config.CLAUDE_MODEL, resp.usage)
        return _update(out=False, since=None, reason="", last_check=now, last_result="Claude answered")
    except Exception as e:
        if is_credit_error(e):
            mark_out(str(e))
            return _update(last_check=now, last_result="Still out of credit")
        return _update(last_check=now, last_result=f"Couldn't reach Claude: {type(e).__name__}")


def next_check(now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now()
    for h in CHECK_HOURS:
        t = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if t > now:
            return t
    return (now + dt.timedelta(days=1)).replace(hour=CHECK_HOURS[0], minute=0, second=0, microsecond=0)


def summary() -> dict:
    d = load()
    return {"out": bool(d.get("out")), "since": d.get("since"), "balance": d.get("balance"),
            "spent": round(d.get("spent", 0.0), 4), "last_check": d.get("last_check"),
            "last_result": d.get("last_result", ""), "next_check": next_check().timestamp() if d.get("out") else None,
            "model": config.CLAUDE_MODEL, "local": config.LOCAL_MODEL}


def watch():
    """Server thread: at each check hour, if Claude is off, try it again."""
    while True:
        time.sleep(max(1.0, (next_check() - dt.datetime.now()).total_seconds()))
        if out():
            try:
                check()
            except Exception as e:
                sys.stderr.write(f"Claude check failed: {e}\n")
        time.sleep(61)
