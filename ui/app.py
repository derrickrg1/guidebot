import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from guidebot import config, library
from guidebot.answer import full_doc_messages, filter_to_cited, full_section_messages, scenario_answer, search_messages, compare_programs
from guidebot.section_reader import route_and_load
from ui.style import inject
from guidebot.ingest.indexer import get_collection, sync_index
from guidebot.llm import stream_chat
from guidebot.retrieval import Retriever

ICON = {"verified": "✅", "unverified": "⚠️", "changed": "🔄"}

_NONQM = {"NonQM 1099", "NonQM Bank Statements", "NonQM Express DSCR",
           "NonQM Full Doc", "NonQM P&L", "NonQM 2nd"}
_SHARED = "NonQM Shared"

st.set_page_config(page_title="Guidebot", layout="wide")
inject()


@st.cache_resource
def load_retriever():
    return Retriever(get_collection())


def _render_comparison(comp: dict) -> str:
    """Render cross-program scenario comparison. Returns markdown string for history."""
    if comp["missing"]:
        msg = f"To compare programs I need: **{', '.join(comp['missing'])}**."
        st.markdown(msg)
        return msg

    results = comp["results"]
    if not results:
        msg = "No programs with verified matrices found. Run **Update index** then verify matrices in Verify Documents."
        st.markdown(msg)
        return msg

    eligible = [r for r in results if r.get("eligible") is True]
    ineligible = [r for r in results if r.get("eligible") is False]
    errors = [r for r in results if r.get("eligible") is None]

    lines = []
    if eligible:
        st.markdown("**Eligible programs**")
        rows = [("Program", "Base LTV", "Matrix cell")]
        for r in eligible:
            rows.append((r["program"], f"{r['max_ltv']:g}%", r.get("cell", "")))
        # render as simple markdown table
        header = f"| {rows[0][0]} | {rows[0][1]} | {rows[0][2]} |"
        sep = "|---|---|---|"
        body = "\n".join(f"| {r[0]} | {r[1]} | {r[2]} |" for r in rows[1:])
        tbl = f"{header}\n{sep}\n{body}"
        st.markdown(tbl)
        lines.append("**Eligible programs**\n" + tbl)

    if ineligible:
        st.markdown("**Not eligible**")
        for r in ineligible:
            reason = r.get("error") or r.get("cell") or "ineligible per matrix"
            item = f"- {r['program']}: {reason}"
            st.markdown(item)
            lines.append(item)

    if errors:
        with st.expander("Could not check", expanded=False):
            for r in errors:
                st.caption(f"{r['program']}: {r.get('error', 'unknown error')}")

    note = "*Base LTV from matrix only — overlays not applied. Select a specific program for full overlay analysis.*"
    st.caption(note)
    lines.append(note)
    return "\n\n".join(lines)


import re as _re_src
import pdfplumber as _pdfplumber
import base64 as _base64
from io import BytesIO as _BytesIO


def _cited_page_for_source(answer: str, fname: str, loc: str) -> int | None:
    """
    Scan the answer for citations like (fname, ..., p. N) that fall within
    the source's page range. Returns the most specific cited page, or the
    section start if none found.
    """
    page_nums = _re_src.findall(r"\d+", loc)
    if not page_nums:
        return None
    start_p = int(page_nums[0])
    end_p = int(page_nums[-1]) if len(page_nums) > 1 else start_p
    pattern = _re_src.escape(fname) + r"[^)]*p\.\s*(\d+)"
    for pg_str in _re_src.findall(pattern, answer):
        pg = int(pg_str)
        if start_p <= pg <= end_p:
            return pg
    return start_p


def _render_sources(sources: list[dict], answer: str = ""):
    """Render source citations with section info and extracted text."""
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})", expanded=False):
        for s in sources:
            fname = s.get("file", "")
            loc = s.get("loc", "")
            section = s.get("section", "")
            section_title = s.get("section_title", "")
            prog = s.get("program", "")
            text = s.get("text", "")

            if section:
                header = f"**§{section} {section_title}** — {fname} ({loc})"
            else:
                header = f"**{prog} | {fname}** {loc}" if prog else f"**{fname}** {loc}"

            st.markdown(header)

            if text:
                # Strip bracketed prefix and short running headers
                lines = text.split("\n")
                lines = [l for l in lines if not l.startswith("[")]
                while lines and len(lines[0].strip()) < 30 and not lines[0].strip().endswith((".",":",",")):
                    lines.pop(0)
                body = "\n".join(lines).strip()
                if body:
                    st.markdown(
                        f"<div style='background:#ede9e1;border-left:3px solid #b89230;"
                        f"padding:8px 12px;margin:4px 0 12px 0;font-size:12px;"
                        f"color:#1a1814;white-space:pre-wrap;border-radius:0 4px 4px 0;'>"
                        f"{body[:600]}{'…' if len(body) > 600 else ''}</div>",
                        unsafe_allow_html=True,
                    )

            # Render PDF page image and link — use exact cited page if available
            pdf_path = next(
                (p for prog_paths in all_programs.values()
                 for p in prog_paths if p.name == fname and p.suffix.lower() == ".pdf"),
                None,
            )
            if pdf_path and loc:
                target_p = _cited_page_for_source(answer, fname, loc)
                if target_p:
                    try:
                        with _pdfplumber.open(pdf_path) as _pdf:
                            pi = target_p - 1
                            if pi < len(_pdf.pages):
                                img = _pdf.pages[pi].to_image(resolution=150)
                                buf = _BytesIO()
                                img.original.save(buf, format="PNG")
                                b64 = _base64.b64encode(buf.getvalue()).decode()
                                pdf_url = f"/app/static/{fname}#page={target_p}"
                                st.markdown(
                                    f'<a href="{pdf_url}" target="_blank" title="Open PDF at p. {target_p}">'
                                    f'<img src="data:image/png;base64,{b64}" width="180" '
                                    f'style="cursor:pointer;border:1px solid #d4c9b0;border-radius:4px;margin:4px 0;" />'
                                    f'</a>',
                                    unsafe_allow_html=True,
                                )
                    except Exception:
                        pass

            st.divider()


# ---------- sidebar ----------
with st.sidebar:
    st.markdown("## Guidebot")
    st.divider()

    all_programs = library.programs()
    user_programs = [p for p in all_programs if p != _SHARED]

    mode_guideline = True  # always on — guidelines is the default behavior
    mode_matrix = st.checkbox("Program")
    mode_scenario = st.checkbox("Scenario")
    st.checkbox("Pricing", value=False, disabled=True, help="Coming soon")

    choice = ""
    if mode_matrix or mode_scenario:
        # Scenario can run without a program (compares all); Program always needs one
        if mode_scenario and not mode_matrix:
            program_options = [""] + user_programs
            choice = st.selectbox(
                "Program",
                program_options,
                format_func=lambda x: "All programs" if x == "" else x,
                label_visibility="collapsed",
            )
        else:
            program_options = [""] + user_programs
            choice = st.selectbox(
                "Program",
                program_options,
                format_func=lambda x: "Select a program..." if x == "" else x,
                label_visibility="collapsed",
            )

    # Active mode indicator
    _scenario_all = mode_scenario and not mode_matrix and choice == ""
    _display_name = "All programs" if _scenario_all else choice
    if _display_name:
        if mode_matrix and mode_scenario:
            _c, _lbl = "#b89230", "Program + Scenario"
        elif mode_matrix:
            _c, _lbl = "#b89230", "Program"
        else:
            _c, _lbl = "#1a2e52", "Scenario"
        st.markdown(
            f"<div style='padding:6px 10px;background:{_c}18;border-left:3px solid {_c};"
            f"font-size:0.7rem;font-weight:600;color:{_c};letter-spacing:0.06em;margin-top:2px;'>"
            f"{_display_name}<br><span style='font-weight:400;color:#a09a90;font-size:0.63rem;"
            f"letter-spacing:0.03em;text-transform:none;'>{_lbl}</span></div>",
            unsafe_allow_html=True,
        )
    elif mode_matrix:
        st.caption("Select a program above.")

    docs = []
    if choice:
        prog_paths, dups = library.unique_docs(all_programs.get(choice, []))
        shared_paths = all_programs.get(_SHARED, []) if choice in _NONQM else []
        all_paths, shared_dups = library.unique_docs(prog_paths + shared_paths)
        dups = dups + shared_dups
        docs = [library.load(p) for p in all_paths]
        st.divider()
        for d in docs:
            lbl = "(shared)" if d["path"].parent.name == _SHARED else ""
            st.caption(f"{ICON[d['status']]} {d['path'].name} {lbl}".strip())
        for dup, orig in dups:
            st.caption(f"↳ skipping {dup.name} (duplicate)")
        if any(d["status"] != "verified" for d in docs):
            st.warning("Some docs unverified — LTV numbers blocked until approved in Verify Documents.")

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Update index", use_container_width=True):
            with st.status("Indexing…", expanded=True) as status:
                sync_index(log=status.write)
                status.update(label="Up to date", state="complete")
            load_retriever.clear()
            st.rerun()
    with c2:
        if st.button("New chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    st.markdown(
        f"<div style='position:fixed;bottom:12px;left:12px;font-size:11px;color:#a09a90;'>"
        f"Model: {config.CHAT_MODEL}<br>by Derrick Garcia</div>",
        unsafe_allow_html=True,
    )

model = config.CHAT_MODEL


# ---------- chat ----------
st.title("Guideline Assistant")

# Mode context bar under title
_ctx_name = choice or ("All programs" if mode_scenario and not mode_matrix else "")
if _ctx_name:
    if mode_matrix and mode_scenario:
        _mc, _ml = "#b89230", f"Program + Scenario  ·  {_ctx_name}"
    elif mode_matrix:
        _mc, _ml = "#b89230", f"Program  ·  {_ctx_name}"
    else:
        _mc, _ml = "#1a2e52", f"Scenario  ·  {_ctx_name}"
    st.markdown(
        f"<div style='font-size:0.68rem;font-weight:600;color:{_mc};letter-spacing:0.14em;"
        f"text-transform:uppercase;margin-bottom:0.75rem;padding-bottom:0.5rem;"
        f"border-bottom:1px solid {_mc}30;'>{_ml}</div>",
        unsafe_allow_html=True,
    )

st.session_state.setdefault("messages", [])

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("sources"):
            _render_sources(m["sources"], answer=m.get("content", ""))


question = st.chat_input("Ask about a guideline, or give me a scenario")
if not question:
    if not st.session_state.messages:
        no_mode = not (mode_guideline or mode_matrix or mode_scenario)
        if no_mode:
            st.info("Select a mode in the sidebar, then ask a question.")
        elif not choice and (mode_matrix or mode_scenario):
            st.info("Select a program for matrix and scenario lookups. Guideline research works without one.")
        else:
            st.info("Ask a guideline question, or describe a scenario: "
                    "loan amount, FICO, occupancy, purpose, property type.")
    st.stop()

# Validate mode selection
if not (mode_guideline or mode_matrix or mode_scenario):
    st.warning("Select at least one mode in the sidebar.")
    st.stop()

if (mode_matrix or mode_scenario) and not choice:
    # Prompt to pick a program instead of refusing
    with st.chat_message("assistant"):
        programs_list = "\n".join(f"- {p}" for p in user_programs)
        msg = (f"I need a specific program to look that up. Which one?\n\n{programs_list}\n\n"
               f"Pick it in the sidebar, then ask again.")
        st.markdown(msg)
    st.stop()

history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
st.session_state.messages.append({"role": "user", "content": question})
with st.chat_message("user"):
    st.markdown(question)

answer = ""
source_hits = []

with st.chat_message("assistant"):

    # --- Scenario mode ---
    if mode_scenario:
        if not choice:
            # All-programs comparison
            try:
                with st.spinner("Checking all programs…"):
                    program_data = []
                    for prog in user_programs:
                        prog_paths, _ = library.unique_docs(all_programs.get(prog, []))
                        shared = all_programs.get(_SHARED, []) if prog in _NONQM else []
                        all_p, _ = library.unique_docs(prog_paths + shared)
                        pdocs = [library.load(p) for p in all_p]
                        mats = library.matrices(pdocs)
                        if mats:
                            program_data.append({
                                "program": prog,
                                "mats": mats,
                                "docs_text": "\n\n".join(library.render(d) for d in pdocs),
                            })
                    comp = compare_programs(question, history, program_data, model)
                answer = _render_comparison(comp)
            except Exception as e:
                st.caption(f"Comparison skipped: {e}")
        elif docs:
            docs_text = "\n\n".join(library.render(d) for d in docs)
            try:
                with st.spinner("Checking the matrix"):
                    scen = scenario_answer(question, history, library.matrices(docs), docs_text, model)
                if scen:
                    st.markdown(scen)
                    answer = scen
            except Exception as e:
                st.caption(f"Scenario check skipped: {e}")

    # --- Matrix / Program full-doc Q&A ---
    if mode_matrix and choice and docs and not answer:
        docs_text = "\n\n".join(library.render(d) for d in docs)
        if len(docs_text) <= config.FULL_DOC_MAX_CHARS:
            answer = st.write_stream(stream_chat(full_doc_messages(question, docs_text, history), model))
        else:
            search_programs = [choice] + ([_SHARED] if choice in _NONQM else [])
            hits = load_retriever().search(question, programs=search_programs)
            answer = st.write_stream(stream_chat(search_messages(question, hits, history), model))
            source_hits = filter_to_cited(answer, hits, model)

    # --- Guideline research ---
    if mode_guideline and not answer:
        # Find guidelines PDFs with a TOC (NonQM Shared and any future guideline docs)
        shared_doc_paths = all_programs.get(_SHARED, [])
        routed = None
        for gpath in shared_doc_paths:
            if gpath.suffix.lower() == ".pdf":
                routed = route_and_load(question, gpath, gpath.name, model)
                if routed:
                    break

        if routed:
            import re as _re
            fname = routed["sections"][0]["file"] if routed["sections"] else ""
            # Accumulate full response, then post-process before rendering
            with st.spinner("Reading guidelines..."):
                chunks = []
                for chunk in stream_chat(full_section_messages(question, routed["text"], history, filename=fname), model):
                    chunks.append(chunk)
                raw = "".join(chunks)
            # Fix "(file, p. X)", make citations italic clickable links to PDF page
            answer = _re.sub(r"\(file,\s*p\.", f"({fname}, p.", raw)
            def _make_link(m):
                inner = m.group(1)
                page_m = _re.search(r"p\.\s*(\d+)", inner)
                if page_m and fname:
                    pg = page_m.group(1)
                    url = f"/app/static/{fname}#page={pg}"
                    return f'<a href="{url}" target="_blank" style="font-size:0.8em;font-style:italic;color:#7a6a40;">({inner})</a>'
                return f'<em style="font-size:0.8em;color:#7a6a40;">({inner})</em>'
            answer = _re.sub(r"\(([^)]+\.pdf[^)]*)\)", _make_link, answer)
            st.markdown(answer, unsafe_allow_html=True)
            source_hits = routed["sections"]
        else:
            # Fallback: chunk retrieval for docs without TOC or no route match
            search_programs = ([choice] if choice else []) + [_SHARED]
            hits = load_retriever().search(question, programs=list(dict.fromkeys(search_programs)))
            if not hits:
                answer = "Nothing indexed yet. Hit Update index in the sidebar first."
                st.markdown(answer)
            else:
                answer = st.write_stream(stream_chat(search_messages(question, hits, history), model))
                source_hits = filter_to_cited(answer, hits, model)

    # Fallback: full doc if matrix mode selected and no answer yet
    if not answer and choice and docs:
        docs_text = "\n\n".join(library.render(d) for d in docs)
        answer = st.write_stream(stream_chat(full_doc_messages(question, docs_text, history), model))

    if source_hits:
        _render_sources(source_hits, answer=answer)

    # When both program modes are active, suggest narrowing down
    all_modes = mode_matrix and mode_scenario
    if all_modes and answer:
        tip_parts = []
        if not choice:
            tip_parts.append("select a specific **Program** in the sidebar")
        if tip_parts:
            tip = "💡 **To get a more precise answer:** " + " and ".join(tip_parts) + ", then use just the **Scenario** or **Matrices / Program** mode."
        else:
            tip = ("💡 **To dial it in further:** use just **Scenario** mode for an exact matrix lookup with overlays, "
                   "or just **Matrices / Program** for a full program read.")
        st.info(tip)

st.session_state.messages.append({
    "role": "assistant",
    "content": answer,
    "sources": source_hits,
})
