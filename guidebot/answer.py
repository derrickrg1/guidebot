"""Two ways to answer:
1. Scenario (max LTV for a specific deal): the AI only maps the deal to the matrix and finds overlays.
   Python looks up the cell and does the math. Every overlay quote gets checked against the source.
2. Everything else: the AI reads the program's COMPLETE guidelines, not fragments."""
import re
from typing import Literal

from pydantic import BaseModel

from guidebot import overlay_rules
from guidebot.llm import ask_json
from guidebot.matrix_lookup import apply_adjustments, dimension_columns, header_problems, lookup, value_columns

RULES = """Rules:
- Read everything before answering. Don't stop at the first thing that looks relevant.
- Answer only from the documents. No outside knowledge, no guessing.
- Give the complete answer: every condition, overlay, footnote (*), cap, reduction, and exception that applies.
  If the answer depends on details the user didn't give (occupancy, purpose, loan amount, FICO, property type),
  lay out each case briefly.
- Cite every fact using the filename and page from the section header above it, formatted as (filename, p. X). The header looks like [§5.3 Title | NonQM-Underwriting-Guidelines.pdf | p. 35-39] — copy the filename exactly from there.
- Copy numbers exactly as written.
- Never infer from a negative statement. "No additional X required for Y" means only that exact case — do not extend it to mean X is not required generally or for similar cases.
- If the document says "per program guidelines," "refer to the program matrix," or gives no specific value, say so and note the requirement varies by program.
- If the answer is not in the provided sections, say you couldn't find it in the shared guidelines — do not infer or fill the gap.
- Lead with a direct one-sentence answer (yes/no + the key condition if there is one).
- Then use bullet points for requirements, restrictions, and exceptions — never prose paragraphs for lists.
- Bold key terms, numbers, and thresholds (e.g. **5%**, **gift letter**, **not allowed for reserves**).
- Group related bullets under a short bold label when there are two or more distinct categories (e.g. **Requirements:**, **Restrictions:**).
- Keep it tight. No filler sentences."""

FULL_DOC_SYSTEM = (
    "You are a mortgage guideline assistant for loan set-up and underwriting staff.\n"
    "You have the COMPLETE guidelines for the selected program below.\n\n"
    + RULES
)

FULL_SECTION_SYSTEM = (
    "You are a mortgage guideline assistant for loan set-up and underwriting staff.\n"
    "You have the COMPLETE text of the relevant guideline sections below -- not fragments, full sections.\n\n"
    + RULES
    + "\n\nIf the shared guidelines say a requirement is 'per program guidelines' or defer to a program matrix, say so"
    " and note that it varies by program -- do not ask the user to select a program."
)


def full_section_messages(question: str, sections_text: str, history: list[dict], filename: str = "") -> list[dict]:
    cite_note = (
        f"\nIMPORTANT: The source document is '{filename}'. Every citation must use this exact filename"
        f" and include the subsection number when the fact comes from a numbered subsection visible in the text"
        f" (e.g. if the text shows '5.3.3 Non-Sufficient Funds', cite it as ({filename}, section 5.3.3, p. 37))."
        f" Never write '(file, p. X)'.\n"
    ) if filename else ""
    return (
        [{"role": "system", "content": FULL_SECTION_SYSTEM + cite_note + "\n\nGUIDELINE SECTIONS:\n\n" + sections_text}]
        + history[-6:]
        + [{"role": "user", "content": question}]
    )

SEARCH_SYSTEM = (
    "You are a mortgage guideline assistant for loan set-up and underwriting staff.\n"
    "You have the most relevant sections from several programs guidelines below.\n\n"
    "IMPORTANT: Do NOT quote, estimate, or reason about maximum LTV or CLTV percentages for any specific\n"
    "loan scenario. LTV lookups require an exact matrix read that only works when a single program is selected.\n"
    "If someone asks for max LTV or whether a specific deal is eligible, tell them:\n"
    '"Pick a specific program in the sidebar. I cannot look up LTV numbers from search results."\n\n'
    "Synthesis rules:\n"
    "- Read EVERY section provided before you start writing. The complete answer often requires combining two or three sections.\n"
    "- Programs can differ -- never mix one program's rules into another's answer. Name the program for every fact.\n"
    "- If different programs have different requirements for the same question, state each one separately.\n"
    "- If a section gives a partial answer and you can tell the full rule likely lives elsewhere, flag it.\n"
    "- Never stop at the first section that mentions the topic. The first hit is often the introduction; the specific rule is usually in a later section.\n\n"
    + RULES
)


NO_LTV_RULE = (
    "\n\nIMPORTANT: These documents haven't been verified yet. Do NOT state any LTV, CLTV, or max loan-to-value"
    " percentage, even if it appears in the text. If asked, say the program's documents need to be approved on"
    " the Verify Documents page before LTV numbers can be quoted. Everything else you may answer."
)


def full_doc_messages(question: str, docs_text: str, history: list[dict], ltv_ok: bool = True) -> list[dict]:
    """ltv_ok=False when any of the program's docs is unverified: the model must not quote LTVs."""
    system = FULL_DOC_SYSTEM + ("" if ltv_ok else NO_LTV_RULE)
    return (
        [{"role": "system", "content": system + "\n\nGUIDELINES:\n\n" + docs_text}]
        + history[-6:]
        + [{"role": "user", "content": question}]
    )


def search_messages(question: str, hits: list[dict], history: list[dict]) -> list[dict]:
    pages = "\n\n".join(h["text"] for h in hits)
    return (
        [{"role": "system", "content": SEARCH_SYSTEM + "\n\nPAGES:\n\n" + pages}]
        + history[-6:]
        + [{"role": "user", "content": question}]
    )


# ---------- scenario mode ----------

# ---- cross-program comparison ----

class RawScenario(BaseModel):
    fico: int | None = None
    loan_amount: int | None = None
    occupancy: str | None = None   # Primary Residence / Second Home / Investment Property
    purpose: str | None = None     # Purchase / Rate/Term Refinance / Cash Out Refinance
    property_type: str | None = None  # SFR / Condo / 2-4 Unit / Townhome
    units: int | None = None
    missing: list[str] = []

RAW_SCENARIO_SYSTEM = """Extract the loan scenario details from the user's message.
Return null for any field not mentioned.
occupancy: "Primary Residence", "Second Home", or "Investment Property"
purpose: "Purchase", "Rate/Term Refinance", or "Cash Out Refinance"
property_type: "SFR", "Condo", "2-4 Unit", "Townhome", or "Rural"
List any key fields not provided in 'missing' (fico, loan_amount, occupancy, purpose, property_type)."""


# Python fallbacks for the details the model sometimes drops ("$650,000 loan, 720 FICO" came back as missing).
_FICO = re.compile(r"\b(\d{3})\s*(?:fico|credit|score|mid)\b|\b(?:fico|score)\s*(?:of|is|:)?\s*(\d{3})\b", re.I)
_AMOUNT = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*([km]\b)?|\b(\d+(?:\.\d+)?)\s*([km])\b", re.I)
_OCCUPANCY = {"Investment Property": r"invest|rental|non[- ]?owner|\bnoo\b",
              "Second Home": r"second home|2nd home|vacation home",
              "Primary Residence": r"primary|owner[- ]occupied|\boo\b"}
_PURPOSE = {"Cash Out Refinance": r"cash[- ]?out|\bc/o\b",   # not bare "co": that's co-borrower
            "Rate/Term Refinance": r"rate[ /&-]*(and )?term|\br/?t\b|no cash|refi",
            "Purchase": r"purchas|buying|\bbuy\b"}
REQUIRED = ("fico", "loan_amount", "occupancy", "purpose")


def _amount(text: str) -> int | None:
    for m in _AMOUNT.finditer(text):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        value = float(num.replace(",", "")) * {"k": 1e3, "m": 1e6}.get((unit or "").lower(), 1)
        if value >= 10000:
            return int(value)
    return None


def fill_scenario(raw: RawScenario, text: str) -> RawScenario:
    """Fill what the model missed from the user's own words, then work out what's really missing in Python."""
    raw = raw.model_copy()
    if raw.fico is None and (m := _FICO.search(text)):
        raw.fico = int(m.group(1) or m.group(2))
    if raw.loan_amount is None:
        raw.loan_amount = _amount(text)
    for field, table in (("occupancy", _OCCUPANCY), ("purpose", _PURPOSE)):
        if not getattr(raw, field):
            setattr(raw, field, next((k for k, rx in table.items() if re.search(rx, text, re.I)), None))
    raw.missing = [f for f in REQUIRED if getattr(raw, f) in (None, "")]
    return raw


def extract_scenario(question: str, history: list[dict], model: str) -> RawScenario | None:
    convo = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-4:])
    said = " ".join([*(m["content"] for m in history[-4:] if m["role"] == "user"), question])
    try:
        raw = ask_json(RAW_SCENARIO_SYSTEM, f"Earlier messages:\n{convo}\n\nQuestion: {question}", RawScenario, model)
    except Exception:
        raw = RawScenario()
    return fill_scenario(raw, said)


def compare_programs(
    question: str,
    history: list[dict],
    program_data: list[dict],  # [{program, mats, docs_text, rules}]
    model: str,
) -> dict:
    """
    Run a scenario across multiple programs.
    Returns {missing, results: [{program, eligible, max_ltv, cell, trace, error}]}
    Results sorted: eligible first by LTV desc, then ineligible, then errors.
    Eligible programs get the same checked overlay step as single-program mode, so max_ltv is after overlays.
    """
    raw = extract_scenario(question, history, model)
    if not raw:
        return {"missing": ["scenario details"], "results": []}
    if raw.missing:
        return {"missing": raw.missing, "results": []}

    convo = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-4:])
    results = []
    for pd in program_data:
        prog = pd["program"]
        mats = [m for m in pd["mats"] if m["status"] == "verified"]
        if not mats:
            if pd["mats"]:
                results.append({"program": prog, "eligible": None, "error": "Matrix not verified"})
            continue
        blocked = occupancy_block(raw.occupancy, mats, pd.get("docs_text", ""))
        if blocked:
            results.append({"program": prog, "eligible": False, "error": blocked})
            continue
        try:
            crit = ask_json(
                CRITERIA_SYSTEM,
                f"Matrices:\n{describe_matrices(mats)}\n\nEarlier messages:\n{convo}\n\nQuestion: {question}",
                Criteria, model,
            )
        except Exception:
            results.append({"program": prog, "eligible": None, "error": "Could not read matrix"})
            continue

        if not crit.is_scenario:
            continue
        if 0 <= crit.matrix < len(mats) and header_problems(mats[crit.matrix]["df"]):
            results.append({"program": prog, "eligible": None, "error": "Matrix headers need re-verifying"})
            continue
        if not 0 <= crit.matrix < len(mats):
            results.append({"program": prog, "eligible": None, "error": "Matrix index out of range"})
            continue
        if crit.missing:
            # The user already gave the basics (raw.missing is empty), so this is a mapping problem, not missing info
            mapped = python_mapping(mats[crit.matrix]["df"], raw)
            if not mapped:
                results.append({"program": prog, "eligible": None,
                                "error": f"Couldn't map the deal onto this matrix (AI asked for: {', '.join(crit.missing)}). "
                                         "Pick this program for a direct lookup."})
                continue
            crit.column = mapped[0]
            crit.dims = [DimValue(name=k, value=v) for k, v in mapped[1].items()]
            crit.missing = []
        if mats[crit.matrix].get("reference", {}).get("status") == "mismatch":
            results.append({"program": prog, "eligible": None, "error": "Matrix disagrees with the Revo site matrix"})
            continue

        res = lookup(mats[crit.matrix]["df"], {d.name: d.value for d in crit.dims}, crit.column)
        if not res["ok"]:
            results.append({"program": prog, "eligible": False, "cell": res.get("cell", ""), "trace": res.get("trace", []), "error": res.get("error", "")})
        elif not res["eligible"]:
            results.append({"program": prog, "eligible": False, "cell": res.get("cell", "N/A"), "trace": res.get("trace", [])})
        else:
            row = {"program": prog, "eligible": True, "max_ltv": res["ltv"], "cell": res["cell"], "trace": res["trace"]}
            try:
                kept, rejected, _, calc = checked_overlays(question, history, crit, res, pd.get("docs_text", ""), model,
                                                           pd.get("rules"))
                generic = {"cap", "reduction", "footnote", "overlay", "ineligible", "property type overlay"}
                label = lambda a: (f"{a.kind} {a.value:g}%: {a.quote[:50]}" if a.reason.strip().lower() in generic
                                   else a.reason)
                row.update(overlays=[label(a) for a in kept], skipped=len(rejected), stacked=calc.get("stacked"))
                if not calc["eligible"]:
                    row.update(eligible=False, error="; ".join(a.reason for a in kept) or "ineligible after overlays")
                else:
                    row["max_ltv"] = calc["final"]
            except Exception as e:
                row.update(eligible=None, error=f"Overlay check failed ({e}). Pick this program for a direct lookup.")
            results.append(row)

    results.sort(key=lambda r: (r.get("eligible") is not True, -(r.get("max_ltv") or 0)))
    return {"missing": [], "results": results}

class DimValue(BaseModel):
    name: str
    value: str


class Criteria(BaseModel):
    is_scenario: bool
    matrix: int
    dims: list[DimValue]
    column: str
    missing: list[str]


class Adjustment(BaseModel):
    reason: str
    trigger: str  # the user's own words that make this apply ("investment", "2 unit", "condo")
    kind: Literal["cap", "reduction", "ineligible"]
    value: float
    file: str
    page: int
    quote: str


class Adjustments(BaseModel):
    adjustments: list[Adjustment]
    notes: list[str]


class CitedSections(BaseModel):
    cited: list[str]  # section numbers actually used, e.g. ["5.3.3", "5.3.2"]


CITATION_SYSTEM = """You are given an answer that was generated from guideline sections.
Return the section numbers (e.g. "5.3.3", "5.3.2") that were directly used to generate that answer.
Only include a section if its content is clearly reflected in the answer.
Return an empty list if none match."""


def filter_to_cited(answer_text: str, hits: list[dict], model: str) -> list[dict]:
    """Return only the hits whose sections were actually cited in the answer."""
    if not hits:
        return hits
    section_hits = [h for h in hits if h.get("section")]
    if not section_hits:
        return hits[:3]  # no section metadata, just show top 3

    sections_desc = "\n".join(
        f"- {h['section']}: {h.get('section_title', '')}" for h in section_hits
    )
    try:
        result = ask_json(
            CITATION_SYSTEM,
            f"Answer:\n{answer_text[:2000]}\n\nAvailable sections:\n{sections_desc}",
            CitedSections,
            model,
        )
        cited = set(result.cited)
        filtered = [h for h in hits if h.get("section") in cited]
        return filtered if filtered else hits[:3]
    except Exception:
        return hits[:3]


CRITERIA_SYSTEM = """You map a loan scenario onto an LTV matrix. You do NOT look up or state any LTV yourself.

is_scenario = true only if the user wants the max LTV/CLTV (or whether a specific deal is eligible) for a deal
with at least some specifics (loan amount, FICO, occupancy, purpose...). General questions = false.

If true:
- matrix: index of the matrix to use from the list.
- dims: one entry per matrix dimension with the user's value. Numbers as plain digits (1200000, 705).
  FICO: use the exact number the user gave. Never round up to the nearest tier. 679 stays 679.
- column: copy one column name EXACTLY from that matrix's column list.
- missing: dimension names or column info (occupancy, purpose) the user didn't give. Never guess them.
Use earlier messages for context on follow-ups like "what if it's a second home"."""

ADJ_SYSTEM = """You check a loan scenario against the program guidelines for anything that changes the max LTV.

The base max LTV already came from the matrix. Your job: list every overlay that applies to THIS scenario,
based only on facts the user actually stated.
- cap: a max LTV for a property type, product, or borrower type (condo, 2-4 units, rural, interest only,
  first-time buyer, Texas 50(a)(6), etc.). value = the cap for this deal's purpose/occupancy.
  If an overlay has a footnote (like * for investment), apply what the footnote says as a separate item.
- reduction: "X% reduction from max LTV" items. value = X.
- ineligible: the deal isn't allowed at all (value = 0).
- trigger: copy the words from the user's scenario that make it apply ("investment", "condo", "2 unit").
  If you can't point to the user's own words, it doesn't apply. Put it in notes instead.
- file/page: where it says so. quote: copy the supporting words EXACTLY from the document, including the % value.
Only LTV percentages go in adjustments. Reserves (months), escrows, seasoning, DTI and loan amount limits are
never adjustments, even if they mention LTV.
An overlay for a different occupancy, purpose or property type than the user's does not apply
(an investment property overlay never applies to a primary residence).
Do not include overlays that depend on facts the user didn't state (declining market, non-occupant co-borrower,
credit events, location). Put those in notes as short "If ..." lines with (file, p. X). Also put other
requirements that apply to this deal but don't change the LTV (reserves, escrows, seasoning, max loan amount)
in notes, with citations. Every note ends with (file, p. X). Notes never restate the base LTV, the math,
or why something does or doesn't apply."""

OCCUPANCY = {"investment": ("investment", "investor", "non owner", "rental", "noo"),
             "second home": ("second home", "2nd home", "vacation"),
             "primary": ("primary", "owner occupied", "owner occupant")}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9%$.]+", " ", s.lower()).strip()


def _occ_keys(text: str) -> set[str]:
    t = f" {_norm(text)} "
    return {occ for occ, words in OCCUPANCY.items() if any(f" {w}" in t for w in words)}


_OCC_LINE = re.compile(r"^(?:#+\s*)?Occupancy\s*[|:]\s*(.+)$", re.I | re.M)


def occupancy_block(occupancy: str | None, mats: list[dict], docs_text: str) -> str | None:
    """Matrices without occupancy columns (DSCR) can't say no to a primary residence. The program's own
    'Occupancy | ...' line can. Returns why the deal is ineligible, or None."""
    if not occupancy or any(_occ_keys(c) for m in mats for c in value_columns(m["df"])):
        return None
    line = _OCC_LINE.search(docs_text)
    allowed = _occ_keys(line.group(1)) if line else set()
    stated = _occ_keys(occupancy)
    if allowed and stated and not stated & allowed:
        return f"{occupancy} not allowed. Program says: Occupancy | {line.group(1).strip()[:120]}"
    return None


def _percents(s: str) -> set[float]:
    return {float(n) for n in re.findall(r"(\d+(?:\.\d+)?)\s*%", s)}


def check_adjustment(a, doc_norm: str, facts: str) -> str | None:
    """Python's gate on an overlay the AI suggested. Returns why it can't go in the math, or None if it can.
    facts = everything the user stated plus the matrix row/column it mapped to."""
    quote = _norm(a.quote)
    if not quote or quote not in doc_norm:
        return "quote not found word for word in the document"
    if not re.search(r"[a-z]{2,}", quote):
        return "the quote is just a number, not a guideline"
    if a.kind != "ineligible" and float(a.value) not in _percents(a.quote):
        return f"{a.value:g}% isn't in the quote"
    trig = _norm(a.trigger)
    if not trig or f" {trig} " not in f" {_norm(facts)} ":
        return "it depends on something you didn't say" + (f" ({a.trigger})" if a.trigger else "")
    said = f" {_norm(facts)} "
    stated = {occ for occ, words in OCCUPANCY.items() if any(f" {w}" in said for w in words)}
    named = {occ for occ, words in OCCUPANCY.items() if any(f" {w}" in f" {quote}" for w in words)}
    if named and stated and not named & stated:
        return f"it's for {', '.join(sorted(named))}, not this deal's occupancy"
    return None


def describe_matrices(mats: list[dict]) -> str:
    lines = []
    for i, m in enumerate(mats):
        df = m["df"]
        dims = "; ".join(f"{d} (values: {', '.join(dict.fromkeys(df[d].astype(str)))})" for d in dimension_columns(df))
        lines.append(f"[{i}] {m['title']} | {m['file']} p. {m['page']}\n  dimensions: {dims}\n  columns: {value_columns(df)}")
    return "\n".join(lines)


def comparison_md(comp: dict) -> str:
    """Markdown for compare_programs results, shared by both UIs."""
    if comp["missing"]:
        return f"To compare programs I need: **{', '.join(comp['missing'])}**."
    results = comp["results"]
    if not results:
        return "No programs with verified matrices found. Verify matrices in Verify Documents first."
    parts = []
    eligible = [r for r in results if r.get("eligible") is True]
    ineligible = [r for r in results if r.get("eligible") is False]
    errors = [r for r in results if r.get("eligible") is None]
    if eligible:
        rows = "\n".join(
            f"| {r['program']} | **{r['max_ltv']:g}%** | {r.get('cell', '')} | "
            f"{', '.join(r.get('overlays') or []).replace('|', '/') or 'none'}{' ⚠️ stacked' if r.get('stacked') else ''} |"
            for r in eligible)
        parts.append("**Eligible programs**\n\n| Program | Max LTV | Matrix cell | Overlays applied |\n"
                     f"|---|---|---|---|\n{rows}")
    if ineligible:
        parts.append("**Not eligible**\n\n" + "\n".join(
            f"- {r['program']}: {r.get('error') or r.get('cell') or 'ineligible per matrix'}" for r in ineligible))
    if errors:
        parts.append("**Could not check**\n\n" + "\n".join(
            f"- {r['program']}: {r.get('error', 'unknown error')}" for r in errors))
    if any(r.get("stacked") for r in eligible):
        parts.append("⚠️ Stacked means more than one reduction was applied. Confirm with underwriting that they stack.")
    parts.append("*Overlays checked against each program's guidelines. Pick a program to see the quotes, "
                 "the math, and anything left out.*")
    return "\n\n".join(parts)


RULES_STATUS = {
    "unapproved": "Overlay rules for this program aren't approved yet",
    "changed": "This program's overlay rules or PDF changed since you approved them",
    "bad_quotes": "Some overlay rule quotes aren't in the PDF word for word",
}


def rules_overlays(question: str, history: list[dict], res: dict, rules: dict):
    """Approved overlay rules, applied by Python to the facts the user stated. No model involved."""
    said = " ".join([*(m["content"] for m in history[-4:] if m["role"] == "user"), question])
    raw = fill_scenario(RawScenario(), said)
    facts = overlay_rules.extract_facts(said, raw.occupancy, raw.purpose, raw.fico, raw.loan_amount)
    out = overlay_rules.apply_rules(rules["rules"], facts, rules["source"])
    kept = [Adjustment(trigger="", **{k: a[k] for k in ("reason", "kind", "value", "file", "page", "quote")})
            for a in out["applied"]]
    calc = apply_adjustments(res["ltv"], [a.model_dump() for a in kept])
    notes = [f"⚠️ {c}" for c in out["confirm"]] + out["unknown"]
    return kept, [], notes, calc


def checked_overlays(question: str, history: list[dict], crit, res: dict, docs_text: str, model: str,
                     rules: dict | None = None):
    """Approved rules if the program has them. Otherwise the AI finds overlays, Python gates each one
    (check_adjustment) and does the math. Returns (kept, rejected [(adj, why)], cited notes, calc)."""
    if rules and rules["status"] == "approved":
        return rules_overlays(question, history, res, rules)
    kept, rejected, notes, calc = _ai_overlays(question, history, crit, res, docs_text, model)
    if rules:
        notes.insert(0, f"⚠️ {RULES_STATUS[rules['status']]}, so these overlays were found by the AI and checked "
                        "by Python. Approve the rules in Verify Documents to use them instead.")
    return kept, rejected, notes, calc


def _ai_overlays(question: str, history: list[dict], crit, res: dict, docs_text: str, model: str):
    convo = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-4:])
    adj = ask_json(
        ADJ_SYSTEM,
        f"Scenario: {question}\nEarlier messages:\n{convo}\n\nBase from matrix: {res['cell']} "
        f"({'; '.join(res['trace'])})\n\nGUIDELINES:\n\n{docs_text}",
        Adjustments, model,
    )
    doc = _norm(docs_text)
    facts = " ".join([question, crit.column, *(d.value for d in crit.dims), *res["trace"],
                      *(m["content"] for m in history if m["role"] == "user")])
    kept, rejected = [], []
    for a in adj.adjustments:
        why = check_adjustment(a, doc, facts)
        (rejected.append((a, why)) if why else kept.append(a))
    calc = apply_adjustments(res["ltv"], [a.model_dump() for a in kept])
    notes = [n for n in adj.notes if re.search(r"p\.\s*\d", n)]   # uncited notes are the AI talking, not the doc
    return kept, rejected, notes, calc


def python_mapping(df, raw: RawScenario) -> tuple[str, dict] | None:
    """Fallback when the AI says occupancy/purpose are missing but the user gave them:
    pick the one column that fits and fill loan amount/FICO dims. None if it isn't clear cut."""
    cols = value_columns(df)
    stated = _occ_keys(raw.occupancy or "")
    if any(_occ_keys(c) for c in cols):
        cols = [c for c in cols if _occ_keys(c) & stated]
    rx = {"Purchase": r"purchase", "Rate/Term Refinance": r"rate", "Cash Out Refinance": r"cash"}.get(raw.purpose or "")
    cols = [c for c in cols if re.search(rx, c, re.I)] if rx else []
    if len(cols) != 1:
        return None
    dims = {}
    for d in dimension_columns(df):
        if "loan" in d.lower() and raw.loan_amount:
            dims[d] = str(raw.loan_amount)
        elif "fico" in d.lower() and raw.fico:
            dims[d] = str(raw.fico)
        else:
            return None
    return cols[0], dims


def scenario_answer(question: str, history: list[dict], mats: list[dict], docs_text: str, model: str,
                    rules: dict | None = None) -> str | None:
    """Returns markdown if this is a scenario question, None if it isn't."""
    if not mats:
        return None
    convo = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-4:])
    crit = ask_json(
        CRITERIA_SYSTEM,
        f"Matrices:\n{describe_matrices(mats)}\n\nEarlier messages:\n{convo}\n\nQuestion: {question}",
        Criteria, model,
    )
    if not crit.is_scenario:
        return None
    if crit.missing:
        # The model sometimes says occupancy/purpose are missing when they're right there. Python decides.
        said = " ".join([*(h["content"] for h in history[-4:] if h["role"] == "user"), question])
        raw = fill_scenario(RawScenario(), said)
        idx = crit.matrix if 0 <= crit.matrix < len(mats) else (0 if len(mats) == 1 else -1)
        mapped = python_mapping(mats[idx]["df"], raw) if idx >= 0 and not raw.missing else None
        if not mapped:
            return "To pull the exact number I need: **" + ", ".join(raw.missing or crit.missing) + "**."
        crit.matrix, crit.column, crit.missing = idx, mapped[0], []
        crit.dims = [DimValue(name=k, value=v) for k, v in mapped[1].items()]
    if not 0 <= crit.matrix < len(mats):
        return None

    m = mats[crit.matrix]
    if m["status"] != "verified":
        return (f"**{m['file']}** hasn't been verified yet, so I won't quote numbers from it. "
                "Open **Verify Documents** in the sidebar, check the matrix against the PDF, and approve it.")
    probs = header_problems(m["df"])
    if probs:
        return (f"The column headers on **{m['file']}** look wrong, so I won't quote numbers from it:\n"
                + "\n".join(f"- {p}" for p in probs)
                + "\n\nOpen **Verify Documents**, re-read the PDF, and approve it again.")
    ref = m.get("reference") or {}
    if ref.get("status") == "mismatch":
        return (f"**{m['file']}** doesn't match the Revo site's copy of this matrix ({ref['source']}), "
                "so I won't quote numbers from it:\n" + "\n".join(f"- {d}" for d in ref["diffs"][:5])
                + "\n\nCheck the PDF in **Verify Documents** and fix whichever side is wrong.")

    said = " ".join([*(h["content"] for h in history[-4:] if h["role"] == "user"), question])
    blocked = occupancy_block(fill_scenario(RawScenario(), said).occupancy, mats, docs_text)
    if blocked:
        return f"**Not eligible.** {blocked}"

    res = lookup(m["df"], {d.name: d.value for d in crit.dims}, crit.column)
    out = [f"*Matrix: {m['file']} p. {m['page']}*"] + [f"- {t}" for t in res["trace"]]
    if ref.get("status") == "match":
        out.append("- ✅ Matrix matches the Revo site's copy cell for cell")
    if not res["ok"]:
        return "\n".join(out + ["", f"**Can't price this off the matrix:** {res['error']}"])
    if not res["eligible"]:
        return "\n".join(out + ["", f"**Not eligible.** The matrix shows **{res['cell']}** for this scenario."])

    kept, rejected, notes, calc = checked_overlays(question, history, crit, res, docs_text, model, rules)

    head = "**Not eligible**" if not calc["eligible"] else f"**Max LTV/CLTV: {calc['final']:g}%**"
    lines = [head, ""] + out + [f"- Matrix cell: **{res['cell']}**"]
    if rules and rules["status"] == "approved":
        lines += ["", "*Overlays from your approved rules for this program, applied by Python. "
                      "Assumes none of the \"If\" items under Also check apply.*"]
    if kept:
        lines += ["", "**Overlays applied**"]
        for a in kept:
            lines.append(f"- {a.kind.title()}{'' if a.kind == 'ineligible' else f' {a.value:g}%'}: {a.reason} "
                         f"({a.file}, p. {a.page}) ✅\n  > {a.quote}")
    if rejected:
        lines += ["", "**Not applied, check if they fit this deal**"]
        for a, why in rejected:
            lines.append(f"- ⚠️ {a.reason} ({a.file}, p. {a.page}): left out because {why}\n  > {a.quote}")
    if calc.get("steps"):
        lines += ["", "**Math**"] + [f"- {s}" for s in calc["steps"]]
    warnings = list(res["notes"])
    if calc.get("stacked"):
        warnings.append("More than one reduction was stacked. Confirm with underwriting that they stack.")
    if warnings:
        lines += [""] + [f"⚠️ {w}" for w in warnings]
    if notes:
        lines += ["", "**Also check**"] + [f"- {n}" for n in notes]
    return "\n".join(lines)
