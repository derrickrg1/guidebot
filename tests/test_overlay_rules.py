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


def test_unapproved_rules_give_no_final_number(monkeypatch, df):
    # The AI picking overlays here turned a 75% deal into 80% and 70%. Unapproved rules stop at the matrix cell.
    calls = []

    def fake(system, *a, **k):
        calls.append(system)
        return answer.Criteria(is_scenario=True, matrix=0, column="Primary Residence / Purchase", missing=[],
                               dims=[answer.DimValue(name="Loan Amount", value="650000"),
                                     answer.DimValue(name="FICO", value="720")])
    monkeypatch.setattr(answer, "ask_json", fake)
    mats = [{"df": df, "file": "g.pdf", "page": 1, "title": "LTV", "status": "verified"}]
    rules = {"status": "unapproved", "source": "g.pdf", "rules": IGNITE_1099}
    out = answer.scenario_answer("Purchase, primary, sfr, $650,000 loan, 720 FICO", [], mats, "", "m", rules)
    assert calls == [answer.CRITERIA_SYSTEM]               # no AI overlay call
    assert "No final LTV yet" in out and "aren't approved yet" in out and "before overlays" in out
    assert "Max LTV/CLTV" not in out
    with pytest.raises(ValueError):
        answer.checked_overlays("q", [], None, {"ltv": 90}, "", "m", rules)


def test_compare_gives_no_final_number_for_unapproved_rules(monkeypatch, df):
    monkeypatch.setattr(answer, "ask_json", lambda system, *a, **k: (
        answer.RawScenario() if system == answer.RAW_SCENARIO_SYSTEM else
        answer.Criteria(is_scenario=True, matrix=0, column="Primary Residence / Purchase", missing=[],
                        dims=[answer.DimValue(name="Loan Amount", value="650000"),
                              answer.DimValue(name="FICO", value="720")])))
    mats = [{"df": df, "file": "g.pdf", "page": 1, "title": "LTV", "status": "verified"}]
    data = [{"program": "P", "mats": mats, "docs_text": "",
             "rules": {"status": "unapproved", "source": "g.pdf", "rules": IGNITE_1099}}]
    comp = answer.compare_programs("Purchase, primary, sfr, $650,000 loan, 720 FICO", [], data, "m")
    row = comp["results"][0]
    assert row["eligible"] is None and "before overlays" in row["error"] and "max_ltv" not in row


@pytest.mark.parametrize("question, earlier, deal", [
    ("Max LTV for an investment cash out, single family, $1.2m loan, 705 FICO?", None, True),
    ("What's the max LTV for a rate and term refi on a second home, $1,200,000 loan, 690 FICO?", None, True),
    ("Purchase, primary residence, single family, $650,000 loan, 720 FICO", None, True),
    ("what if it's an investment property?", "Purchase, primary, sfr, $650,000 loan, 720 FICO", True),
    ("what if the FICO is 680?", "Purchase, primary, sfr, $650,000 loan, 720 FICO", True),
    ("What are the reserve requirements for a $1,500,000 loan?", None, False),
    ("What's the minimum credit score for co-borrowers?", None, False),
    ("Can a first time home buyer purchase an investment property?", None, False),
    ("What's the max DTI?", "Purchase, primary, sfr, $650,000 loan, 720 FICO", False),
])
def test_deal_questions_are_spotted_by_python(question, earlier, deal):
    history = [{"role": "user", "content": earlier}, {"role": "assistant", "content": "x"}] if earlier else []
    assert answer.looks_like_deal(question, history) is deal


def test_newest_message_wins_on_follow_ups():
    history = [{"role": "user", "content": "Purchase, investment, sfr, $650,000 loan, 720 FICO"},
               {"role": "assistant", "content": "x"}]
    raw = answer.said_scenario("what if it's a primary and 680 FICO?", history)
    assert (raw.fico, raw.occupancy, raw.loan_amount, raw.purpose) == (680, "Primary Residence", 650000, "Purchase")


def test_deal_question_never_reaches_the_free_text_reader(monkeypatch):
    """Program on, Scenario off: a deal goes to the matrix lookup, and if that can't answer, no LTV at all."""
    monkeypatch.setattr(chat.library, "programs", lambda: {"P": []})
    monkeypatch.setattr(chat, "program_docs", lambda c, a: ([{"status": "verified"}], []))
    monkeypatch.setattr(chat.library, "matrices", lambda d: [])
    monkeypatch.setattr(chat.library, "render", lambda d: "")
    monkeypatch.setattr(chat, "scenario_answer", lambda *a, **k: None)
    monkeypatch.setattr(chat, "_stream", lambda *a, **k: pytest.fail("free-text reader was called"))
    events = list(chat.respond("Max LTV for an investment cash out, sfr, $1.2m loan, 705 FICO?", [], "P",
                               mode_program=True, mode_scenario=False))
    assert "won't give an LTV" in events[-1]["replace"]

    monkeypatch.setattr(chat, "scenario_answer", lambda *a, **k: "**Max LTV/CLTV: 75%**")
    events = list(chat.respond("Max LTV for an investment cash out, sfr, $1.2m loan, 705 FICO?", [], "P",
                               mode_program=True, mode_scenario=False))
    assert events[-1]["replace"] == "**Max LTV/CLTV: 75%**"

    # No program picked: the deal is checked against every program instead of the free-text reader.
    monkeypatch.setattr(chat, "compare_programs", lambda *a, **k: [])
    monkeypatch.setattr(chat, "comparison_md", lambda rows: "compared")
    events = list(chat.respond("Max LTV for an investment cash out, sfr, $1.2m loan, 705 FICO?", [], ""))
    assert events[-1]["replace"] == "compared"


@pytest.mark.parametrize("text, facts", [
    ("Purchase, primary, SFR, $650k, 720 FICO, 53% DTI", {"dti": 53.0}),
    ("DTI of 54.5, purchase", {"dti": 54.5}),
    ("cash out, DSCR 1.15", {"dscr": 1.15}),
    ("purchase, 0.9 DSCR", {"dscr": 0.9}),
    ("dscr of .85", {"dscr": 0.85}),
    ("airbnb, vacant", {"short_term_rental": True, "vacant": True}),
    ("first time investor", {"first_time_investor": True}),
    ("non-permanent resident alien", {"non_permanent_resident": True}),
])
def test_dti_dscr_and_investor_facts(text, facts):
    got = o.extract_facts(text)
    assert {k: got.get(k) for k in facts} == facts


def test_numbers_that_cant_be_dti_or_dscr_are_dropped():
    """"DSCR, 720 FICO" must not read as a DSCR of 720."""
    assert "dscr" not in o.extract_facts("dscr, 720 fico")
    assert "dti" not in o.extract_facts("dti, 720 fico")


def _expanded_dti(text):
    rules = json.loads((config.ROOT / "overlays" / "Ignite Bank Statements.json").read_text(encoding="utf-8"))["rules"]
    raw = fill_scenario(RawScenario(), text)
    facts = o.extract_facts(text, raw.occupancy, raw.purpose, raw.fico, raw.loan_amount)
    return {a["id"]: a["value"] for a in o.apply_rules(rules, facts, "x.pdf")["applied"]}


def test_expanded_dti_caps_at_80_and_has_limits():
    assert _expanded_dti("Purchase, primary, SFR, $650k, 720 FICO, 53% DTI") == {"dti_expanded": 80}
    assert "dti_expanded_not_allowed" in _expanded_dti("Cash out, primary, SFR, $650k, 720 FICO, 53% DTI")
    assert "dti_expanded_not_allowed" in _expanded_dti("Purchase, primary, SFR, $650k, 690 FICO, 53% DTI")
    assert "dti_over_55" in _expanded_dti("Purchase, primary, SFR, $650k, 720 FICO, 58% DTI")


@pytest.mark.parametrize("program", sorted(p.stem for p in (config.ROOT / "overlays").glob("*.json")))
def test_every_rule_quote_is_in_its_pdf(program):
    """A rules file whose quotes don't match its PDF word for word is blocked, so catch it here first."""
    docs, _ = chat.program_docs(program, library.programs())
    if not docs:
        pytest.skip("docs/ not present")
    data = o.for_program(program, docs)
    assert data["bad_quotes"] == []
