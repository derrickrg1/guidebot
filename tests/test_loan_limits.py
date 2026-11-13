"""County loan limits: FHFA's file and the Census ZIP file, read by Python. NDM's sheets ran ahead of FHFA in
November 2026 ($845,000 baseline vs FHFA's $832,750), and the sheet wins."""
from guidebot import conv_matrix, loan_limits as L
from tests.test_conv_matrix import FANNIE, _deal, _mats

SHEET = [845000, 1267500]


def test_counties_by_name_state_and_zip():
    assert [c["how"] for c in L.counties("purchase in Maricopa County, AZ")] == ["Maricopa County, AZ"]
    assert [c["how"] for c in L.counties("Kings County, New York")] == ["Kings County, NY"]
    assert len(L.counties("Orange County")) > 3   # every state with one, so the answer lists them
    assert [c["how"] for c in L.counties("Scottsdale, AZ 85251")] == ["ZIP 85251: Maricopa County, AZ"]
    assert L.counties("loan amount 75000") == []   # a bare 5-digit number isn't a ZIP


def test_2026_figures():
    assert (L.year(), L.baseline(1), L.ceiling(1), L.ceiling(4)) == (2026, 832750, 1249125, 2402625)


def test_baseline_county_uses_the_sheet_limit():
    fits = L.conforming_check("Maricopa County, AZ", 840000, 1, SHEET)
    assert not fits["over"] and "fits (conforming)" in fits["lines"][0]
    over = L.conforming_check("Maricopa County, AZ", 900000, 1, SHEET)
    assert over["over"] and "jumbo" in over["lines"][0]


def test_high_cost_county_is_high_balance_up_to_its_limit():
    assert "fits (High Balance)" in L.conforming_check("Los Angeles County", 1200000, 1, SHEET)["lines"][0]
    assert L.conforming_check("Los Angeles County", 1260000, 1, SHEET)["over"]


def test_no_county_over_the_baseline_asks_for_it():
    lines = L.conforming_check("", 900000, 1, SHEET)["lines"]
    assert "Name the county or ZIP" in lines[0]


def test_fha_floor_and_ceiling():
    assert "every county" in L.fha_lines("", 500000, 1)[0]
    assert "over the FHA limit in every" in L.fha_lines("", 1300000, 1)[0]
    assert "HUD's lookup" in L.fha_lines("Maricopa County, AZ", 600000, 1)[0]


def test_limit_question_answered_from_the_file():
    out = L.answer("What's the conforming loan limit in Honolulu County?")
    assert "| Honolulu County, HI | $1,249,125 |" in out
    assert L.answer("What is the max DTI on HomeReady?") is None


def test_conventional_deal_over_the_county_limit_is_not_eligible():
    mats = _mats(FANNIE)
    out = conv_matrix.answer(_deal(760, 900000, "Primary Residence", "Purchase"),
                             "760 FICO $900,000 purchase primary in Maricopa County, AZ", mats)
    assert out.startswith("**Not eligible: over the county's conforming loan limit**")
    ok = conv_matrix.answer(_deal(760, 900000, "Primary Residence", "Purchase"),
                            "760 FICO $900,000 purchase primary in Los Angeles County, CA", mats)
    assert ok.startswith("**Max LTV/CLTV:") and "fits (High Balance)" in ok
