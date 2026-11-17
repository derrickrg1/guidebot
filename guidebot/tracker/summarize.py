"""One agency release (a Mortgagee Letter, Circular, Lender Letter, Selling Guide announcement, or the lines a
guide edition added and removed) turned into plain rule changes by the model. Same fallback chain as answers:
Claude, then Gemini, then local (llm.ask_json).

The model only reads and restates. Every date it gives is checked to look like a date, and a release it says has
nothing for someone originating a forward loan is left out of the feed.
"""
import re

from pydantic import BaseModel, Field

from guidebot import agency, llm

MAX_CHARS = 60000   # a Selling Guide announcement is 8-25k; a 4000.1 diff is cut to its first lines anyway


class Change(BaseModel):
    topic: str = Field(description="What the rule is about, a few words: 'Gift funds', 'DTI with AUS approval'")
    old: str | None = Field(description="The rule before, only if the text states it; null otherwise")
    new: str = Field(description="The rule now, one or two plain sentences with the numbers exactly as written")
    section: str | None = Field(description="Guide section or chapter the change is in (B3-4.3-04, II.A.4.c, "
                                            "Chapter 4), null if not stated")
    effective: str | None = Field(description="When this change takes effect, as written in the text; null if not stated")


class Release(BaseModel):
    origination: bool = Field(description="True if anything here matters to someone originating or underwriting "
                                          "a new forward purchase or refinance loan. False for servicing, loss "
                                          "mitigation, HECM, multifamily, or only administrative notices")
    summary: str = Field(description="Two sentences at most: what this release does, for a loan officer")
    effective: str | None = Field(description="The main effective date, as written; null if not stated")
    changes: list[Change] = Field(description="Each concrete rule change for originating or underwriting. Leave "
                                              "out servicing-only items, typo fixes and reformatting")


SYSTEM = """You read official mortgage agency releases (HUD/FHA, VA, Fannie Mae, Freddie Mac) and list the rule
changes in them for loan officers and underwriters at a mortgage lender.

Rules:
- Use only what the text says. Never add a rule, number, date or section from memory.
- Keep every number, percentage, dollar amount and date exactly as written.
- "old" is the prior rule only when the text states it (a "previously" / "currently" / "was" sentence, a removed line). Otherwise null.
- One change per rule. Keep each "new" to one or two plain sentences.
- Leave out servicing, loss mitigation, reverse mortgage (HECM), multifamily, and purely administrative items.
- Plain words. Never use em dashes or en dashes."""

DIFF_NOTE = ("This is not the whole document. It is the lines a new edition of the guide ADDED and REMOVED compared "
             "with the last edition, with page numbers. Pair a removed line with an added one when they are the old "
             "and new versions of the same rule. Ignore lines that only moved or were reformatted.")


def iso(s: str | None) -> str | None:
    """'October 1, 2026' / '2026-10-01' / '10/1/2026' as 2026-10-01, None if it isn't a date."""
    if not s:
        return None
    if m := re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", s):
        return m[0]
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", s):
        return f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"
    return agency.find_date(s) or None


def diff_text(diff: dict) -> str:
    """page_diff's output as text for the model."""
    out = ["REMOVED lines (old edition):"]
    out += [f"[p. {p}] {line}" for p, line in diff.get("removed_lines", [])] or ["(none)"]
    out += ["", "ADDED lines (new edition):"]
    out += [f"[p. {p}] {line}" for p, line in diff.get("added_lines", [])] or ["(none)"]
    return "\n".join(out)


def summarize(agency_name: str, title: str, text: str, is_diff: bool = False) -> Release:
    """The release's changes. Raises if every model in the chain failed."""
    text = text[:MAX_CHARS]
    user = f"Agency: {agency_name}\nRelease: {title}\n\n" + (DIFF_NOTE + "\n\n" if is_diff else "") + "TEXT:\n" + text
    rel = llm.ask_json(SYSTEM, user, Release)
    rel.effective = iso(rel.effective)
    for c in rel.changes:
        c.effective = iso(c.effective) or rel.effective
    return rel
