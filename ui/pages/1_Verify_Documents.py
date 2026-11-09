import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from guidebot import crosscheck, library
from guidebot.extract_pdf import page_image
from guidebot.matrix_lookup import header_problems
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from style import inject

st.set_page_config(page_title="Verify Documents", layout="wide")
inject()
st.title("Verify documents")
st.caption("Check each page against the original. Fix anything that's off, then approve. "
           "The bot only quotes LTV numbers from approved documents.")


@st.cache_data
def cached_image(path_str: str, page: int, digest: str):
    return page_image(path_str, page)


progs = library.programs()
if not progs:
    st.info("No documents found in the docs folder.")
    st.stop()

c1, c2 = st.columns(2)
prog = c1.selectbox("Program", list(progs))
paths, dups = library.unique_docs(progs[prog])
path = c2.selectbox("Document", paths, format_func=lambda p: p.name)
for dup, orig in dups:
    st.info(f"{dup.name} is an exact copy of {orig.name}. It's being skipped, and you can delete it.")

def problems_by_matrix(pages, file_hash: str) -> list[str]:
    """Header problems plus any disagreement with the Revo site's hand-checked copy."""
    out = []
    refs = iter(crosscheck.check({"hash": file_hash, "pages": pages}))
    for page in pages:
        for m in page["matrices"]:
            name = f"p. {page['page']} {m.attrs.get('title') or 'LTV matrix'}"
            out += [f"{name}: {p}" for p in header_problems(m)]
            ref = next(refs)
            if ref["status"] == "mismatch":
                out += [f"{name}: differs from Revo site ({ref['source']}): {d}" for d in ref["diffs"][:5]]
    return out


just_approved = st.session_state.pop("just_approved", None) == str(path)
reverify_key = f"reverify|{path}"
doc = library.load(path)
status = doc["status"]
saved_problems = problems_by_matrix(doc["pages"], doc["hash"]) if status == "verified" else []

if just_approved:
    st.success("Approved.")

# ── Verified and unchanged: show a compact confirmation, hide the review UI ──
if status == "verified" and not just_approved and not st.session_state.get(reverify_key):
    if saved_problems:
        st.error(f"{path.name} is approved, but its matrices have problems. "
                 "The bot won't quote numbers from it until you re-verify.\n\n"
                 + "\n".join(f"- {p}" for p in saved_problems))
    else:
        st.success(f"✓  {path.name} is verified.")
    if st.button("Re-verify", type="primary" if saved_problems else "secondary"):
        st.session_state[reverify_key] = True
        st.rerun()
    st.stop()

elif status == "changed":
    st.warning("File changed since last approval. Re-check and approve again.")

elif status == "unverified":
    st.info("Not verified yet.")

# Re-verifying: start from the saved version (keeps your edits) or a fresh read with the current parser.
fresh = status == "verified" and st.toggle(
    "Re-read the PDF with the current parser (drops edits you made last time)", value=bool(saved_problems))
if fresh:
    doc = library.load(path, fresh=True)

# ── Full review UI ──
edited = []
refs = iter(crosscheck.check(doc))
for page in doc["pages"]:
    st.divider()
    st.subheader(f"Page {page['page']}")
    left, right = st.columns(2)
    if path.suffix.lower() == ".pdf":
        left.image(cached_image(str(path), page["page"], doc["hash"]), use_container_width=True)
    with right:
        mats = []
        for i, m in enumerate(page["matrices"]):
            st.markdown(f"**Matrix: {m.attrs.get('title') or 'LTV matrix'}**  \nCheck every cell against the PDF.")
            for p in header_problems(m):
                st.error(p)
            ref = next(refs)
            if ref["status"] == "match":
                st.success(f"Matches the Revo site's copy cell for cell ({ref['source']}).")
            elif ref["status"] == "mismatch":
                st.error(f"Differs from the Revo site's copy ({ref['source']}):\n\n"
                         + "\n".join(f"- {d}" for d in ref["diffs"][:8]))
            else:
                st.caption("No Revo site copy of this matrix to cross-check. Check every cell by eye.")
            if m.columns.duplicated().any():
                seen: dict[str, int] = {}
                new_cols = []
                for c in m.columns:
                    seen[c] = seen.get(c, 0) + 1
                    new_cols.append(f"{c} ({seen[c]})" if list(m.columns).count(c) > 1 else c)
                m = m.copy()
                m.columns = new_cols
            e = st.data_editor(m, key=f"m|{path}|{page['page']}|{i}|{doc['hash']}|{fresh}",
                               hide_index=True, use_container_width=True)
            e = e.copy()
            e.attrs = dict(m.attrs)
            mats.append(e)
        text = st.text_area("Text the bot reads", page["text"], height=320,
                            key=f"t|{path}|{page['page']}|{doc['hash']}|{fresh}")
    edited.append({"page": page["page"], "text": text, "matrices": mats})

st.divider()
blocked = problems_by_matrix(edited, doc["hash"])
if blocked:
    st.error("Can't approve yet:\n\n" + "\n".join(f"- {b}" for b in blocked[:10])
             + "\n\nA wrong cell can be fixed in the table above. Wrong headers need a parser fix.")
if st.button("Approve this document", type="primary", disabled=bool(blocked)):
    library.save_verified(path, edited)
    st.session_state.just_approved = str(path)
    st.session_state.pop(reverify_key, None)
    st.rerun()
