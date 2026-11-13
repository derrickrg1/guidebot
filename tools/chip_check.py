"""Clicks every citation chip in a stress run's answers, in a real browser, with the viewer's own code: headless
Chromium (Playwright) opens the running AskNick page, draws each answer with md.js, and for each chip runs
viewer.js's claimFor + findMarks on the cited page (one page either side, like the viewer). A chip whose row and
cell Python placed ("mark ...") counts as marked. Read only; needs the server on 127.0.0.1:8600.

    .venv\\Scripts\\python.exe -m tools.chip_check [run]      (run = data/stress/<run>.jsonl, default answers)
    -> data/stress/chips-<run>.md
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

STRESS = Path(__file__).resolve().parents[1] / "data" / "stress"
URL = "http://127.0.0.1:8600/"

PROBE = r"""
async (answers) => {
  const lib = await loadPdfjs(), docs = {};
  const out = { chips: 0, marked: 0, placed: 0, missed: [], errors: [] };
  for (const a of answers) {
    const box = document.createElement("div");
    box.className = "msg bot";
    box.innerHTML = `<div class="md">${md(a.answer)}</div>`;
    document.getElementById("log").appendChild(box);
    for (const link of box.querySelectorAll('a[href*="/pdf/"]')) {
      out.chips++;
      if (link.dataset.mark) { out.placed++; out.marked++; continue; }
      const u = new URL(link.href, location.href);
      const file = decodeURIComponent(u.pathname.split("/pdf/")[1] || "");
      const pg = +((u.hash.match(/page=(\d+)/) || [])[1] || 1);
      try {
        docs[file] ||= await lib.getDocument(`${API}/pdf/${encodeURIComponent(file)}`).promise;
        const d = docs[file], claim = claimFor(link);
        let hit = false;
        for (const n of [pg, pg + 1, pg - 1]) {
          if (n < 1 || n > d.numPages) continue;
          if ((await findMarks(await d.getPage(n), claim)).length) { hit = true; break; }
        }
        if (hit) out.marked++;
        else out.missed.push(`${a.id}: ${file} p. ${pg}: "${claim.slice(0, 90)}"`);
      } catch (e) { out.errors.push(`${a.id}: ${file}: ${e && e.message}`); }
    }
    box.remove();
  }
  return out;
}
"""


def main(run: str = "answers"):
    rows = [json.loads(line) for line in (STRESS / f"{run}.jsonl").read_text(encoding="utf-8").splitlines() if line]
    rows = [{"id": r["id"], "answer": r["answer"]} for r in rows if ".pdf" in r["answer"] and "[ERROR]" not in r["answer"]]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL)
        page.wait_for_function("typeof md === 'function' && typeof findMarks === 'function'")
        res = {"chips": 0, "marked": 0, "placed": 0, "missed": [], "errors": []}
        for i in range(0, len(rows), 20):   # in batches, so one slow handbook doesn't time out the lot
            part = page.evaluate(PROBE, rows[i:i + 20])
            for k in ("chips", "marked", "placed"):
                res[k] += part[k]
            res["missed"] += part["missed"]
            res["errors"] += part["errors"]
            print(f"{min(i + 20, len(rows))}/{len(rows)} answers: {res['marked']}/{res['chips']} chips marked", flush=True)
        browser.close()
    pct = 100 * res["marked"] / max(res["chips"], 1)
    lines = [f"# Chip check: {run}", "", f"{res['marked']} of {res['chips']} chips land on a marked line ({pct:.1f}%); "
             f"{res['placed']} placed by Python (matrix rows), {len(res['errors'])} errors.", "", "## Not marked", ""]
    lines += [f"- {m}" for m in res["missed"]] + ["", "## Errors", ""] + [f"- {e}" for e in res["errors"]]
    (STRESS / f"chips-{run}.md").write_text("\n".join(lines), encoding="utf-8")
    print(lines[2])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(*sys.argv[1:2])
