import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from guidebot import answer, config, crosscheck, extract_pdf, library
from guidebot.extract_pdf import extract_document, parse_matrices, parse_matrix
from guidebot.matrix_lookup import apply_adjustments, header_problems, lookup

N = None
# Shape copied from how pdfplumber actually returns an NDM matrix: merged cells come back as None,
# loan amount only on the first row of each block, and highlighted rows shift a column.
RAW = [
    ["", "Program Maximum LTV/CLTVs", N, N, "", "Primary Residence", N, N, "", "Investment Property", N, N, ""],
    ["", "Loan Amount", "", "FICO", "", "Purchase", "", "Cash Out", "", "Purchase", "", "Cash Out", ""],
    ["< $1,000,000", N, "720", N, "90%", N, "80%", N, "85%", N, "80%", N, N],
    [N, N, "680", N, "90%", N, "80%", N, "80%", N, "75%", N, N],
    [N, N, "", "620", "80%", "", "70%", "", "N/A", "", "N/A", "", ""],
    ["$1,000,001 -\n$1,500,000", N, "720", N, "90%", N, "80%", N, "85%", N, "80%", N, N],
    [N, N, "700", N, "90%", N, "80%", N, "80%", N, "75%", N, N],
    [N, N, "660", N, "80%", N, "75%", N, "75%", N, "70%", N, N],
]


@pytest.fixture
def df():
    return parse_matrix(RAW)


def test_parse_fills_down_and_combines_headers(df):
    assert list(df.columns) == ["Loan Amount", "FICO", "Primary Residence / Purchase", "Primary Residence / Cash Out",
                                "Investment Property / Purchase", "Investment Property / Cash Out"]
    assert df.attrs["title"] == "Program Maximum LTV/CLTVs"
    assert df.iloc[2].tolist() == ["< $1,000,000", "620", "80%", "70%", "N/A", "N/A"]
    assert df.iloc[4]["Loan Amount"] == "$1,000,001 - $1,500,000"


def test_regular_table_is_not_a_matrix():
    rows = [["Rural", "80%", "75%"], ["Condo", "90%*", "80%*"], ["Min Loan", "$150,000", ""]]
    assert parse_matrix(rows) is None


# Shape from the FHA Standard guidelines (p. 10): topic | sub-topic | wrapped text, topic merged down.
FHA_RAW = [
    ["Multiple FHA Loan\nExceptions Con't", "Non-\nOccupying\nCo-\nBorrower",
     "A non-occupying co-Borrower may qualify for another FHA-insured Mortgage.\n"
     "A Borrower with an existing FHA-insured Mortgage may qualify as a co-\nBorrower."],
    ["Resale Restrictions\n(Flip Transactions)",
     "Time Restriction on Transfers of Title - The eligibility of a Property for a\nMortgage is set by time.\n"
     "Resales Occurring Between 91 Days and 180 Days After Acquisition\n"
     "A second appraisal must be obtained if:\n• the resale date is between 91 and 180 Days\n"
     "following acquisition; and\n• the resale price is 100 percent or more over the price.\n"
     "The cost of the second appraisal may not be charged to the Borrower.", N],
]


def test_guideline_table_grouped_by_topic():
    text = extract_pdf._table_rows_text(FHA_RAW)
    assert text.split("\n") == [
        "### Multiple FHA Loan Exceptions Con't",
        "- Non-Occupying Co-Borrower: A non-occupying co-Borrower may qualify for another FHA-insured Mortgage.",
        "  - A Borrower with an existing FHA-insured Mortgage may qualify as a co-Borrower.",
        "",
        "### Resale Restrictions (Flip Transactions)",
        "- Time Restriction on Transfers of Title - The eligibility of a Property for a Mortgage is set by time.",
        "- Resales Occurring Between 91 Days and 180 Days After Acquisition",
        "  - A second appraisal must be obtained if:",
        "    - the resale date is between 91 and 180 Days following acquisition; and",
        "    - the resale price is 100 percent or more over the price.",
        "  - The cost of the second appraisal may not be charged to the Borrower.",
    ]


def test_o_bullets_nest_under_their_lead_in():
    # Ignite puts the "o" glyph on its own line after each item; VA puts it in front.
    rows = [["Income", "Allowable assets:\nChecking= 100%\no\nStocks= 90%\no"],
            ["Entitlement", "For loans over $144,000:\no Veterans with Full Entitlement: 25%."]]
    assert extract_pdf._table_rows_text(rows).split("\n") == [
        "### Income",
        "- Allowable assets:",
        "  - Checking= 100%",
        "  - Stocks= 90%",
        "",
        "### Entitlement",
        "- For loans over $144,000:",
        "  - Veterans with Full Entitlement: 25%.",
    ]


def test_grid_by_position_fills_merged_cells_and_splits_group_headers():
    # Ignite p. 2 overlay box: "Max LTV/CLTV" over "Purch/R&T | Cash Out", a note spanning both
    # columns, and a footnote stacked under one cell that must not become its own row.
    cells = [
        ((272, 81, 387, 131), "Property Overlays"), ((387, 81, 508, 94), "Max LTV/CLTV"),
        ((387, 94, 452, 131), "Purch/R&T"), ((452, 94, 508, 131), "Cash Out"),
        ((272, 131, 387, 144), "Rural"), ((387, 131, 452, 144), "80%"), ((452, 131, 508, 144), "75%"),
        ((272, 144, 387, 168), "Condo"), ((387, 144, 452, 160), "90%*"), ((452, 144, 508, 168), "80%*"),
        ((387, 160, 452, 168), "see note"),
        ((272, 168, 387, 181), "Declining Markets"), ((387, 168, 508, 181), "5% reduction from max LTV"),
    ]
    assert extract_pdf._layout_text(cells).split("\n") == [
        "| Property Overlays | Max LTV/CLTV Purch/R&T | Max LTV/CLTV Cash Out |",
        "|---|---|---|",
        "| Rural | 80% | 75% |",
        "| Condo | 90%* see note | 80%* |",
        "| Declining Markets | 5% reduction from max LTV | 5% reduction from max LTV |",
    ]


@pytest.mark.skipif(not (config.DOCS_DIR / "FHA/FHA Standard Matrix.pdf").exists(), reason="FHA PDF not in docs/")
def test_fha_grid_keeps_footnotes_off_the_numbers():
    text = extract_document(config.DOCS_DIR / "FHA/FHA Standard Matrix.pdf")[0]["text"]
    # "5801" in the raw PDF is FICO 580 with footnote 1; $75,000 is one cell merged down every row
    assert "| Purchase | 580¹ | 1 – 4 Units³, Manufactured | 96.5%, 100%³ | $75,000 |" in text
    assert "| Cash-Out | 550 | 1 – 4 Units³, Manufactured | 80.0% | $75,000 |" in text
    assert "5801" not in text
    assert "| 4 Unit | $2,402,625 |" in text          # loan limits panel, not mixed in with the AUS box


def test_lookup_rounds_fico_down(df):
    r = lookup(df, {"Loan Amount": "1200000", "FICO": "705"}, "Investment Property / Cash Out")
    assert r["ltv"] == 75 and "700 row" in r["trace"][1]


def test_lookup_na_is_ineligible(df):
    r = lookup(df, {"Loan Amount": "500000", "FICO": "630"}, "Investment Property / Purchase")
    assert r["ok"] and not r["eligible"]


def test_lookup_below_lowest_tier(df):
    r = lookup(df, {"Loan Amount": "1.2mm", "FICO": "650"}, "Primary Residence / Purchase")
    assert not r["ok"] and "below the lowest tier" in r["error"]


def test_lookup_bracket_edge_warns(df):
    r = lookup(df, {"Loan Amount": "$1,000,000", "FICO": "720"}, "Primary Residence / Purchase")
    assert r["ltv"] == 90 and r["notes"]


def test_adjustments_cap_then_reduce():
    calc = apply_adjustments(85, [
        {"kind": "cap", "value": 80, "reason": "rural"},
        {"kind": "reduction", "value": 5, "reason": "declining market"},
        {"kind": "reduction", "value": 5, "reason": "mortgage lates"},
    ])
    assert calc["final"] == 70 and calc["stacked"]


def test_verify_roundtrip_and_change_detection(tmp_path, monkeypatch, df):
    monkeypatch.setattr(library, "VERIFIED_DIR", tmp_path / "verified")
    docs = tmp_path / "docs" / "Ignite 1099"
    docs.mkdir(parents=True)
    f = docs / "guide.md"
    f.write_text("Reserves 6 months.")
    doc = library.load(f, tmp_path / "docs")
    assert doc["status"] == "unverified"

    pages = [{"page": 1, "text": "Reserves 6 months.", "matrices": [df]}]
    library.save_verified(f, pages, tmp_path / "docs")
    doc = library.load(f, tmp_path / "docs")
    assert doc["status"] == "verified"
    assert doc["pages"][0]["matrices"][0].attrs["dims"] == ["Loan Amount", "FICO"]

    f.write_text("Reserves 9 months.")
    assert library.load(f, tmp_path / "docs")["status"] == "changed"


def test_duplicates_skipped(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    keep, dups = library.unique_docs([a, b])
    assert keep == [a] and dups == [(b, a)]


def test_scenario_uses_python_math_and_checks_quotes(monkeypatch, df):
    replies = iter([
        answer.Criteria(is_scenario=True, matrix=0, column="Investment Property / Purchase", missing=[],
                        dims=[answer.DimValue(name="Loan Amount", value="900000"),
                              answer.DimValue(name="FICO", value="725")]),
        answer.Adjustments(notes=["Reserves: 6 months (guide.pdf, p. 2)"], adjustments=[
            answer.Adjustment(reason="2-4 units", kind="cap", value=80, file="guide.pdf", page=2,
                              quote="2 - 4 Units 85%* 80%*"),
            answer.Adjustment(reason="investment footnote", kind="reduction", value=5, file="guide.pdf", page=2,
                              quote="Reduce LTV by 5% from maximum on Investment Properties"),
        ]),
    ])
    monkeypatch.setattr(answer, "ask_json", lambda *a, **k: next(replies))
    mats = [{"df": df, "file": "guide.pdf", "page": 1, "title": "LTV", "status": "verified"}]
    text = "2 - 4 Units | 85%* | 80%*\n*Reduce LTV by 5% from maximum on Investment Properties"
    out = answer.scenario_answer("investment purchase 2 unit 725 fico 900k", [], mats, text, "m")
    assert "Max LTV/CLTV: 75%" in out
    assert out.count("✅") == 2


def test_unverified_matrix_blocks_numbers(monkeypatch, df):
    monkeypatch.setattr(answer, "ask_json", lambda *a, **k: answer.Criteria(
        is_scenario=True, matrix=0, column="x", missing=[], dims=[]))
    mats = [{"df": df, "file": "guide.pdf", "page": 1, "title": "LTV", "status": "unverified"}]
    assert "hasn't been verified" in answer.scenario_answer("q", [], mats, "", "m")


# ---------- one test per matrix layout seen in the real NDM PDFs ----------

# Ignite Express DSCR: title cell sits in the same row as the two DSCR groups.
# Old bug: read as 3 groups of 2, shifting every DSCR label one column.
DSCR_RAW = [
    ["", "Program Maximum LTV/CLTVs", N, N, N, "", "DSCR > 1.00", N, N, N, N, "", "DSCR .75 to .99", N, N, N, ""],
    ["", "Loan Amount", "", "FICO", "", "Purchase", N, "Rate & Term", "", "Cash Out", "", "Purchase", N, "Rate & Term", "", "Cash Out", ""],
    ["$100,000 to\n$1,000,000", N, "720", N, "85%", N, "85%", N, "80%", N, "80%", N, "80%", N, "70%", N, N],
    [N, N, "700", N, "80%", N, "80%", N, "80%", N, "75%", N, "75%", N, "70%", N, N],
    [N, N, "660", N, "75%", N, "75%", N, "75%", N, "N/A", N, "N/A", N, "N/A", N, N],
]


def test_dscr_title_cell_not_read_as_group():
    df = parse_matrix(DSCR_RAW)
    assert list(df.columns)[2:] == [
        "DSCR > 1.00 / Purchase", "DSCR > 1.00 / Rate & Term", "DSCR > 1.00 / Cash Out",
        "DSCR .75 to .99 / Purchase", "DSCR .75 to .99 / Rate & Term", "DSCR .75 to .99 / Cash Out"]
    assert df.attrs["title"] == "Program Maximum LTV/CLTVs"
    assert lookup(df, {"Loan Amount": "500000", "FICO": "720"}, "DSCR .75 to .99 / Purchase")["ltv"] == 80
    assert lookup(df, {"Loan Amount": "500000", "FICO": "720"}, "DSCR > 1.00 / Purchase")["ltv"] == 85
    assert header_problems(df) == []


# NonQM P&L: title, date, and all three occupancy groups merged into one cell.
PL_RAW = [
    ["NONQM PROFIT & LOSS ONLY Effective Date: 3.2.2026\nProgram Maximum LTV/CLTVs Primary Second Home* Investment Property*",
     N, N, N, N, N, N, N, N, N, N, N],
    ["Loan Amount", N, "FICO", "Purchase", "Rate & Term", "Cash Out", "Purchase", "Rate & Term", "Cash Out",
     "Purchase", "Rate & Term", "Cash Out"],
    ["≤ $1,000,000", N, "700+", "80%", "75%", "70%", "80%", "75%", "70%", "80%", "75%", "70%"],
    [N, N, "680", "80%", "75%", "70%", "80%", "75%", "70%", "80%", "75%", "70%"],
    [N, N, "660", "75%", "75%", "70%", "75%", "75%", "70%", "70%", "70%", "65%"],
]


def test_merged_title_and_groups_cell():
    df = parse_matrix(PL_RAW)
    assert list(df.columns)[2:5] == ["Primary / Purchase", "Primary / Rate & Term", "Primary / Cash Out"]
    assert list(df.columns)[-1] == "Investment Property* / Cash Out"
    assert lookup(df, {"Loan Amount": "800000", "FICO": "665"}, "Investment Property* / Cash Out")["ltv"] == 65
    assert header_problems(df) == []


# NonQM Premier Full Doc: two matrices in one table, the second one's only row squashed into a
# single cell, and the overlay/requirements grid below them in the same table.
FULL_DOC_RAW = [
    ["", "NON QM PREMIER FULL DOC", N, N, N, "Effective Date: 6.15.2023", N, N],
    ["Program Max LTV/CLTVs Primary Second Home Investment Property", N, N, N, N, N, N, N],
    ["Loan Amount", "FICO", "Purch. & R/T", "Cash Out", "Purch. & R/T", "Cash Out", "Purch. & R/T", "Cash Out"],
    ["≤ $1,000,000", "700+", "75%", "70%", "70%", "65%", "70%", "65%"],
    [N, "680", "70%", "65%", "65%", "60%", "65%", "60%"],
    ["$1,000,001 - $1,500,000", "700", "75%", "70%", "70%", "65%", "70%", "65%"],
    ["", N, "Full Doc with Asset Utilization", N, N, N, N, N],
    ["Program Max LTVs/CLTVs", N, "Primary", N, "Second Home", N, "Investment Property", N],
    ["Loan Amount", "FICO", "Purch. & R/T", "Cash Out", "Purch. & R/T", "Cash Out", "Purch. & R/T", "Cash Out"],
    ["≤ $2,000,000 700 70% 65% 65% 60% 60% 55%", N, N, N, N, N, N, N],
    ["Program Requirements", N, "Property Overlays", N, "Max LTV/CLTV", N, "Products", N],
    ["Min Loan Amount", "$250,000", "Low Rise Condos", N, "75%", N, "30 Year Fixed", N],
]


def test_two_matrices_in_one_table_and_overlays_kept():
    mats, rest = parse_matrices(FULL_DOC_RAW)
    assert len(mats) == 2
    main, asset = mats
    assert list(main.columns)[2] == "Primary / Purch. & R/T"
    assert asset.attrs["title"].startswith("Full Doc with Asset Utilization")
    assert asset.iloc[0].tolist() == ["≤ $2,000,000", "700", "70%", "65%", "65%", "60%", "60%", "55%"]
    assert lookup(asset, {"Loan Amount": "1500000", "FICO": "740"}, "Investment Property / Cash Out")["ltv"] == 55
    text = " ".join(c for r in rest for c in r if c)
    assert "Low Rise Condos" in text and "Min Loan Amount" in text
    assert "Full Doc with Asset Utilization" not in text  # consumed as the matrix title


def test_header_problems_catch_old_bad_parses():
    shifted = pd.DataFrame([["< $1,000,000", "720", "85%", "85%", "80%", "80%"]], columns=[
        "Loan Amount", "FICO", "Program Maximum LTV/CLTVs / Purchase", "Program Maximum LTV/CLTVs / Rate & Term",
        "DSCR > 1.00 / Cash Out", "DSCR > 1.00 / Purchase"])
    shifted.attrs["dims"] = ["Loan Amount", "FICO"]
    assert header_problems(shifted)
    numbered = pd.DataFrame([["< $1,000,000", "720", "80%", "75%"]],
                            columns=["Loan Amount", "FICO", "Purchase (1)", "Purchase (2)"])
    numbered.attrs["dims"] = ["Loan Amount", "FICO"]
    assert any("tacked on" in p for p in header_problems(numbered))


def test_bad_headers_block_numbers(monkeypatch):
    bad = parse_matrix(DSCR_RAW)
    bad.columns = ["Loan Amount", "FICO"] + [f"Purchase ({i})" for i in range(1, 7)]
    monkeypatch.setattr(answer, "ask_json", lambda *a, **k: answer.Criteria(
        is_scenario=True, matrix=0, column="Purchase (1)", missing=[],
        dims=[answer.DimValue(name="Loan Amount", value="500000"), answer.DimValue(name="FICO", value="720")]))
    mats = [{"df": bad, "file": "dscr.pdf", "page": 1, "title": "LTV", "status": "verified"}]
    out = answer.scenario_answer("q", [], mats, "", "m")
    assert "won't quote numbers" in out and "%" not in out.split("\n")[0]


# ---------- against the real PDFs (skipped when docs/ isn't present) ----------

REAL = {
    "Ignite Express DSCR/Ignite Express DSCR Expanded.pdf": (1, "DSCR .75 to .99 / Purchase", "80%"),
    "NonQM P&L/NonQM-Profit-Loss-Only-Matrix.pdf": (1, "Investment Property* / Cash Out", "70%"),
    "NonQM Full Doc/NonQM-Express-Full-Doc-Matrix.pdf": (1, "Primary / Purchase", "*90%"),
    "NonQM Express DSCR/NonQM-Express-DSCR-Matrix.pdf": (2, "Purchase", "80%"),
    "Ignite 1099/Ignite 1099 Expanded.pdf": (1, "Investment Property / Cash Out", None),
    "NonQM 1099/NonQM-1099-Matrix.pdf": (1, "Primary / Purchase", None),
    "NonQM Bank Statements/NonQM-Bank-Statements-Matrix.pdf": (1, "Primary / Purchase", None),
}


@pytest.mark.parametrize("rel", REAL)
def test_real_pdf_headers(rel):
    path = config.DOCS_DIR / rel
    if not path.exists():
        pytest.skip(f"{rel} not in docs/")
    n, col, first = REAL[rel]
    mats = [m for p in extract_document(path) for m in p["matrices"]]
    assert len(mats) == n
    for m in mats:
        assert header_problems(m) == [], rel
    assert col in mats[0].columns
    if first:
        assert mats[0][col].iloc[0] == first


# NonQM Express DSCR: second DSCR tier starts with its own title row and no repeated label row.
EX_DSCR_RAW = [
    ["NON QM EXPRESS DSCR", "Effective Date: 8.17.2026"],
    ["Full Doc Only\nProgram Max LTVs/CLTVs DSCR > 1.00", N],
    ["Loan Amount", "FICO", "Purchase", "Rate and Term", "Cash Out"],
    ["$100,000* to $1,000,000", "720", "80%", "80%", "75%"],
    [N, "700", "80%", "75%", "75%"],
    ["$1,000,001 - $1,500,000", "720", "80%", "75%", "75%"],
    ["Program Max LTVs/CLTVs", "DSCR .75 TO .99**"],
    ["$150,000 to $1,000,000", "700", "75%", "75%", "70%"],
    [N, "680", "70%", "70%", "60%"],
]


def test_dscr_tiers_split_into_two_matrices():
    mats, _ = parse_matrices(EX_DSCR_RAW)
    assert len(mats) == 2
    ge, lt = mats
    assert "DSCR > 1.00" in ge.attrs["title"] and "DSCR .75 TO .99" in lt.attrs["title"]
    assert len(ge) == 3 and len(lt) == 2
    assert list(lt.columns) == ["Loan Amount", "FICO", "Purchase", "Rate and Term", "Cash Out"]
    assert lookup(lt, {"Loan Amount": "500000", "FICO": "690"}, "Cash Out")["ltv"] == 60


# NonQM Express Full Doc: "Loan Amount" lands on the first data row and that row's values sit one row up.
EX_FD_RAW = [
    ["NON QM FULL DOC Effective Date: 3.2.2026\nProgram Maximum LTV/CLTVs Primary Second Home Investment Property",
     N, N, N, N, N, N, N, N, N, N],
    ["", "FICO", "Purchase", "Rate & Term", "Cash Out", "Purchase", "Rate & Term", "Cash Out", "Purchase", "Rate & Term", "Cash Out"],
    ["Loan Amount", "", "*90%", "90%", "80%", "85%", "85%", "75%", "85%", "80%", "75%"],
    ["≤ $1,000,000", "740", N, N, N, N, N, N, N, N, N],
    [N, "720", "90%", "85%", "80%", "85%", "85%", "75%", "85%", "80%", "75%"],
    [N, "700", "90%", "85%", "80%", "85%", "85%", "75%", "85%", "80%", "75%"],
]


def test_displaced_loan_amount_label_and_starred_cell():
    df = parse_matrix(EX_FD_RAW)
    assert df.attrs["dims"] == ["Loan Amount", "FICO"]
    assert df.iloc[0].tolist()[:4] == ["≤ $1,000,000", "740", "*90%", "90%"]
    r = lookup(df, {"Loan Amount": "900000", "FICO": "745"}, "Primary / Purchase")
    assert r["ltv"] == 90 and any("footnote" in n for n in r["notes"])


def _site(tmp_path, grid_src):
    site = tmp_path / "site"
    (site / "originals").mkdir(parents=True)
    (site / "originals" / "full-doc-express.pdf").write_bytes(b"ndm sheet")
    # the f-string with a backslash is why the builder can't go through ast on Python 3.11
    builder = "\n".join(['NA = "N/A"', "EXPRESS = " + grid_src, r"""x = f'{"a" if 1 else "\""}'"""])
    (site / "build_full_doc.py").write_text(builder, encoding="utf-8")
    return site


GRID_SRC = """[
    ("≤ $1,000,000", [
        ("740", "*90%", "90%", "80%", "85%", "85%", "75%", "85%", "80%", "75%"),
        ("720", "90%", "85%", "80%", "85%", "85%", "75%", "85%", "80%", "75%"),
        ("700", "90%", "85%", "80%", "85%", "85%", "75%", "85%", "80%", "75%")]),
]"""


def test_crosscheck_match_mismatch_and_other_version(tmp_path):
    import hashlib
    df = parse_matrix(EX_FD_RAW)
    doc = {"hash": hashlib.sha256(b"ndm sheet").hexdigest(), "pages": [{"page": 1, "text": "", "matrices": [df]}]}
    crosscheck._original_hashes.cache_clear(); crosscheck.site_grid.cache_clear()
    assert crosscheck.check(doc, _site(tmp_path / "a", GRID_SRC))[0]["status"] == "match"

    crosscheck._original_hashes.cache_clear(); crosscheck.site_grid.cache_clear()
    bad = crosscheck.check(doc, _site(tmp_path / "b", GRID_SRC.replace('("720", "90%", "85%"', '("720", "90%", "80%"')))
    assert bad[0]["status"] == "mismatch" and "Row 2" in bad[0]["diffs"][0]

    other = dict(doc, hash="0" * 64)   # a newer NDM sheet: no reference, not a false match
    assert crosscheck.check(other, tmp_path / "a" / "site")[0]["status"] == "none"


def test_crosscheck_mismatch_blocks_numbers(monkeypatch, df):
    monkeypatch.setattr(answer, "ask_json", lambda *a, **k: answer.Criteria(
        is_scenario=True, matrix=0, column="Primary Residence / Purchase", missing=[],
        dims=[answer.DimValue(name="Loan Amount", value="500000"), answer.DimValue(name="FICO", value="720")]))
    mats = [{"df": df, "file": "g.pdf", "page": 1, "title": "LTV", "status": "verified",
             "reference": {"status": "mismatch", "source": "build_x.py X", "diffs": ["Row 1: ..."]}}]
    out = answer.scenario_answer("q", [], mats, "", "m")
    assert "doesn't match the Revo site" in out and "Max LTV" not in out


def test_unverified_docs_forbid_ltv_in_full_doc_answers():
    assert answer.NO_LTV_RULE in answer.full_doc_messages("q", "text", [], ltv_ok=False)[0]["content"]
    assert answer.NO_LTV_RULE not in answer.full_doc_messages("q", "text", [], ltv_ok=True)[0]["content"]


def test_real_pdfs_match_revo_site():
    if not config.SITE_MATRICES.exists():
        pytest.skip("Revo site repo not next to guidebot")
    checked = 0
    for prog, paths in library.programs().items():
        keep, _ = library.unique_docs(paths)
        for p in keep:
            if p.suffix != ".pdf" or "Guidelines" in p.name:
                continue
            for r in crosscheck.check(library.load(p, fresh=True)):
                assert r["status"] != "mismatch", (p.name, r["diffs"][:3])
                checked += r["status"] == "match"
    if checked == 0:
        pytest.skip("no docs/ PDFs with a site reference")
    assert checked >= 7
