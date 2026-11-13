"""Race local models as Claude's backup on the stress bank's guideline questions (no Claude, no credits).

    .venv\\Scripts\\python.exe -m tools.backup_race run gemma4:26b 65536     -> data/stress/backup-<model>-<ctx>.jsonl
    .venv\\Scripts\\python.exe -m tools.backup_race grade                    -> data/stress/backup.md

Runs chat.respond in this process (the server's own code path, read only; nothing is saved to anyone's chats) with
the model and context size given, so section-wide questions get their whole prompt (20K-41K tokens) instead of
being cut at 16K. Graded three ways: answer keys (tools/backup_keys.py), grounding (stress_check: citations open,
numbers and quotes on the cited pages, right route) and seconds per answer.
"""
import json
import statistics
import sys
import time
from pathlib import Path

from guidebot import chat, config
from tools.backup_keys import KEYS, misses
from tools.stress_bank import bank
from tools.stress_check import check

STRESS = Path(__file__).resolve().parents[1] / "data" / "stress"
ORDER = ("nq-guide", "conv-guide", "fha-guide", "va-guide")   # by section, so Ollama reuses the cached sheets


def _file(model: str, ctx: int) -> Path:
    return STRESS / f"backup-{model.replace(':', '_')}-{ctx}.jsonl"


def ask(case: dict, model: str) -> dict:
    text, sources, choices, t0 = "", [], [], time.time()
    try:
        for ev in chat.respond(case["q"], [], case["program"], bool(case["program"]), case["mode"] == "deal",
                               case["section"], model):
            text = ev["replace"] if "replace" in ev else text + ev.get("text", "")
            sources = ev.get("sources", sources)
            choices = ev.get("choices", choices)
            if "error" in ev:
                text += f"\n[ERROR] {ev['error']}"
    except Exception as e:
        text += f"\n[ERROR] {type(e).__name__}: {e}"
    return {**case, "answer": text, "sources": sources, "choices": choices, "secs": round(time.time() - t0, 1)}


def cases(only: int = 0) -> list[dict]:
    out = sorted((c for c in bank() if c["id"].rsplit("-", 1)[0] in ORDER),
                 key=lambda c: (ORDER.index(c["id"].rsplit("-", 1)[0]), c["id"]))
    if only:   # an even slice of the keyed questions across all four sections
        keyed = [c for c in out if c["id"] in KEYS]
        out = keyed[::max(1, len(keyed) // only)][:only]
    return out


def run(model: str, ctx: int, only: int = 0):
    config.CHAT_OPTIONS["num_ctx"] = ctx
    cases_ = cases(only)
    out = _file(model, ctx)
    done = {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines() if l} if out.exists() else set()
    with out.open("a", encoding="utf-8") as f:
        for i, c in enumerate(cases_, 1):
            if c["id"] in done:
                continue
            r = ask(c, model)
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(f"{i}/{len(cases_)} {c['id']} {r['secs']}s {'KEY MISS' if misses(c['id'], r['answer']) else ''}",
                  flush=True)


def grade(only: int = 0):
    ids = {c["id"] for c in cases(only)}
    lines = ["# Local backup race", "", "| Model (context) | Keys right | Grounding problems | Errors | Median s | Answers |",
             "|---|---|---|---|---|---|"]
    detail = []
    for path in sorted(STRESS.glob("backup-*.jsonl")):
        rows = [r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if r["id"] in ids]
        if len(rows) < len(ids):
            continue   # still running
        keyed = [r for r in rows if r["id"] in KEYS]
        wrong = [(r, misses(r["id"], r["answer"])) for r in keyed]
        wrong = [(r, m) for r, m in wrong if m]
        probs = [(r, p) for r in rows for p in check(r) if not p.startswith(("missing /", "server error"))]
        errors = sum("[ERROR]" in r["answer"] for r in rows)
        name = path.stem.removeprefix("backup-")
        lines.append(f"| {name} | {len(keyed) - len(wrong)}/{len(keyed)} | {len(probs)} | {errors} | "
                     f"{statistics.median(r['secs'] for r in rows):.0f} | {len(rows)} |")
        detail += ["", f"## {name}", ""] + [f"- {r['id']}: {'; '.join(m)}" for r, m in wrong] + \
                  [f"- {r['id']} (grounding): {p[:160]}" for r, p in probs]
    (STRESS / "backup.md").write_text("\n".join(lines + detail), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1] == "run":
        run(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 0)
    else:
        grade(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
