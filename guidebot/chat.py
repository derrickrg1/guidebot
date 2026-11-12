"""The chat flow from ui/app.py without Streamlit, for the standalone HTML page (server.py).

respond() yields events: {"status": str}, {"text": chunk}, {"replace": full_text}, {"sources": [...]}.
All LTV guards live in answer.py, so both UIs get the same rules.
"""
import re

from guidebot import config, gov_matrix, library, overlay_rules
from guidebot.answer import (broad_messages, compare_programs, comparison_md, filter_to_cited, full_doc_messages,
                             full_section_messages, looks_like_deal, said_scenario, scenario_answer, search_messages)
from guidebot.llm import stream_chat
from guidebot.section_reader import cited_pages, expand_terms, route_and_load

NONQM = {"Express 1099", "Express Bank Statements", "Express DSCR",
         "Express Full Doc", "Express P&L", "Express 2nd"}
SHARED = "NonQM Shared"
# Programs at or under this are read whole for an all-programs question (the Express and Ignite sheets, ~10k
# each). Longer ones (FHA, VA, the 2nd-lien guides) are searched instead.
BROAD_SHEET_MAX = 20000

# One pick per doc type that runs Express and Ignite side by side. Each keeps its own matrix, overlays and
# guidelines; only the picking is combined.
FAMILIES = {
    "1099 (Express + Ignite)": ["Express 1099", "Ignite 1099"],
    "Bank Statements (Express + Ignite)": ["Express Bank Statements", "Ignite Bank Statements"],
    "DSCR (Express + Ignite)": ["Express DSCR", "Ignite Express DSCR"],
    "Full Doc (Express + Ignite)": ["Express Full Doc", "Ignite Full Doc"],
    "P&L (Express + Ignite)": ["Express P&L", "Ignite P&L"],
}

# The four tabs across the top of the page. Each one narrows the chat to its own programs: "all programs" under
# FHA means FHA only, and the shared NonQM guide only ever comes along with NonQM.
SECTIONS = {
    "NonQM": lambda name: name == SHARED or name.startswith(("Express", "Ignite")),
    "Conventional": lambda name: name.startswith("Conventional"),
    "FHA": lambda name: name.startswith("FHA"),
    "VA": lambda name: name == "VA" or name.startswith("VA "),
}

_retriever = None


def retriever():
    global _retriever
    if _retriever is None:
        from guidebot.ingest.indexer import get_collection
        from guidebot.retrieval import Retriever
        _retriever = Retriever(get_collection())
    return _retriever


def reset_retriever():
    global _retriever
    _retriever = None


def program_docs(choice: str, all_programs: dict) -> tuple[list[dict], list]:
    paths, dups = library.unique_docs(all_programs.get(choice, []))
    shared = all_programs.get(SHARED, []) if choice in NONQM else []
    all_paths, shared_dups = library.unique_docs(paths + shared)
    return [library.load(p) for p in all_paths], dups + shared_dups


def family_members(choice: str, all_programs: dict) -> list[str]:
    return [p for p in FAMILIES.get(choice, []) if p in all_programs]


def programs_info() -> list[dict]:
    all_programs = library.programs()
    out = []
    for prog in all_programs:
        if prog == SHARED:
            continue
        docs, dups = program_docs(prog, all_programs)
        out.append({
            "name": prog,
            "docs": [{"file": d["path"].name, "status": d["status"],
                      "shared": d["path"].parent.name == SHARED} for d in docs],
            "duplicates": [[a.name, b.name] for a, b in dups],
        })
    by_name = {p["name"]: p for p in out}
    for fam in FAMILIES:
        members = family_members(fam, all_programs)
        if len(members) > 1:
            out.append({"name": fam, "family": members,
                        "docs": [d for m in members for d in by_name[m]["docs"]],
                        "duplicates": [x for m in members for x in by_name[m]["duplicates"]]})
    return out


def find_pdf(fname: str):
    return next((p for paths in library.programs().values() for p in paths
                 if p.name == fname and p.suffix.lower() == ".pdf"), None)


def _sources(hits: list[dict]) -> list[dict]:
    keep = ("file", "loc", "section", "section_title", "program")
    return [{**{k: h.get(k, "") for k in keep}, "text": (h.get("text") or "")[:600]} for h in hits]


def _stream(messages, model):
    text = ""
    for chunk in stream_chat(messages, model):
        text += chunk
        yield {"text": chunk}
    return text


def _compare(question, history, progs, all_programs, model) -> str:
    """The matrix lookup on several programs at once, as one side-by-side table."""
    data = []
    for prog in progs:
        pdocs = program_docs(prog, all_programs)[0]
        mats = library.matrices(pdocs)
        if mats:
            data.append({"program": prog, "mats": mats,
                         "docs_text": "\n\n".join(library.render(d) for d in pdocs),
                         "rules": overlay_rules.for_program(prog, pdocs)})
    return comparison_md(compare_programs(question, history, data, model))


def _program_answer(question, history, prog, docs, model):
    """A guideline answer from one program: the whole program if it fits, else a search of it. Returns (answer, hits)."""
    ltv_ok = all(d["status"] == "verified" for d in docs)
    docs_text = "\n\n".join(library.render(d) for d in docs)
    if len(docs_text) <= config.FULL_DOC_MAX_CHARS:
        yield {"status": f"Reading {prog}…"}
        answer = yield from _stream(full_doc_messages(question, docs_text, history, ltv_ok=ltv_ok, model=model), model)
        return answer, []
    yield {"status": f"Searching {prog}…"}
    found = retriever().search(question, programs=[prog] + ([SHARED] if prog in NONQM else []))
    answer = yield from _stream(search_messages(question, found, history), model)
    return answer, filter_to_cited(answer, found, model)


def _route(question, history, gpath, model):
    """Shared guide sections for this question, plus the ones earlier answers in this chat cited."""
    return route_and_load(question, gpath, gpath.name, model, keep_pages=cited_pages(history, gpath.name))


def _broad_answer(question, history, all_programs, model):
    """A guideline question with no program picked. It used to read only the shared NonQM guide, so anything that
    lives on a program sheet (Ignite DSCR's 660 minimum for first time investors) came back "couldn't find it".
    Now every program sheet is read whole; guides too long for that (FHA, VA, 2nds) are searched, and the shared
    guide is routed to its relevant sections as before. Returns (answer, hits)."""
    sheets, long_progs = [], []
    for prog in sorted(all_programs):
        if prog == SHARED:
            continue
        paths, _ = library.unique_docs(all_programs[prog])   # the shared guide is added once below, not per program
        text = "\n\n".join(library.render(library.load(p)) for p in paths)
        if len(text) <= BROAD_SHEET_MAX:
            sheets.append(f"##### PROGRAM: {prog} #####\n\n{text}")
        else:
            long_progs.append(prog)
    yield {"status": "Finding the right guideline sections…"}
    extra, hits = [], []
    for gpath in all_programs.get(SHARED, []):
        if gpath.suffix.lower() == ".pdf":
            routed = _route(question, history, gpath, model)
            if routed:
                extra.append(f"##### SHARED NONQM GUIDE ({gpath.name}) #####\n\n{routed['text']}")
                hits += routed["sections"]
                break
    found = []
    try:
        if long_progs:
            found = retriever().search(question, programs=long_progs)
        # A sheet that's silent on a topic means the shared guide's rule applies, so the shared guide has to hold
        # that rule even when the router picked other sections (cash out for reserves lives under assets, p. 47,
        # while the router sent only §6.2 Reserves). Its best-matching pages are added unless already routed.
        routed_text = "\n".join(extra)
        found += [h for h in retriever().search(question, k=4, programs=[SHARED])
                  if h["text"].strip()[:200] not in routed_text]
    except Exception:
        pass   # no index yet: the sheets and routed shared guide still answer
    extra += [h["text"] for h in found]
    yield {"status": f"Reading {len(sheets)} program sheets…"}
    answer = yield from _stream(broad_messages(question, "\n\n".join(sheets), "\n\n".join(extra), history, model), model)
    if found:
        hits += filter_to_cited(answer, found, model)
    return answer, hits


def respond(question: str, history: list[dict], choice: str = "",
            mode_program: bool = False, mode_scenario: bool = False, section: str = ""):
    model = config.CHAT_MODEL
    all_programs = library.programs()
    asked = question
    question = expand_terms(question)   # "NOD" -> also "Notice of Default", for routing, search and the answer
    if section in SECTIONS:
        all_programs = {k: v for k, v in all_programs.items() if SECTIONS[section](k)}
    user_programs = [p for p in all_programs if p != SHARED]
    members = family_members(choice, all_programs)
    if choice and choice not in user_programs and not members:
        yield {"replace": f"Unknown program: {choice}"}
        return
    if mode_program and not choice:
        yield {"replace": "I need a specific program for that. Pick one in the sidebar, then ask again."}
        return
    if not user_programs:
        yield {"replace": f"No {section} programs are loaded yet."}
        return

    docs = program_docs(choice, all_programs)[0] if choice and not members else []
    answer, hits = "", []

    # A number for a specific deal only ever comes from the matrix lookup. With Scenario off, the guideline
    # reader used to write its own "Max LTV" (wrong FICO row, 80 - 5 = 76). With no program picked, the
    # deal runs against every program, same as Deal check mode; a family runs against its members.
    deal = looks_like_deal(asked, history)
    skipped = ""

    if mode_scenario or deal:
        if not choice or members:
            yield {"status": f"Checking {' and '.join(members)}…" if members else "Checking all programs…"}
            try:
                answer = _compare(asked, history, members or user_programs, all_programs, model)
                yield {"replace": answer}
            except Exception as e:
                skipped = str(e)
                yield {"status": f"Comparison skipped: {e}"}
        elif docs and (SECTIONS["FHA"](choice) or SECTIONS["VA"](choice)):
            # FHA/VA eligibility rows: Python only, start to finish
            mats = library.matrices(docs)
            answer = (gov_matrix.answer(said_scenario(asked, history), asked, mats) if mats else
                      f"The approved copies of the {choice} matrices have no LTV table in them yet. On the admin page, "
                      "open each one, hit **Re-read the PDF**, check the table against the page, and approve it.")
            yield {"replace": answer}
        elif docs:
            yield {"status": "Checking the matrix…"}
            try:
                scen = scenario_answer(asked, history, library.matrices(docs),
                                       "\n\n".join(library.render(d) for d in docs), model,
                                       overlay_rules.for_program(choice, docs))
                if scen:
                    answer = scen
                    yield {"replace": answer}
            except Exception as e:
                skipped = str(e)
                yield {"status": f"Scenario check skipped: {e}"}

    if deal and not answer:
        yield {"replace": "I couldn't run the matrix lookup on this one"
                          + (f" ({skipped})" if skipped else "")
                          + ", so I won't give an LTV. Spell out the FICO, loan amount, occupancy and purpose "
                            "and ask again."}
        return

    if mode_program and members and not answer:
        # Each program answers from its own guidelines, one after the other under its own heading.
        parts = []
        for i, prog in enumerate(members):
            head = ("\n\n" if i else "") + f"## {prog}\n\n"
            yield {"text": head}
            part, found = yield from _program_answer(question, history, prog, program_docs(prog, all_programs)[0], model)
            parts.append(head + part)
            hits += found
        answer = "".join(parts)

    if mode_program and docs and not answer:
        answer, hits = yield from _program_answer(question, history, choice, docs, model)

    if not answer and not choice:
        answer, hits = yield from _broad_answer(question, history, all_programs, model)

    if not answer:
        routed = None
        yield {"status": "Finding the right guideline sections…"}
        for gpath in all_programs.get(SHARED, []):
            if gpath.suffix.lower() == ".pdf":
                routed = _route(question, history, gpath, model)
                if routed:
                    break
        if routed:
            fname = routed["sections"][0]["file"] if routed["sections"] else ""
            yield {"status": "Reading guidelines…"}
            raw = "".join(stream_chat(full_section_messages(question, routed["text"], history, filename=fname, model=model), model))
            answer = re.sub(r"\(file,\s*p\.", f"({fname}, p.", raw)
            yield {"replace": answer}
            hits = routed["sections"]
        else:
            yield {"status": "Searching guidelines…"}
            progs = list(dict.fromkeys(([choice] if choice else []) + [SHARED]))
            found = retriever().search(question, programs=progs)
            if not found:
                answer = "Nothing indexed yet. Hit Update index first."
                yield {"replace": answer}
            else:
                answer = yield from _stream(search_messages(question, found, history), model)
                hits = filter_to_cited(answer, found, model)

    if not answer and docs:
        docs_text = "\n\n".join(library.render(d) for d in docs)
        ltv_ok = all(d["status"] == "verified" for d in docs)
        answer = yield from _stream(full_doc_messages(question, docs_text, history, ltv_ok=ltv_ok, model=model), model)

    if hits:
        yield {"sources": _sources(hits)}
