"""The chat flow from ui/app.py without Streamlit, for the standalone HTML page (server.py).

respond() yields events: {"status": str}, {"text": chunk}, {"replace": full_text}, {"sources": [...]}.
All LTV guards live in answer.py, so both UIs get the same rules.
"""
import re

from guidebot import agency, config, conv_matrix, gov_matrix, library, overlay_rules
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

# Base programs first, then their variants: Fannie Mae and Freddie Mac before HomeReady and RefiNow, Standard
# before Streamline and IRRRL. Everything else stays alphabetical.
_BASE = re.compile(r"(?:^Conventional (?:Fannie Mae|Freddie Mac)|^(?:FHA|VA) Standard)$")


NO_GENERAL_NOTE = (
    "##### NOTE: NO GENERAL GUIDELINE LOADED #####\n\nNo agency guide pages were found for this question, so there is "
    "no general guideline text to compare against. Don't write \"No overlay, general guideline\" or \"supersedes "
    "general guideline\" anywhere. Give each program's rule as its own sheet states it, cited to that sheet. Where a "
    "sheet is silent, say \"Not on the sheet; agency guide not loaded\". Don't mention this note; at most, one closing "
    "bullet may say the agency guide isn't loaded yet.")


# Conventional programs follow their own agency's guide: Fannie's Selling Guide for Fannie Mae, HomeReady and
# RefiNow, Freddie's Seller/Servicer Guide for Freddie Mac, Home Possible, HomeOne and Refi Possible.
FREDDIE = re.compile(r"freddie|home possible|homeone|refi possible", re.I)
AGENCY_ONLY = {
    "Conventional/Fannie Mae": "; the general guideline for Fannie Mae programs only (Fannie Mae, HomeReady, "
                               "RefiNow), never for Freddie Mac programs]",
    "Conventional/Freddie Mac": "; the general guideline for Freddie Mac programs only (Freddie Mac, Home Possible, "
                                "HomeOne, Refi Possible), never for Fannie Mae programs]",
}


def agency_folder(prog: str) -> str:
    """The one agency guide a Conventional program follows; "" for FHA and VA (their whole section applies)."""
    if not SECTIONS["Conventional"](prog):
        return ""
    return "Conventional/Freddie Mac" if FREDDIE.search(prog) else "Conventional/Fannie Mae"


def program_order(name: str) -> tuple:
    return (not _BASE.search(name), name)


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
    return next((p for paths in [*library.programs().values(), agency.files()] for p in paths
                 if p.name == fname and p.suffix.lower() == ".pdf"), None)


def _agency_section(choice: str, section: str) -> str:
    """FHA, VA or Conventional when the chat is about one of them: its agency guides are the general guideline."""
    return next((s for s in agency.SECTIONS if section == s or (choice and SECTIONS[s](choice))), "")


def _agency_hits(question: str, sec: str, k: int = 6, k_letters: int = 3, folder: str = "") -> list[dict]:
    """The agency guide pages that best match the question, each headed with its date and review status. The
    handbook and the letters/circulars each get their own share: a letter that's all about one topic outranked
    every handbook page on it, and the handbook's own rule went missing (FHA minimum score, 2026-11-12)."""
    if not sec:
        return []
    try:
        pool = retriever().search(question, k=80 if folder else 40, programs=[f"{sec} Agency"])
    except Exception:
        return []   # no index yet
    if folder:   # one agency's guide only: a Freddie program never gets Fannie's rules
        pool = [h for h in pool if (agency.info(h["file"]) or {}).get("rel", "").startswith(folder + "/")][:40]
    letter = lambda h: re.search(r"Mortgagee Letter|Circular", h["file"]) is not None
    books = sorted((h for h in pool if not letter(h)), key=lambda h: agency.off_topic(h["text"]))   # stable
    found = books[:k] + [h for h in pool if letter(h)][:k_letters]
    out = []
    for i, h in enumerate(found):
        rec = agency.info(h["file"])
        head = f"[GENERAL GUIDELINE, agency guide: {agency.label(rec) if rec else h['file']}]"
        rel = rec["rel"] if rec else ""
        for f, note in AGENCY_ONLY.items():
            if rel.startswith(f + "/"):
                head = head[:-1] + note
        # The index holds one page per chunk, and a handbook rule often runs across pages (HUD's MDCS definition
        # on p. 176, its standard on p. 177; the gift donor list starts on p. 362, the hit was p. 363). Every
        # handbook page brings the next one along, and the three best also bring the one before.
        n = int(re.sub(r"\D", "", h.get("loc", "")) or 0)
        before, after = "", ""
        if n and not letter(h):
            prev, nxt = (agency.page_text(h["file"], n - 1).strip() if i < 3 else ""), agency.page_text(h["file"], n + 1).strip()
            before = f"=== {h['file']} | p. {n - 1} ===\n{prev}\n" if prev else ""
            after = f"\n=== {h['file']} | p. {n + 1} ===\n{nxt}" if nxt else ""
        out.append({**h, "text": f"{head}\n{before}{h['text']}{after}{_exhibits(h['file'])}", "agency": True, "page": n})
    return out


def _exhibits(filename: str) -> str:
    """A VA circular's exhibits, which hold its tables: 26-23-6 says the current funding fees are "in Exhibit B"."""
    m = re.match(r"VA Circular (26-\d+-\d+)(?: Change \d+)?\.pdf$", filename)
    if not m:
        return ""
    parts = []
    for p in agency.files():
        if p.name.startswith(f"VA Circular {m[1]} Exhibit"):
            text = "\n".join(t.strip() for t in agency.pages(p))[:6000]
            parts.append(f"\n=== {p.name} | p. 1 ===\n{text}")
    return "".join(parts)


def _agency_block(hits: list[dict]) -> str:
    return ("\n\n##### GENERAL GUIDELINE: AGENCY GUIDES (most relevant pages) #####\n\n"
            + "\n\n".join(h["text"] for h in hits)) if hits else ""


def _fix_agency_cites(answer: str) -> str:
    """Puts the full filename back on agency citations the model shortened ("VA M26-7 Chapter 07, p. 2"), so the
    chip opens the PDF and the page counts as cited. Only a name that starts exactly one agency file is changed."""
    stems = {p.stem: p.name for p in agency.files()}

    def seg(s):
        name = s.split(",")[0].strip()
        if len(name) < 8 or ".pdf" in s.lower():
            return s
        key = re.sub(r"Chapter (\d)\b", r"Chapter 0\1", name).lower()
        match = [st for st in stems if st.lower().startswith(key)]
        return s.replace(name, stems[match[0]], 1) if len(match) == 1 else s

    def paren(m):
        return "(" + ";".join(seg(s) for s in m[1].split(";")) + ")" if re.search(r",\s*pp?\.\s*\d", m[1]) else m[0]
    return re.sub(r"\(([^()]*)\)", paren, answer)


def _agency_note(answer: str, hits: list[dict]) -> tuple[str, list[dict]]:
    """The agency pages the answer cited, and a line naming any of their guides that haven't been reviewed yet."""
    def used(h):
        pages = cited_pages([{"role": "assistant", "content": answer}], h["file"])
        return h["file"] in answer and (not pages or not h.get("page") or {h["page"], h["page"] + 1} & set(pages))
    cited = [h for h in hits if h.get("agency") and used(h)]
    pending = {}
    for h in cited:
        rec = agency.info(h["file"])
        if rec and not rec.get("reviewed"):
            pending[rec.get("title") or h["file"]] = rec.get("downloaded", "")
    note = ("\n\n_" + "; ".join(f"{t} was updated here {d}" for t, d in pending.items())
            + (". It hasn't" if len(pending) == 1 else ". They haven't") + " been reviewed yet._") if pending else ""
    return note, cited


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
        # A Conventional sheet approved before conv_matrix has no table at all: list it as needing a re-read
        if mats or SECTIONS["Conventional"](prog):
            data.append({"program": prog, "mats": mats,
                         "docs_text": "\n\n".join(library.render(d) for d in pdocs),
                         "rules": overlay_rules.for_program(prog, pdocs)})
    return comparison_md(compare_programs(question, history, data, model))


def _program_answer(question, history, prog, docs, model):
    """A guideline answer from one program: the whole program if it fits, else a search of it. FHA, VA and
    Conventional sheets also get the matching pages of their agency guides. Returns (answer, hits)."""
    ltv_ok = all(d["status"] == "verified" for d in docs)
    docs_text = "\n\n".join(library.render(d) for d in docs)
    gov = _agency_hits(question, _agency_section(prog, ""), folder=agency_folder(prog))
    if len(docs_text) <= config.FULL_DOC_MAX_CHARS:
        yield {"status": f"Reading {prog}…"}
        no_general = "\n\n" + NO_GENERAL_NOTE if _agency_section(prog, "") and not gov else ""
        answer = yield from _stream(full_doc_messages(question, docs_text + _agency_block(gov) + no_general, history,
                                                      ltv_ok=ltv_ok, model=model), model)
        return answer, gov
    yield {"status": f"Searching {prog}…"}
    found = retriever().search(question, programs=[prog] + ([SHARED] if prog in NONQM else []))
    answer = yield from _stream(search_messages(question, found + gov, history), model)
    return answer, filter_to_cited(answer, found, model) + gov


def _route(question, history, gpath, model):
    """Shared guide sections for this question, plus the ones earlier answers in this chat cited."""
    return route_and_load(question, gpath, gpath.name, model, keep_pages=cited_pages(history, gpath.name))


def _broad_answer(question, history, all_programs, model, section=""):
    """A guideline question with no program picked. It used to read only the shared NonQM guide, so anything that
    lives on a program sheet (Ignite DSCR's 660 minimum for first time investors) came back "couldn't find it".
    Now every program sheet is read whole; guides too long for that (FHA, VA, 2nds) are searched, and the shared
    guide is routed to its relevant sections as before. Returns (answer, hits)."""
    sheets, long_progs = [], []
    for prog in sorted(all_programs, key=program_order):
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
        if SHARED in all_programs:   # the NonQM tab only; the FHA tab was getting NonQM guide pages
            found += [h for h in retriever().search(question, k=4, programs=[SHARED])
                      if h["text"].strip()[:200] not in routed_text]
    except Exception:
        pass   # no index yet: the sheets and routed shared guide still answer
    sec = _agency_section("", section)
    gov = _agency_hits(question, sec)
    extra += [h["text"] for h in found + gov]
    if sec and not gov:
        # No agency guide loaded (Fannie's Selling Guide is a manual download): with nothing to quote, the model
        # was labeling each sheet's own rule "No overlay, general guideline".
        extra.append(NO_GENERAL_NOTE)
    yield {"status": f"Reading {len(sheets)} program sheets…"}
    answer = yield from _stream(broad_messages(question, "\n\n".join(sheets), "\n\n".join(extra), history, model), model)
    if found:
        hits += filter_to_cited(answer, found, model)
    return answer, hits + gov


def named_programs(question: str, programs: list[str]) -> list[str]:
    """Programs the question names, spaces and hyphens ignored ("Home Ready" is HomeReady). A name inside a
    longer one that also matched gives way (Home Possible to Home Possible VLIP, Express DSCR to Ignite Express
    DSCR). FHA and VA are left out: "VA cash-out" is a loan purpose too, and those tabs run every sheet anyway."""
    flat = lambda t: re.sub(r"[\s\-_]+", "", t.lower())
    q = flat(question)
    hits = {p: flat(re.sub(r"^Conventional ", "", p)) for p in programs
            if not (SECTIONS["FHA"](p) or SECTIONS["VA"](p))}
    hits = {p: n for p, n in hits.items() if len(n) > 3 and n in q}
    return [p for p, n in hits.items() if not any(n != o and n in o for o in hits.values())]


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

    # "Freddie Mac: 700 FICO, $1.4M purchase..." with All picked: the deal runs on the program it names.
    named = named_programs(asked, user_programs) if not choice and looks_like_deal(asked, history) else []
    if len(named) == 1:
        choice = named[0]
    elif named:
        members = named

    docs = program_docs(choice, all_programs)[0] if choice and not members else []
    # FHA and VA are one program per sheet, but their eligibility rows are all read by gov_matrix: with none
    # picked, a deal runs against every sheet in the tab together.
    gov = section in ("FHA", "VA") and not choice or choice and (SECTIONS["FHA"](choice) or SECTIONS["VA"](choice))
    # A picked Conventional sheet is read by conv_matrix. With none picked, the deal runs program by program (_compare).
    conv = bool(choice) and not members and SECTIONS["Conventional"](choice)
    answer, hits = "", []

    # A number for a specific deal only ever comes from the matrix lookup. With Scenario off, the guideline
    # reader used to write its own "Max LTV" (wrong FICO row, 80 - 5 = 76). With no program picked, the
    # deal runs against every program, same as Deal check mode; a family runs against its members.
    deal = looks_like_deal(asked, history)
    skipped = ""

    if deal and not mode_scenario and not choice and not named:
        # Guidelines mode, no program picked: ask before running every matrix (Derrick, 2026-11-12)
        where = f"all {section} programs" if section else "all programs"
        yield {"replace": f"That looks like a specific deal. Pick a program for its matrix, or review {where} "
                          "side by side.", "choices": ["pick", "all"]}
        return

    if mode_scenario or deal:
        if (not choice or members) and not gov:
            yield {"status": f"Checking {' and '.join(members)}…" if members else "Checking all programs…"}
            try:
                answer = _compare(asked, history, members or user_programs, all_programs, model)
                yield {"replace": answer}
            except Exception as e:
                skipped = str(e)
                yield {"status": f"Comparison skipped: {e}"}
        elif gov:
            docs = docs or [d for p in user_programs for d in program_docs(p, all_programs)[0]]
            # FHA/VA eligibility rows: Python only, start to finish
            mats = library.matrices(docs)
            answer = (gov_matrix.answer(said_scenario(asked, history), asked, mats) if mats else
                      f"The approved copies of the {choice or section} matrices have no LTV table in them yet. On the admin page, "
                      "open each one, hit **Re-read the PDF**, check the table against the page, and approve it.")
            yield {"replace": answer}
        elif conv and docs:
            mats = library.matrices(docs)
            answer = (conv_matrix.answer(said_scenario(asked, history), asked, mats) if conv_matrix.is_conv(mats) else
                      f"The approved copy of the {choice} matrix has no LTV table in it yet. On the admin page, open it, "
                      "hit **Re-read the PDF**, check the table against the page, and approve it.")
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
        answer, hits = yield from _broad_answer(question, history, all_programs, model, section)

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
            gov = _agency_hits(question, _agency_section(choice, section))
            if not found and not gov:
                answer = "Nothing indexed yet. Hit Update index first."
                yield {"replace": answer}
            else:
                answer = yield from _stream(search_messages(question, found + gov, history), model)
                hits = filter_to_cited(answer, found, model) + gov

    if not answer and docs:
        docs_text = "\n\n".join(library.render(d) for d in docs)
        ltv_ok = all(d["status"] == "verified" for d in docs)
        answer = yield from _stream(full_doc_messages(question, docs_text, history, ltv_ok=ltv_ok, model=model), model)

    # agency pages only count as sources when the answer cited them; unreviewed guides get a line saying so
    if any(h.get("agency") for h in hits) and (fixed := _fix_agency_cites(answer)) != answer:
        answer = fixed
        yield {"replace": answer}
    note, cited = _agency_note(answer, hits)
    hits = [h for h in hits if not h.get("agency")] + cited
    if note:
        yield {"text": note}
    if hits:
        yield {"sources": _sources(hits)}
