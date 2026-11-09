import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from guidebot import library
from guidebot.extract_pdf import page_image
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

just_approved = st.session_state.pop("just_approved", None) == str(path)
doc = library.load(path)
status = doc["status"]

if just_approved:
    st.success("Approved.")

# ── Verified and unchanged: show a compact confirmation, hide the review UI ──
if status == "verified" and not just_approved:
    st.success(f"✓  {path.name} is verified.")
    if not st.button("Re-verify"):
        st.stop()
    # If they clicked Re-verify, fall through to the full review UI below

elif status == "changed":
    st.warning("File changed since last approval — re-check and approve again.")

elif status == "unverified":
    st.info("Not verified yet.")

# ── Full review UI ──
edited = []
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
            if m.columns.duplicated().any():
                seen: dict[str, int] = {}
                new_cols = []
                for c in m.columns:
                    seen[c] = seen.get(c, 0) + 1
                    new_cols.append(f"{c} ({seen[c]})" if list(m.columns).count(c) > 1 else c)
                m = m.copy()
                m.columns = new_cols
            e = st.data_editor(m, key=f"m|{path}|{page['page']}|{i}|{doc['hash']}",
                               hide_index=True, use_container_width=True)
            e = e.copy()
            e.attrs = dict(m.attrs)
            mats.append(e)
        text = st.text_area("Text the bot reads", page["text"], height=320,
                            key=f"t|{path}|{page['page']}|{doc['hash']}")
    edited.append({"page": page["page"], "text": text, "matrices": mats})

st.divider()
if st.button("Approve this document", type="primary"):
    library.save_verified(path, edited)
    st.session_state.just_approved = str(path)
    st.rerun()
