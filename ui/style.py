"""Inject Revolution Funding brand styles into any Streamlit page."""
import streamlit as st

# Palette: warm cream paper, navy + gold accents (matches NDM SOP aesthetic)
_INK   = "#1a1814"   # warm near-black — primary text
_STEEL = "#4a4640"   # warm gray-brown — secondary text
_MIST  = "#a09a90"   # warm sand — muted / captions
_RULE  = "#d8d2c6"   # hairline rules
_NAVY  = "#1a2e52"   # brand navy — titles, stripes
_GOLD  = "#b89230"   # brand gold — labels, accents
_PAGE  = "#f4f1eb"   # warm cream — page background
_SIDE  = "#ede9e1"   # slightly darker cream — sidebar

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');

/* ── Base ── */
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stApp"],
[data-testid="stMain"],
[data-testid="block-container"] {
    background-color: #f4f1eb !important;
    color: #1a1814 !important;
    font-family: 'Inter', 'Helvetica Neue', Helvetica, sans-serif !important;
    font-weight: 400 !important;
}
p, span, div, li, label {
    color: #1a1814 !important;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background-color: #ede9e1 !important;
    border-right: 1px solid #d8d2c6 !important;
}
[data-testid="stSidebar"] * {
    color: #1a1814 !important;
    font-weight: 400 !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #b89230 !important;
    font-weight: 600 !important;
    font-size: 0.65rem !important;
    letter-spacing: 0.22em !important;
    text-transform: uppercase !important;
}

/* ── Headings ── */
h1 {
    color: #1a2e52 !important;
    font-weight: 600 !important;
    font-size: 1.4rem !important;
    letter-spacing: 0.05em !important;
    text-transform: uppercase !important;
}
h2 {
    color: #4a4640 !important;
    font-weight: 400 !important;
    font-size: 0.9rem !important;
    letter-spacing: 0.07em !important;
    text-transform: uppercase !important;
}
h3 {
    color: #b89230 !important;
    font-weight: 500 !important;
    font-size: 0.72rem !important;
    letter-spacing: 0.15em !important;
    text-transform: uppercase !important;
}

/* ── Inputs — underline only ── */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stSidebar"] [data-testid="stTextInput"] input {
    background: transparent !important;
    background-color: transparent !important;
    border: none !important;
    border-bottom: 1px solid #a09a90 !important;
    border-radius: 0 !important;
    color: #1a1814 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 400 !important;
    padding: 0.4rem 0 !important;
    transition: border-color 0.2s !important;
    box-shadow: none !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stSidebar"] [data-testid="stTextInput"] input:focus {
    border-bottom: 1px solid #b89230 !important;
    box-shadow: none !important;
    outline: none !important;
    background: transparent !important;
    background-color: transparent !important;
}
/* Kill any wrapper background too */
[data-testid="stTextInput"] > div,
[data-testid="stTextInput"] > div > div,
.react-aria-TextField > div {
    background: transparent !important;
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

/* ── Selectbox ── */
.react-aria-ComboBox [role="group"],
.stSelectbox [role="group"] {
    background: transparent !important;
    border: none !important;
    border-bottom: 0.5px solid #d8d2c6 !important;
    border-radius: 0 !important;
    color: #1a1814 !important;
}
.react-aria-ComboBox input,
.stSelectbox input {
    background: transparent !important;
    color: #1a1814 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 300 !important;
    border: none !important;
}
[data-testid="stSelectboxVirtualDropdown"],
.react-aria-Popover,
[role="listbox"] {
    background-color: #f4f1eb !important;
    border: 0.5px solid #d8d2c6 !important;
    border-radius: 0 !important;
    box-shadow: 0 4px 12px rgba(26,24,20,0.08) !important;
}
[role="option"]:hover, [role="option"][data-focused="true"] {
    background-color: rgba(184,146,48,0.08) !important;
}

/* ── Chat input ── */
[data-testid="stChatInput"],
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] > div > div {
    background: transparent !important;
    background-color: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;
}
[data-testid="stChatInput"] textarea {
    background: transparent !important;
    border: none !important;
    border-bottom: 0.5px solid #d8d2c6 !important;
    border-radius: 0 !important;
    color: #1a1814 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 300 !important;
}
[data-testid="stChatInput"] textarea:focus {
    border-bottom: 1px solid #b89230 !important;
    box-shadow: none !important;
}
.stBottom, .stBottom > div {
    background: #f4f1eb !important;
    background-color: #f4f1eb !important;
    border-top: 0.5px solid #d8d2c6 !important;
}

/* ── Buttons ── */
.stButton > button {
    background: transparent !important;
    border: 1px solid #a09a90 !important;
    border-radius: 0 !important;
    color: #1a1814 !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.7rem !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
    padding: 0.45rem 1.1rem !important;
    transition: all 0.15s !important;
}
.stButton > button:hover {
    border-color: rgba(184,146,48,0.5) !important;
    color: #b89230 !important;
    background: transparent !important;
}
.stButton > button[kind="primary"] {
    border-color: #b89230 !important;
    color: #b89230 !important;
}

/* ── Alerts — hairline left border, no fill ── */
.stAlert, .stAlertContainer,
[data-testid="stAlert"],
[data-baseweb="notification"] {
    background: transparent !important;
    background-color: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;
}
.stAlertContainer {
    background-color: transparent !important;
    background: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    border-left: 2px solid #d8d2c6 !important;
    padding: 0.4rem 0 0.4rem 0.9rem !important;
    color: #4a4640 !important;
}
div[class*="stSuccess"] .stAlertContainer { border-left: 2px solid #b89230 !important; }
div[class*="stWarning"]  .stAlertContainer { border-left: 2px solid #6e4400 !important; }
div[class*="stInfo"]     .stAlertContainer { border-left: 2px solid #1a2e52 !important; }
div[class*="stError"]    .stAlertContainer { border-left: 2px solid #7a1818 !important; }
.stAlertContainer, .stAlertContainer p, .stAlertContainer span, .stAlertContainer div {
    color: #1a1814 !important;
}

/* ── Chat messages ── */
[data-testid="stChatMessage"] {
    background: transparent !important;
    border: none !important;
    border-top: 0.5px solid #d8d2c6 !important;
    border-radius: 0 !important;
    padding: 1rem 0 !important;
    margin: 0 !important;
}

/* ── Data editor ── */
[data-testid="stDataEditor"],
.stDataFrame {
    border: none !important;
    border-top: 0.5px solid #d8d2c6 !important;
    border-radius: 0 !important;
}
[data-testid="stDataEditor"] th {
    background: transparent !important;
    border-bottom: 0.5px solid #d8d2c6 !important;
    color: #b89230 !important;
    font-size: 0.65rem !important;
    letter-spacing: 0.1em !important;
    text-transform: uppercase !important;
    font-weight: 500 !important;
}
[data-testid="stDataEditor"] td {
    background: transparent !important;
    border-bottom: 0.5px solid #ede9e1 !important;
    color: #4a4640 !important;
}

/* ── Dividers ── */
hr {
    border: none !important;
    border-top: 0.5px solid #d8d2c6 !important;
    margin: 1.5rem 0 !important;
}

/* ── Captions ── */
[data-testid="stCaptionContainer"], small, .stCaption {
    color: #4a4640 !important;
    font-size: 0.72rem !important;
    font-weight: 400 !important;
    letter-spacing: 0.03em !important;
}

/* ── Spinner ── */
[data-testid="stSpinner"] { color: #b89230 !important; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 2px; height: 2px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(184,146,48,0.3); }

/* ── Layout ── */
[data-testid="block-container"] {
    padding-top: 2rem !important;
    padding-bottom: 2rem !important;
    max-width: 860px !important;
}

/* ── Sidebar compact ── */
[data-testid="stSidebar"] hr {
    margin: 0.5rem 0 !important;
}
[data-testid="stSidebar"] .element-container {
    margin-bottom: 0 !important;
}
[data-testid="stSidebar"] [data-testid="stCheckbox"] {
    padding: 0.05rem 0 !important;
    min-height: unset !important;
}
[data-testid="stSidebar"] [data-testid="stCheckbox"] label p {
    font-size: 0.8rem !important;
}
[data-testid="stSidebar"] .stMarkdown p {
    margin-bottom: 0.2rem !important;
}
</style>
"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)
