"""Runs tools/stress_bank.py's questions through the running server (127.0.0.1:8600) and saves each answer.

    .venv\\Scripts\\python.exe -m tools.stress_run [id-prefix ...]

No chat id is sent, so nothing lands in anyone's chat history. Results: data/stress/answers.jsonl (one line per
case; a re-run replaces the cases it ran). Then tools/stress_check.py grades them.
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.stress_bank import bank

OUT = Path(__file__).resolve().parents[1] / "data" / "stress" / "answers.jsonl"
URL = "http://127.0.0.1:8600/api/chat"


def ask(case: dict) -> dict:
    body = {"question": case["q"], "history": case.get("history") or [], "program": case["program"],
            "section": case["section"], "mode_program": bool(case["program"]), "mode_scenario": case["mode"] == "deal"}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    text, sources, choices, statuses, t0 = "", [], [], [], time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            for line in r:
                ev = json.loads(line)
                text = ev["replace"] if "replace" in ev else text + ev.get("text", "")
                sources = ev.get("sources", sources)
                choices = ev.get("choices", choices)
                if "status" in ev:
                    statuses.append(ev["status"])
                if "error" in ev:
                    text += f"\n[ERROR] {ev['error']}"
    except Exception as e:
        text += f"\n[ERROR] {e}"
    return {**case, "answer": text, "sources": sources, "choices": choices, "statuses": statuses,
            "secs": round(time.time() - t0, 1)}


def main(prefixes: list[str]):
    cases = [c for c in bank() if not prefixes or any(c["id"].startswith(p) for p in prefixes)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            old[r["id"]] = r
    done = 0
    with ThreadPoolExecutor(4) as pool:
        for res in pool.map(ask, cases):
            old[res["id"]] = res
            done += 1
            print(f"{done}/{len(cases)} {res['id']} {res['secs']}s", flush=True)
            if done % 10 == 0 or done == len(cases):
                OUT.write_text("\n".join(json.dumps(r) for r in old.values()) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
