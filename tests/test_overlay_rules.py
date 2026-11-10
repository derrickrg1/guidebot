import json

import pytest

from guidebot import answer, chat, config, library, overlay_rules as o
from guidebot.answer import RawScenario, fill_scenario

from test_matrix import df  # noqa: F401  (shared NDM-shaped matrix fixture)

IGNITE_1099 = json.loads((config.ROOT / "overlays" / "Ignite 1099.json").read_text(encoding="utf-8"))["rules"]


def run(text):
    raw = fill_scenario(RawScenario(), text)
    facts = o.extract_facts(text, raw.occupancy, raw.purpose, raw.fico, raw.loan_amount)
    out = o.apply_rules(IGNITE_1099, facts, "Ignite 1099 Expanded.pdf")
    return facts, {a["id"]: a["value"] for a in out["applied"]}, out


@pytest.mark.parametrize("text, applied", [
    # the three deals checked by hand against the PDF
    ("Purchase, primary residence, single family, $650,000 loan, 720 FICO", {}),
    ("Purchase, primary residence, 2 unit, $650,000 loan, 720 FICO", {"two_to_four_units": 85}),
    ("Purchase, investment property, 2 unit, $650,000 loan, 720 FICO", {"two_to_four_units": 80}),
    ("cash out, primary, 2 unit, $500k, 700 fico", {"two_to_four_units": 80}),
    ("purchase investment non-warrantable condo 740 fico 400k", {"non_warrantable_condo": 80}),
    ("purchase second home condotel 740 fico 400k", {"condotel": 80}),
    ("purchase primary sfr, rural, 760 fico 500k", {"rural": 80}),
    ("purchase investment sfr interest only 760 fico 500k", {"interest_only": 80}),
    ("purchase primary sfr, declining market, 760 fico 500k", {"declining_market": 5}),
    ("purchase investment sfr in Baltimore, MD 740 fico 300k", {"geo_investment": 0}),
    ("purchase primary sfr in Baltimore, MD 740 fico 300k", {}),
    ("cash out primary sfr, BK discharged 30 months ago, 720 fico 400k", {"derog_24": 5}),
    ("cash out primary sfr, foreclosure 18 months ago, 720 fico 400k", {"derog_under_24": 0}),
    ("cash out primary sfr, owned 4 months, 720 fico 400k", {"cash_out_seasoning": 5}),
    ("purchase primary sfr, first time buyer living rent free, 660 fico 400k",
     {"fthb_rent_free": 80, "fthb_rent_free_fico": 0}),
    ("purchase investment sfr, first time home buyer, 740 fico 400k", {"fthb_investment": 0}),
    ("purchase primary sfr with a non-occupant co-borrower, 740 fico 400k", {"non_occupant": 5}),
    ("cash out primary sfr with a non-occupant co-borrower, 740 fico 400k",
     {"non_occupant": 5, "non_occupant_not_allowed": 0}),
    ("Texas 50(a)(6) cash out, primary sfr, 740 fico 300k", {"texas_a6": 80}),
])
def test_ignite_1099_rules(text, applied):
    assert run(text)[1] == applied


def test_unsaid_facts_are_never_assumed():
    facts, applied, out = run("Purchase, primary residence, condo, $500,000 loan, 740 FICO")
    assert applied == {}                                   # plain "condo" isn't assumed warrantable
    assert any("which kind of condo" in u for u in out["unknown"])
    assert any(u.startswith("If Rural") for u in out["unknown"])
    assert run("purchase primary sfr, not rural, 740 fico 500k")[0]["rural"] is False


def test_investment_star_footnote_is_flagged():
    assert o.STAR_NOTE in run("Purchase, investment property, 2 unit, $650,000 loan, 720 FICO")[2]["confirm"]
    assert not run("Purchase, primary residence, 2 unit, $650,000 loan, 720 FICO")[2]["confirm"]


def test_ignite_1099_quotes_are_in_the_pdf():
    progs = library.programs()
    if "Ignite 1099" not in progs:
        pytest.skip("Ignite 1099 PDF not present")
    docs, _ = chat.program_docs("Ignite 1099", progs)
    rules = o.for_program("Ignite 1099", docs)
    assert rules["bad_quotes"] == []


def test_approval_follows_rules_and_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(o, "RULES_DIR", tmp_path)
    monkeypatch.setattr(o, "APPROVED_DIR", tmp_path)
    (tmp_path / "P.json").write_text(json.dumps({"program": "P", "source": "p.pdf", "rules": [
        {"id": "r", "label": "Rural", "page": 1, "quote": "Rural 80%", "when": {"rural": True}, "kind": "cap", "value": 80}]}))
    doc = {"hash": "abc", "pages": [{"text": "Rural | 80%"}]}
    assert o.load("P", doc)["status"] == "unapproved"
    o.approve("P", o.load("P", doc))
    assert o.load("P", doc)["status"] == "approved"
    assert o.load("P", {**doc, "hash": "new pdf"})["status"] == "changed"
    assert o.load("P", {"hash": "abc", "pages": [{"text": "Rural | 85%"}]})["status"] == "bad_quotes"


def test_approved_rules_replace_the_ai_overlay_step(monkeypatch, df):
    calls = []

    def fake(system, *a, **k):
        calls.append(system)
        return answer.Criteria(is_scenario=True, matrix=0, column="Primary Residence / Purchase", missing=[],
                               dims=[answer.DimValue(name="Loan Amount", value="650000"),
                                     answer.DimValue(name="FICO", value="720")])
    monkeypatch.setattr(answer, "ask_json", fake)
    mats = [{"df": df, "file": "Ignite 1099 Expanded.pdf", "page": 1, "title": "LTV", "status": "verified"}]
    rules = {"status": "approved", "source": "Ignite 1099 Expanded.pdf", "rules": IGNITE_1099}
    out = answer.scenario_answer("Purchase, primary residence, 2 unit, $650,000 loan, 720 FICO", [], mats, "", "m", rules)
    assert calls == [answer.CRITERIA_SYSTEM]               # no AI overlay call
    assert "Max LTV/CLTV: 85%" in out and "approved rules" in out and "If Rural" in out


def test_unapproved_rules_fall_back_to_ai_with_a_warning(monkeypatch, df):
    replies = iter([
        answer.Criteria(is_scenario=True, matrix=0, column="Primary Residence / Purchase", missing=[],
                        dims=[answer.DimValue(name="Loan Amount", value="650000"),
                              answer.DimValue(name="FICO", value="720")]),
        answer.Adjustments(notes=[], adjustments=[]),
    ])
    monkeypatch.setattr(answer, "ask_json", lambda *a, **k: next(replies))
    mats = [{"df": df, "file": "g.pdf", "page": 1, "title": "LTV", "status": "verified"}]
    rules = {"status": "unapproved", "source": "g.pdf", "rules": IGNITE_1099}
    out = answer.scenario_answer("Purchase, primary, sfr, $650,000 loan, 720 FICO", [], mats, "", "m", rules)
    assert "aren't approved yet" in out and "Max LTV/CLTV: 90%" in out
