"""Two ways to answer:
1. Scenario (max LTV for a specific deal): the AI only maps the deal to the matrix and finds overlays.
   Python looks up the cell and does the math. Every overlay quote gets checked against the source.
2. Everything else: the AI reads the program's COMPLETE guidelines, not fragments."""
import re
from typing import Literal

from pydantic import BaseModel

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


def extract_scenario(question: str, history: list[dict], model: str) -> RawScenario | None:
    convo = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-4:])
    try:
        return ask_json(RAW_SCENARIO_SYSTEM, f"Earlier messages:\n{convo}\n\nQuestion: {question}", RawScenario, model)
    except Exception:
        return None


def compare_programs(
    question: str,
    history: list[dict],
    program_data: list[dict],  # [{program, mats, docs_text}]
    model: str,
) -> dict:
    """
    Run a scenario across multiple programs.
    Returns {missing, results: [{program, eligible, max_ltv, cell, trace, error}]}
    Results sorted: eligible first by LTV desc, then ineligible, then errors.
    Overlays are skipped for speed — select a program for detail.
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
        if crit.missing:
            results.append({"program": prog, "eligible": None, "error": f"Need: {', '.join(crit.missing)}"})
            continue
        if not 0 <= crit.matrix < len(mats):
            results.append({"program": prog, "eligible": None, "error": "Matrix index out of range"})
            continue
        if header_problems(mats[crit.matrix]["df"]):
            results.append({"program": prog, "eligible": None, "error": "Matrix headers need re-verifying"})
            continue
        if mats[crit.matrix].get("reference", {}).get("status") == "mismatch":
            results.append({"program": prog, "eligible": None, "error": "Matrix disagrees with the Revo site matrix"})
            continue

        res = lookup(mats[crit.matrix]["df"], {d.name: d.value for d in crit.dims}, crit.column)
        if not res["ok"]:
            results.append({"program": prog, "eligible": False, "cell": res.get("cell", ""), "trace": res.get("trace", []), "error": res.get("error", "")})
        elif not res["eligible"]:
            results.append({"program": prog, "eligible": False, "cell": res.get("cell", "N/A"), "trace": res.get("trace", [])})
        else:
            results.append({"program": prog, "eligible": True, "max_ltv": res["ltv"], "cell": res["cell"], "trace": res["trace"]})

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
- file/page: where it says so. quote: copy the supporting words EXACTLY from the document.
Do not include overlays that depend on facts the user didn't state. Put those in notes as short
"If ..." lines with (file, p. X). Also put other requirements that apply to this deal but don't change the LTV
(reserves, escrows, seasoning, max loan amount) in notes, with citations."""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9%$.]+", " ", s.lower()).strip()


def describe_matrices(mats: list[dict]) -> str:
    lines = []
    for i, m in enumerate(mats):
        df = m["df"]
        dims = "; ".join(f"{d} (values: {', '.join(dict.fromkeys(df[d].astype(str)))})" for d in dimension_columns(df))
        lines.append(f"[{i}] {m['title']} | {m['file']} p. {m['page']}\n  dimensions: {dims}\n  columns: {value_columns(df)}")
    return "\n".join(lines)


def scenario_answer(question: str, history: list[dict], mats: list[dict], docs_text: str, model: str) -> str | None:
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
        return "To pull the exact number I need: **" + ", ".join(crit.missing) + "**."
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

    res = lookup(m["df"], {d.name: d.value for d in crit.dims}, crit.column)
    out = [f"*Matrix: {m['file']} p. {m['page']}*"] + [f"- {t}" for t in res["trace"]]
    if ref.get("status") == "match":
        out.append("- ✅ Matrix matches the Revo site's copy cell for cell")
    if not res["ok"]:
        return "\n".join(out + ["", f"**Can't price this off the matrix:** {res['error']}"])
    if not res["eligible"]:
        return "\n".join(out + ["", f"**Not eligible.** The matrix shows **{res['cell']}** for this scenario."])

    adj = ask_json(
        ADJ_SYSTEM,
        f"Scenario: {question}\nEarlier messages:\n{convo}\n\nBase from matrix: {res['cell']} "
        f"({'; '.join(res['trace'])})\n\nGUIDELINES:\n\n{docs_text}",
        Adjustments, model,
    )
    calc = apply_adjustments(res["ltv"], [a.model_dump() for a in adj.adjustments])
    doc = _norm(docs_text)

    head = "**Not eligible**" if not calc["eligible"] else f"**Max LTV/CLTV: {calc['final']:g}%**"
    lines = [head, ""] + out + [f"- Matrix cell: **{res['cell']}**"]
    if adj.adjustments:
        lines += ["", "**Overlays applied**"]
        for a in adj.adjustments:
            check = "✅" if _norm(a.quote) and _norm(a.quote) in doc else "⚠️ quote not found word-for-word, check it"
            lines.append(f"- {a.kind.title()} {'' if a.kind == 'ineligible' else f'{a.value:g}%'}: {a.reason} "
                         f"({a.file}, p. {a.page}) {check}\n  > {a.quote}")
    if calc.get("steps"):
        lines += ["", "**Math**"] + [f"- {s}" for s in calc["steps"]]
    warnings = list(res["notes"])
    if calc.get("stacked"):
        warnings.append("More than one reduction was stacked. Confirm with underwriting that they stack.")
    if warnings:
        lines += [""] + [f"⚠️ {w}" for w in warnings]
    if adj.notes:
        lines += ["", "**Also check**"] + [f"- {n}" for n in adj.notes]
    return "\n".join(lines)
