"""Grades data/stress/answers.jsonl (from tools/stress_run.py). Read only: never touches the index or the server.

    .venv\\Scripts\\python.exe -m tools.stress_check            -> data/stress/report.md + problems on screen

For every answer: did it take the right route, read the deal right (FICO tier, loan bracket, column, cell, worked
out here from the approved grids on their own), cite real files and pages, and is every number and quote in a cited
sentence actually on the page it cites (the "is it making it up" check).
"""
import json
import re
import sys
from pathlib import Path

from guidebot import agency, library

ROOT = Path(__file__).resolve().parents[1]
ANS = ROOT / "data" / "stress" / "answers.jsonl"
REPORT = ROOT / "data" / "stress" / "report.md"

# ---------- source text ----------
_docs = {p.name: p for paths in library.programs().values() for p in paths}
_pages: dict = {}


def page_text(file: str, page: int) -> str:
    if file in _docs:
        if file not in _pages:
            d = library.load(_docs[file])
            _pages[file] = {p["page"]: library.render_page(file, p) for p in d["pages"]}
        return _pages[file].get(page, "")
    return agency.page_text(file, page)


def page_count(file: str) -> int:
    if file in _docs:
        page_text(file, 1)
        return len(_pages[file])
    path = next((p for p in agency.files() if p.name == file), None)
    return len(agency.pages(path)) if path else 0


# ---------- citations, the way ui/md.js reads them ----------
CITE = re.compile(r"\(([^()]*\.pdf[^()]*)\)", re.I)


def citations(text: str) -> list[dict]:
    out = []
    for m in CITE.finditer(text):
        last = ""
        for part in re.split(r";\s*", m[1]):
            f = re.search(r"([^,;():]+?\.pdf)", part, re.I)   # same as ui/md.js
            file = re.sub(r"^(also|see|and|plus|per|via)\s+", "", f[1].strip(), flags=re.I) if f else last
            last = file
            pg = re.search(r"p(?:age|p)?\.?\s*(\d+)", part, re.I)
            out.append({"file": file, "page": int(pg[1]) if pg else None, "mark": "mark " in part,
                        "at": m.start(), "end": m.end()})
    return out


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"(\d),(\d{3})", r"\1\2", s)
    s = re.sub(r"(\d),(\d{3})", r"\1\2", s)
    return re.sub(r"\s+", " ", s)


NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
SKIP_NUM = re.compile(r"^(1|2|3|4|5|6|7|8|9|10|0)$")   # list numbers, unit counts and such say too little alone


def _loose(s: str) -> str:
    return re.sub(r"[^a-z0-9%$.]+", " ", _norm(s)).strip()


def grounding(answer: str) -> list[str]:
    """Numbers and quotes on a line that carries chips, missing from every page those chips cite (and the pages
    either side, since the viewer looks there too). Python's placed matrix rows are skipped."""
    probs = []
    for line in answer.split("\n"):
        cites = [c for c in citations(line) if c["page"] and c["file"] and not c["mark"]]
        if not cites:
            continue
        pages = {(c["file"], p) for c in cites for p in (c["page"] - 1, c["page"], c["page"] + 1)}
        text = _loose(" ".join(page_text(f, p) for f, p in pages))
        if not text:
            continue
        said = re.sub(r"\*\*|`", "", CITE.sub(" ", line))
        where = ", ".join(sorted({f"{c['file']} p. {c['page']}" for c in cites}))
        for q in re.findall(r"[\"“]([^\"”]{12,200})[\"”]", said):
            if _loose(q).strip(" .") not in text:
                probs.append(f'quote not on {where}: "{q[:80]}"')
        for n in NUM.findall(said):
            core = _norm(n).strip("$%,.")
            if not core or SKIP_NUM.match(core) or len(core) < 2 or core == "1099":
                continue
            if core not in text:
                probs.append(f"number {n} not on {where}: …{said.strip()[:110]}")
    return probs


# ---------- routes ----------
def route_of(r: dict) -> str:
    a = r["answer"]
    if r.get("choices"):
        return "pick"
    if re.search(r"To (pull the exact number|compare programs) I need|I need a few details|need the (loan amount|fico)", a, re.I):
        return "missing"
    if re.match(r"\s*(\*\*(Max LTV/CLTV|Eligible programs|Not eligible|No final LTV|Could not check)|\*Matrix:|"
                r"To compare|\*\*\S+\*\* hasn't been verified)", a) or re.search(r"Can't price this|no LTV table", a):
        return "deal"
    if False and re.search(r"Max LTV/CLTV|Eligible programs|Not eligible on th|Could not check|no LTV table|"
                 r"haven't been verified|Not eligible\b|\*\*Matrix|Can't price this|\*Matrix:", a):
        return "deal"
    return "guide"


# ---------- independent grid reference for NonQM deals ----------
def _range(s: str):
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", s)]
    if s.strip()[:1] in "<≤":
        return 0, nums[0]   # $1,000,000 itself: the next row starts at $1,000,001 (open question, flagged in answers)
    return (nums[0], nums[1]) if len(nums) >= 2 else (nums[0], nums[0])


def reference_cell(program: str, d: dict):
    """The cell a deal lands on, read straight from the approved grid: loan in the bracket, FICO tier at or below,
    column by occupancy + purpose (or DSCR band + purpose). None if the grid doesn't cover it."""
    occ = {"Primary Residence": "primary", "Second Home": "second", "Investment Property": "invest"}[d["occ"]]
    pur = {"Purchase": "purch", "Rate/Term Refinance": "rate", "Cash Out Refinance": "cash"}[d["purpose"]]
    for path in library.programs().get(program, []):
        if "Units+" in path.name:   # the 5-8 unit sheet; the bank's deals are 1-4 units
            continue
        for p in library.load(path)["pages"]:
            for m in p["matrices"]:
                cols = [str(c) for c in m.columns]
                if "FICO" not in cols or "Loan Amount" not in cols:
                    continue
                if "dscr" in d:
                    band = "1.00" if d["dscr"] >= 1.0 else ".75"
                    title = (m.attrs.get("title") or "").lower()
                    want = [c for c in cols if pur in c.lower().replace(" and ", " & ")
                            and (band in c or (band == "1.00" and "1.00" in title) or (band == ".75" and ".75" in title))]
                else:
                    want = [c for c in cols if occ in c.lower() and pur in c.lower()]
                if not want:
                    continue
                rows = [r for _, r in m.iterrows()]
                fit = [r for r in rows if _range(str(r["Loan Amount"]))[0] <= d["loan"] <= _range(str(r["Loan Amount"]))[1]]
                tiers = sorted({int(re.sub(r"\D", "", str(r["FICO"])) or 0) for r in fit}, reverse=True)
                tier = next((t for t in tiers if t <= d["fico"]), None)
                if tier is None:
                    return {"file": path.name, "cell": None, "why": "no FICO tier / loan bracket"}
                row = next(r for r in fit if int(re.sub(r"\D", "", str(r["FICO"])) or 0) == tier)
                return {"file": path.name, "cell": str(row[want[0]]), "tier": tier, "col": want[0],
                        "bracket": str(row["Loan Amount"])}
    return None


def _pct(s) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(s or ""))
    return float(m[1]) if m else None


def check_deal(r: dict) -> list[str]:
    a, e, probs = r["answer"], r["expect"], []
    d = e.get("deal")
    if "ltv" in e:
        head = re.search(r"Max LTV/CLTV: (\d+(?:\.\d+)?)%", a)
        if not head or float(head[1]) != e["ltv"]:
            probs.append(f"expected {e['ltv']}%, got {head[1] + '%' if head else 'no number'}")
    if e.get("eligible") is False and re.search(r"Max LTV/CLTV: \d", a):
        probs.append("should be ineligible, gave a number")
    if d:
        echo = re.search(r"\*FICO (\d+), \$([\d,]+), ([^,]+), ([^,]+), ([^*]+)\*", a)   # Conventional/FHA/VA echo
        if echo:
            if int(echo[1]) != d["fico"]:
                probs.append(f"read FICO {echo[1]}, said {d['fico']}")
            if int(echo[2].replace(",", "")) != d["loan"]:
                probs.append(f"read loan ${echo[2]}, said ${d['loan']:,}")
            if echo[3] != d["purpose"]:
                probs.append(f"read purpose {echo[3]}, said {d['purpose']}")
            if echo[4] != d["occ"]:
                probs.append(f"read occupancy {echo[4]}, said {d['occ']}")
            if d.get("units") and not echo[5].startswith(str(d["units"])):
                probs.append(f"read units {echo[5]}, said {d['units']}")
        if not r["program"].startswith("Conventional"):
            ref = reference_cell(r["program"], d)
            got = re.search(r"Matrix cell: \*\*([^*]+)\*\*", a)
            yours = re.search(r"Loan Amount: [^\n]*\(yours: (\d+)\)", a)
            tier = re.search(r"FICO: (\d+) row", a)
            col = re.search(r"Column: ([^\n]+)", a)
            if yours and int(yours[1]) != d["loan"]:
                probs.append(f"read loan {yours[1]}, said {d['loan']}")
            if ref and ref.get("cell") is not None:
                if not got:
                    if re.search(r"Max LTV/CLTV: \d", a):
                        probs.append(f"no matrix cell shown (expected {ref['cell']})")
                    elif _pct(ref["cell"]) is not None:
                        probs.append(f"no number, but grid has {ref['cell']} ({ref['col']}, {ref['bracket']}, {ref['tier']})")
                elif _pct(got[1]) != _pct(ref["cell"]):
                    probs.append(f"cell {got[1]} but grid says {ref['cell']} ({ref['col']}, {ref['bracket']}, FICO {ref['tier']})")
                if tier and int(tier[1]) != ref["tier"]:
                    probs.append(f"FICO row {tier[1]}, expected {ref['tier']}")
                if col and ref["col"] not in col[1]:
                    probs.append(f"column {col[1].strip()}, expected {ref['col']}")
            elif ref and re.search(r"Max LTV/CLTV: \d", a):
                probs.append(f"gave a number but the grid has no row for it ({ref.get('why')})")
    return probs


def check(r: dict) -> list[str]:
    a, e, probs = r["answer"], r["expect"], []
    if "[ERROR]" in a:
        probs.append("server error: " + a[a.find("[ERROR]"):][:160])
    got = route_of(r)
    if e.get("route") and got != e["route"]:
        probs.append(f"route {got}, expected {e['route']}")
    for rx in e.get("must", []):
        if not re.search(rx, a):
            probs.append(f"missing /{rx}/")
    for rx in e.get("must_not", []):
        if re.search(rx, a):
            probs.append(f"has /{rx}/")
    cites = citations(a)
    for c in cites:
        n = page_count(c["file"])
        if not n:
            probs.append(f"cites a file it can't open: {c['file']}")
        elif c["page"] and not 1 <= c["page"] <= n:
            probs.append(f"cites p. {c['page']} of {c['file']} ({n} pages)")
    if got == "guide":
        if not cites and len(a) > 200:
            probs.append("guideline answer with no citations")
        probs += grounding(a)
        q_has_deal = re.search(r"\b\d{3}\s*fico|\$\s*\d", r["q"], re.I)
        if q_has_deal and re.search(r"max(imum)? (LTV|CLTV)[^.\n]{0,40}\d{2,3}(\.\d+)?%", a, re.I):
            probs.append("guideline answer states an LTV for a specific deal")
    if got == "deal":
        probs += check_deal(r)
        if re.search(r"Max LTV/CLTV: \d", a) and not any(c["page"] for c in cites) and "Matrix:" not in a:
            probs.append("deal number with no source chip")
        if re.search(r"Max LTV/CLTV: \d", a) and not any(c["mark"] for c in cites):
            probs.append("matrix row isn't a chip (can't open it highlighted)")
    return probs


def main():
    rows = [json.loads(l) for l in ANS.read_text(encoding="utf-8").splitlines() if l.strip()]
    out, bad = [], 0
    for r in rows:
        probs = check(r)
        bad += bool(probs)
        out.append((r, probs))
    lines = [f"# Stress run: {len(rows)} answers, {bad} with problems", ""]
    for r, probs in out:
        if probs:
            lines += [f"## {r['id']} ({r['section']}{' / ' + r['program'] if r['program'] else ''}, {r['mode']})",
                      f"**Q:** {r['q']}", ""] + [f"- {p}" for p in probs] + ["", "<details><summary>answer</summary>",
                                                                             "", r["answer"], "", "</details>", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(lines[0])
    for r, probs in out:
        for p in probs:
            print(f"{r['id']}: {p}")


if __name__ == "__main__":
    main()
