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


def test_rule_question_is_not_a_deal():
    from guidebot.answer import asks_rule, looks_like_deal
    deals = [{"role": "user", "content": "Fannie Mae: 700 FICO, $845,000 purchase, primary, SFR"},
             {"role": "assistant", "content": "**Max LTV/CLTV: 97%**"}]
    q = "Are temporary buydowns allowed on a cash-out refi?"
    assert asks_rule(q) and not looks_like_deal(q, deals)
    assert looks_like_deal("what if it's a cash-out?", deals)   # a real follow-up still is
    assert not asks_rule("700 FICO, $400k cash-out, is that allowed on a fourplex?")   # numbers: it's a deal


def test_new_deal_does_not_borrow_the_last_loan_amount():
    from guidebot.answer import said_scenario
    hist = [{"role": "user", "content": "Freddie Mac: 700 FICO, $2.5M purchase, primary, fourplex"},
            {"role": "assistant", "content": "Not eligible"}]
    raw = said_scenario("Fannie Mae: 700 FICO, purchase, primary", hist)
    assert raw.loan_amount is None and "loan_amount" in raw.missing
    assert said_scenario("what if it's 680 FICO?", hist).loan_amount == 2_500_000   # follow-ups still carry


@pytest.mark.parametrize("text, loan, occ", [
    ("borrower has a 712 score, buying a primary for $850k with 20% down", 680_000, "Primary Residence"),
    ("value is $1M, they want a $650k cash-out refi on their primary, FICO 681", 650_000, "Primary Residence"),
    ("appraised at $1,500,000, loan amount $975k", 975_000, None),
    ("buying a $900k home with $180k down, 740 fico", 720_000, None),
    ("buying a duplex to live in, 704 fico, $640k loan", 640_000, "Primary Residence"),
    ("max ltv for 700 at 1.5m primary purchase", 1_500_000, "Primary Residence"),
    ("refi for $400k, primary, 700", 400_000, "Primary Residence"),
    ("value is $1M, primary cash out, 700 FICO", None, "Primary Residence"),   # a value alone isn't the loan
])
def test_deal_reader_tells_loan_from_price(text, loan, occ):
    from guidebot.answer import fill_scenario
    raw = fill_scenario(RawScenario(), text)
    assert (raw.loan_amount, raw.occupancy) == (loan, occ)


def test_nonqm_matrix_chip_marks_row_and_cell():
    import pdfplumber
    from guidebot.answer import matrix_cite
    from guidebot.matrix_lookup import lookup
    docs = [library.load(p) for p in library.programs().get("Ignite 1099", [])]
    if not docs:
        pytest.skip("no Ignite 1099 docs")
    m = library.matrices(docs)[0]
    res = lookup(m["df"], {"Loan Amount": "1200000", "FICO": "705"}, "Investment Property / Cash Out")
    cite = matrix_cite(m, res)
    nums = [float(x) for x in cite.split("mark ")[1].rstrip(")").split()]
    with pdfplumber.open(m["path"]) as pdf:
        page = pdf.pages[m["page"] - 1]
        h = page.height
        row = page.crop((nums[0], h - nums[3], nums[2], h - nums[1])).extract_text()
        cell = page.crop((nums[4], h - nums[7], nums[6], h - nums[5])).extract_text()
    assert row.split()[0] == "700" and cell == res["cell"]


def _overview(question, *progs):
    from guidebot import ltv_overview
    mats = {p: library.matrices([library.load(x) for x in library.programs().get(p, [])]) for p in progs}
    if not all(mats.values()):
        pytest.skip("program docs missing")
    return ltv_overview.overview(question, mats)


def test_ltv_question_without_a_deal_is_answered_from_the_grid():
    from guidebot import ltv_overview
    assert ltv_overview.asks_ltv("Max LTV for a 3-unit investment cash-out on Freddie?")
    assert not ltv_overview.asks_ltv("max ltv for 700 FICO, $500k loan, primary purchase")   # a deal: matrix lookup
    assert not ltv_overview.asks_ltv("Are temporary buydowns allowed on a cash-out refi?")
    out = _overview("Max LTV for a 3-unit investment cash-out on Freddie?", "Conventional Freddie Mac")
    assert "Investment Property, Cash-Out, 2-4 Units" in out and "Conforming: **70%**" in out and ", mark " in out
    assert out.count("| Investment") == 1


def test_ltv_overview_reads_the_dscr_band_and_footnotes():
    out = _overview("What's the max LTV for a cash-out with a DSCR of 0.9?", "Express DSCR")
    assert "| Cash Out | **70%** (down to 60%)" in out
    out = _overview("What's the CLTV limit with a community second on Fannie?", "Conventional Fannie Mae")
    assert out.index("up to 105%") < out.index("| Row |")   # the footnote answers it, so it leads


def test_quotes_not_on_the_cited_page_lose_their_quote_marks():
    from guidebot.chat import _unquote_unfound
    if not library.programs().get("Ignite 1099"):
        pytest.skip("no Ignite 1099 docs")
    real = 'BK: "36 months OR 24 months with a 5% reduction" (Ignite 1099 Expanded.pdf, p. 3)'
    fake = 'BK: "no bankruptcies ever allowed on this sheet" (Ignite 1099 Expanded.pdf, p. 3)'
    assert _unquote_unfound(real) == real
    assert _unquote_unfound(fake) == "BK: no bankruptcies ever allowed on this sheet (Ignite 1099 Expanded.pdf, p. 3)"
