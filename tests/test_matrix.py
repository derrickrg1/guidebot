import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from guidebot import answer, library
from guidebot.extract_pdf import parse_matrix
from guidebot.matrix_lookup import apply_adjustments, lookup

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
