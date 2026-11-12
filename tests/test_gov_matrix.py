import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from guidebot import config, crosscheck, gov_matrix, library, site_grids
from guidebot.answer import RawScenario
from guidebot.extract_pdf import extract_document

GOV_PDFS = ["VA/VA Standard Matrix.pdf", "VA/VA Cash-Out Matrix.pdf", "VA/VA IRRRL Matrix.pdf",
            "FHA/FHA Standard Matrix.pdf", "FHA/FHA Streamline Matrix.pdf"]
have_pdfs = pytest.mark.skipif(not all((config.DOCS_DIR / p).exists() for p in GOV_PDFS), reason="FHA/VA PDFs not in docs/")


def _table(rows, title="VA Standard Matrix · Primary Residence", notes=""):
    df = pd.DataFrame(rows, columns=["Loan Purpose", "Minimum FICO", "Maximum Loan Amount", "Max LTV"])
    df.attrs.update(kind="gov", title=title, dims=["Loan Purpose", "Minimum FICO", "Maximum Loan Amount"], notes=notes)
    return df


VA = [["Purchase", "680", "$2,500,000", "100%¹"], ["Purchase", "600", "$1,500,000", "100%¹"],
      ["Purchase", "550", "$832,750", "100%¹"], ["Cash-Out", "600", "$1,500,000", "90.0%²"],
      ["Cash-Out", "550", "$832,750", "90.0%²"]]


def _mats(df, status="verified", ref="match"):
    return [{"df": df, "file": "VA Standard Matrix.pdf", "page": 1, "status": status,
             "reference": {"status": ref, "source": "va/va-standard-matrix.html", "diffs": ["Row 1: x"]}}]


def _first_matrix(rel):
    return next(m for p in extract_document(config.DOCS_DIR / rel) for m in p["matrices"])


@have_pdfs
def test_va_standard_rows_placed_by_position():
    # "Cash-Out 550" and "$832,750" come out of pdfplumber as separate rows under a merged 90.0%; the merged
    # "Purchase" covers three FICO rows
    df = _first_matrix("VA/VA Standard Matrix.pdf")
    assert df.attrs["kind"] == "gov" and df.attrs["title"] == "VA Standard Matrix · Primary Residence"
    got = df[["Loan Purpose", "Minimum FICO", "Maximum Loan Amount", "Max LTV"]].values.tolist()
    assert got == VA
    assert gov_matrix.notes_md(df.attrs["notes"])[1].startswith("2: LTV/CLTV may NOT be exceeded by Funding Fee")


@have_pdfs
def test_fha_standard_and_va_cash_out_columns():
    fha = _first_matrix("FHA/FHA Standard Matrix.pdf")
    assert "Maximum Loan Amount" not in fha.columns and "Minimum Loan Amount" in fha.columns
    assert fha["Max LTV"].tolist() == ["96.5%, 100%³", "90.0%", "97.75%², 100%³", "90.0%", "80.0%"]
    assert fha["Loan Purpose"].tolist() == ["Purchase", "Purchase", "No Cash-Out", "No Cash-Out", "Cash-Out"]
    assert [n[:2] for n in gov_matrix.notes_md(fha.attrs["notes"])] == ["1:", "2:", "3:"]
    co = _first_matrix("VA/VA Cash-Out Matrix.pdf")
    assert co[["Minimum FICO", "Max DTI", "AUS"]].values.tolist()[3] == ["700³", "50%³", "Manual UW"]
    assert co["Max LTV"].tolist()[-1] == "95%" and len(co) == 8


@have_pdfs
@pytest.mark.skipif(not config.SITE_REPO.exists(), reason="Revo site repo not on this PC")
def test_every_gov_table_matches_the_site():
    for rel in GOV_PDFS:
        path = config.DOCS_DIR / rel
        doc = {"path": path, "hash": library.file_hash(path), "pages": extract_document(path)}
        assert [r["status"] for r in crosscheck.check(doc)] == ["match"], rel


def test_gov_compare_catches_a_changed_cell():
    site = [dict(zip(["Loan Purpose", "Minimum FICO", "Maximum Loan Amount", "Max LTV"], r)) for r in
            [["Purchase", "680", "$2,500,000", "100%¹"], ["Cash-Out", "600", "$1,500,000", "90.0%²"]]]
    mine = _table([["Purchase", "680", "$2,500,000", "100%¹"], ["Cash-out .", "600", "1,500,000", "90.0%²"]])
    assert site_grids.gov_compare(mine, site) == []                      # case, $ and stray periods don't count
    mine.iloc[1, 3] = "95.0%²"
    assert site_grids.gov_compare(mine, site) == ["Row 2: Max LTV parsed 95.0%² vs site 90.0%²"]


def test_ltv_cells():
    assert gov_matrix.ltv_of("100%¹") == (100, [])
    assert gov_matrix.ltv_of("96.5%, 100%³") == (96.5, ["100%³"])    # 100% is 203(h) only: never the headline
    assert gov_matrix.ltv_of("90.01- 100%¹,2") == (100, [])
    assert gov_matrix.ltv_of("NA¹") == (None, [])


def test_lookup_needs_fico_and_loan_amount_to_fit():
    df = _table(VA)
    res = gov_matrix.lookup(df, 640, 900_000, "Purchase", "Primary Residence")
    assert [r["row"]["Minimum FICO"] for r in res["rows"]] == ["600"]   # 680 row: FICO; 550 row: over $832,750
    assert gov_matrix.lookup(df, 590, 900_000, "Purchase", None)["why"] == "the loan amount is over the max for this FICO"
    assert "under the lowest" in gov_matrix.lookup(df, 540, 300_000, "Cash Out Refinance", None)["why"]
    assert gov_matrix.lookup(df, 700, 300_000, "Purchase", "Investment Property")["why"].endswith("Primary Residence only")
    assert "no Rate/Term Refinance rows" in gov_matrix.lookup(df, 700, 300_000, "Rate/Term Refinance", None)["why"]


def test_no_cash_out_is_rate_term_not_cash_out():
    assert gov_matrix._purpose("No Cash-Out") == "Rate/Term Refinance"
    assert gov_matrix._purpose("Cash-out .") == "Cash Out Refinance"
    assert gov_matrix._purpose("VA IRRRL") == gov_matrix._purpose("Streamlines") == "Streamline"


def test_answer_blocks_unverified_and_mismatched():
    raw = RawScenario(fico=700, loan_amount=400_000, purpose="Purchase", occupancy="Primary Residence")
    df = _table(VA)
    assert "100%" not in gov_matrix.answer(raw, "", _mats(df, status="unverified"))
    assert "100%" not in gov_matrix.answer(raw, "", _mats(df, ref="mismatch"))
    out = gov_matrix.answer(raw, "", _mats(df))
    assert out.startswith("**Max LTV/CLTV: 100%**") and "| **100%¹** |" in out
    assert gov_matrix.result(raw, "", _mats(df))["max_ltv"] == 100
    assert gov_matrix.result(raw, "", _mats(df, status="unverified"))["eligible"] is None


def test_streamline_question_reads_streamline_rows():
    df = _table([["VA IRRRL", "0", "$1,500,000", "NA¹"], ["VA IRRRL", "550", "$2,000,000", "NA¹"]],
                title="VA IRRRL Matrix · Primary Residence")
    raw = RawScenario(fico=600, loan_amount=1_800_000, purpose="Rate/Term Refinance")
    out = gov_matrix.answer(raw, "VA IRRRL 600 FICO $1.8m", _mats(df))
    assert out.startswith("**Eligible. No max LTV on this matrix (NA)**") and "FICO 550+" in out


def test_kind_and_notes_survive_approval_roundtrip():
    df = _table(VA, notes="¹Funding fee note")
    back = library._from_json(library._to_json([{"page": 1, "text": "", "matrices": [df]}]))[0]["matrices"][0]
    assert back.attrs["kind"] == "gov" and back.attrs["notes"] == "¹Funding fee note"
    assert gov_matrix.is_gov([{"df": back}])
