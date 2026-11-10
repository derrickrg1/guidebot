"""The chat flow from ui/app.py without Streamlit, for the standalone HTML page (server.py).

respond() yields events: {"status": str}, {"text": chunk}, {"replace": full_text}, {"sources": [...]}.
All LTV guards live in answer.py, so both UIs get the same rules.
"""
import re

from guidebot import config, library, overlay_rules
from guidebot.answer import (compare_programs, comparison_md, filter_to_cited, full_doc_messages, full_section_messages,
                             scenario_answer, search_messages)
from guidebot.llm import stream_chat
from guidebot.section_reader import route_and_load

NONQM = {"NonQM 1099", "NonQM Bank Statements", "NonQM Express DSCR",
         "NonQM Full Doc", "NonQM P&L", "NonQM 2nd"}
SHARED = "NonQM Shared"

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


def respond(question: str, history: list[dict], choice: str = "",
            mode_program: bool = False, mode_scenario: bool = False):
    model = config.CHAT_MODEL
    all_programs = library.programs()
    user_programs = [p for p in all_programs if p != SHARED]
    if choice and choice not in user_programs:
        yield {"replace": f"Unknown program: {choice}"}
        return
    if mode_program and not choice:
        yield {"replace": "I need a specific program for that. Pick one in the sidebar, then ask again."}
        return

    docs = program_docs(choice, all_programs)[0] if choice else []
    ltv_ok = all(d["status"] == "verified" for d in docs)
    answer, hits = "", []

    if mode_scenario:
        if not choice:
            yield {"status": "Checking all programs…"}
            try:
                data = []
                for prog in user_programs:
                    pdocs = program_docs(prog, all_programs)[0]
                    mats = library.matrices(pdocs)
                    if mats:
                        data.append({"program": prog, "mats": mats,
                                     "docs_text": "\n\n".join(library.render(d) for d in pdocs),
                                     "rules": overlay_rules.for_program(prog, pdocs)})
                answer = comparison_md(compare_programs(question, history, data, model))
                yield {"replace": answer}
            except Exception as e:
                yield {"status": f"Comparison skipped: {e}"}
        elif docs:
            yield {"status": "Checking the matrix…"}
            try:
                scen = scenario_answer(question, history, library.matrices(docs),
                                       "\n\n".join(library.render(d) for d in docs), model,
                                       overlay_rules.for_program(choice, docs))
                if scen:
                    answer = scen
                    yield {"replace": answer}
            except Exception as e:
                yield {"status": f"Scenario check skipped: {e}"}

    if mode_program and docs and not answer:
        docs_text = "\n\n".join(library.render(d) for d in docs)
        if len(docs_text) <= config.FULL_DOC_MAX_CHARS:
            yield {"status": f"Reading {choice}…"}
            answer = yield from _stream(full_doc_messages(question, docs_text, history, ltv_ok=ltv_ok), model)
        else:
            yield {"status": "Searching the program…"}
            found = retriever().search(question, programs=[choice] + ([SHARED] if choice in NONQM else []))
            answer = yield from _stream(search_messages(question, found, history), model)
            hits = filter_to_cited(answer, found, model)

    if not answer:
        routed = None
        yield {"status": "Finding the right guideline sections…"}
        for gpath in all_programs.get(SHARED, []):
            if gpath.suffix.lower() == ".pdf":
                routed = route_and_load(question, gpath, gpath.name, model)
                if routed:
                    break
        if routed:
            fname = routed["sections"][0]["file"] if routed["sections"] else ""
            yield {"status": "Reading guidelines…"}
            raw = "".join(stream_chat(full_section_messages(question, routed["text"], history, filename=fname), model))
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
        answer = yield from _stream(full_doc_messages(question, docs_text, history, ltv_ok=ltv_ok), model)

    if hits:
        yield {"sources": _sources(hits)}
