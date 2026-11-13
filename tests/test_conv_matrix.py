import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import re

import pytest

from guidebot import config, conv_matrix, library
from guidebot.answer import RawScenario
from guidebot.extract_pdf import extract_document

FANNIE = "Conventional Fannie Mae/Fannie-Mae-Matrix.pdf"
FREDDIE = "Conventional Freddie Mac/Freddie-Mac-Matrix.pdf"
HOMEONE = "Conventional HomeOne/Home-One-Matrix.pdf"
REFINOW = "Conventional RefiNow/Fannie-Mae-Refi-Now.pdf"
have_pdfs = pytest.mark.skipif(not all((config.DOCS_DIR / p).exists() for p in (FANNIE, FREDDIE, HOMEONE, REFINOW)),
                               reason="Conventional PDFs not in docs/")


def _mats(rel, status="verified"):
    # through the same JSON round trip the approved copies take
    pages = library._from_json(library._to_json(extract_document(config.DOCS_DIR / rel)))
    return [{"df": m, "file": Path(rel).name, "page": p["page"], "status": status, "reference": None}
            for p in pages for m in p["matrices"]]


def _deal(fico, loan, occ, purpose):
    return RawScenario(fico=fico, loan_amount=loan, occupancy=occ, purpose=purpose)


@pytest.fixture(scope="module")
def fannie():
    return _mats(FANNIE)


@have_pdfs
def test_fannie_grid_rows_limits_and_notes(fannie):
    df = conv_matrix.conv_mats(fannie)[0]["df"]
    assert len(df) == 19
    assert df.attrs["limits"]["2"] == [1081950.0, 1622925.0] and df.attrs["high_label"] == "High Balance"
    two = df[(df["Occupancy"] == "Primary Residence") & (df["Units"] == "2 Units")].iloc[0]
    assert two["Conforming Max LTV"] == "95% 1, 2" and two["High Balance Max LTV"] == "85%"
    # footnotes stop at the color legend: nothing from the loan limits or MI tables below it
    assert "Minimum Loan Amount" not in df.attrs["notes"] and "Actual loan limits" not in df.attrs["notes"]


@have_pdfs
def test_fannie_lookups(fannie):
    cash_out = conv_matrix.result(_deal(680, 500000, "Primary Residence", "Cash Out Refinance"), "", fannie)
    assert cash_out["eligible"] and cash_out["max_ltv"] == 80
    inv = conv_matrix.result(_deal(640, 300000, "Investment Property", "Purchase"), "3 units", fannie)
    assert inv["max_ltv"] == 75
    sfr = conv_matrix.result(_deal(700, 400000, "Primary Residence", "Purchase"), "", fannie)
    assert sfr["max_ltv"] == 97 and "fixed rate only" in sfr["overlays"][0]
    high_2 = conv_matrix.result(_deal(700, 1500000, "Primary Residence", "Purchase"), "duplex", fannie)
    assert high_2["max_ltv"] == 85 and "High Balance" in high_2["overlays"][0]
    assert conv_matrix.result(_deal(600, 400000, "Primary Residence", "Purchase"), "", fannie)["eligible"] is False
    over = conv_matrix.result(_deal(700, 1300000, "Primary Residence", "Purchase"), "", fannie)
    assert over["eligible"] is False and "over the 1-unit limit" in over["error"]
    mh = conv_matrix.result(_deal(700, 900000, "Primary Residence", "Purchase"), "manufactured home", fannie)
    assert mh["eligible"] is False   # manufactured is conforming only (NA high balance)


@have_pdfs
def test_freddie_super_conforming_and_homeone():
    freddie = _mats(FREDDIE)
    res = conv_matrix.result(_deal(700, 1100000, "Primary Residence", "Purchase"), "2 unit", freddie)
    assert res["max_ltv"] == 85 and "Super Conforming" in res["overlays"][0]
    home_one = _mats(HOMEONE)   # one LTV column: must read as Conventional, not as an FHA/VA sheet
    assert conv_matrix.is_conv(home_one)
    assert conv_matrix.result(_deal(700, 400000, "Primary Residence", "Purchase"), "", home_one)["max_ltv"] == 97
    assert conv_matrix.result(_deal(700, 900000, "Primary Residence", "Purchase"), "", home_one)["eligible"] is False


@have_pdfs
def test_refi_now_answer():
    mats = _mats(REFINOW)
    out = conv_matrix.answer(_deal(680, 500000, "Primary Residence", "Rate/Term Refinance"), "", mats)
    assert "**Max LTV/CLTV: 97%**" in out and "max DTI 65%" in out and "105% TLTV" in out
    assert out.count("2: Loans with resubordinated") == 1   # nested duplicate cell dropped
    no = conv_matrix.answer(_deal(680, 500000, "Primary Residence", "Purchase"), "", mats)
    assert "Not eligible" in no


@have_pdfs
def test_unverified_gives_no_number():
    mats = _mats(REFINOW, status="pending")
    out = conv_matrix.answer(_deal(680, 500000, "Primary Residence", "Rate/Term Refinance"), "", mats)
    assert "%" not in out.split("approve")[0].replace("Refi-Now", "")
    assert conv_matrix.result(_deal(680, 500000, "Primary Residence", "Rate/Term Refinance"), "", mats)["eligible"] is None


def test_ltv_cells():
    assert conv_matrix.ltv_of("95¹%") == (95.0, ["1"], "")
    assert conv_matrix.ltv_of("95% 1, 2")[:2] == (95.0, ["1", "2"])
    assert conv_matrix.ltv_of("Fixed 97%/105%¹,²") == (97.0, ["1", "2"], "fixed rate only; 105% TLTV")
    assert conv_matrix.ltv_of("NA")[0] is None


@have_pdfs
def test_footnotes_read_like_the_sheet():
    ready = conv_matrix.conv_mats(_mats("Conventional HomeReady/Home-Ready-Matrix.pdf"))[0]["df"]
    assert "3: ARM Max LTV 1 Unit is 95%" in conv_matrix.footnotes(ready.attrs["notes"])   # not a new footnote 1
    refi = conv_matrix.conv_mats(_mats("Conventional Refi Possible/Freddie-Mac-Program-RefiPossible.pdf"))[0]["df"]
    notes = conv_matrix.footnotes(refi.attrs["notes"])
    # the "*Minimum Fico" box sits beside footnote 2's two lines on the page
    assert "2: Loans with resubordinated Affordable Seconds max TLTV of 105%" in notes
    assert notes[-1].startswith("*Minimum Fico requirements determined by LPA")


@have_pdfs
def test_answer_cites_the_exact_row(fannie):
    # "1 | 75% | 75%" is on the page under Second Home and Investment: the chip carries this row's place
    out = conv_matrix.answer(_deal(700, 300000, "Investment Property", "Cash Out Refinance"), "", fannie)
    mark = re.search(r"\(Fannie-Mae-Matrix\.pdf, p\. 1, mark ([\d. ]+)\)", out)
    assert mark
    x0, y0, x1, y1, cx0, _, cx1, _ = map(float, mark[1].split())
    df = conv_matrix.conv_mats(fannie)[0]["df"]
    second = df.index[(df["Occupancy"] == "Second Home") & (df["Loan Purpose"] == "Cash-out")][0]
    assert df.attrs["marks"][second][1] > y1   # Second Home's "1 | 75%" row is higher on the page
    assert x0 <= cx0 < cx1 <= x1
    res = conv_matrix.result(_deal(700, 300000, "Investment Property", "Cash Out Refinance"), "", fannie)
    assert res["cite"] == mark[0]


@pytest.mark.parametrize("text, purpose", [
    ("680 FICO, $350k limited cash-out refi, investment, fourplex", "Rate/Term Refinance"),
    ("no cash-out refi, 700 FICO", "Rate/Term Refinance"),
    ("no cash out refinance", "Rate/Term Refinance"),
    ("680 FICO, $500k cash-out refi, primary", "Cash Out Refinance"),
    ("rate and term refi", "Rate/Term Refinance"),
])
def test_limited_cash_out_is_rate_term(text, purpose):
    from guidebot.answer import said_scenario
    assert said_scenario(text, []).purpose == purpose


@have_pdfs
def test_fannie_limited_cash_out_investment_fourplex(fannie):
    from guidebot.answer import said_scenario
    raw = said_scenario("680 FICO, $350k limited cash-out refi, investment, fourplex", [])
    res = conv_matrix.result(raw, "680 FICO, $350k limited cash-out refi, investment, fourplex", fannie)
    assert res["max_ltv"] == 75   # the Limited Cash-Out 1-4 row, not Cash-Out 2-4 (70%)
