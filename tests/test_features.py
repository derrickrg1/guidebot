"""The 2026-11-16 batch: answer cache, Revo rules, MISMO import, change feed, regression trigger, share links,
analytics and the What if deal read."""
import base64
import json

import pytest

from guidebot import analytics, answer_cache, attachments, history, mismo, regress, revo_rules, shares, updates


@pytest.fixture(autouse=True)
def temp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB", tmp_path / "chats.db")
    monkeypatch.setattr(regress, "DIR", tmp_path / "regress")
    monkeypatch.setattr(regress, "RUNS", tmp_path / "regress" / "runs.json")
    monkeypatch.setattr(regress, "LAST", tmp_path / "regress" / "last.json")
    monkeypatch.setattr(regress, "SEEN", tmp_path / "regress" / "seen.json")
    monkeypatch.setattr(updates, "CHANGES", tmp_path / "agency_changes.jsonl")


# ---------- answer cache ----------

def test_same_question_same_key_any_spacing_or_case():
    k = lambda q: answer_cache.key(q, "Ignite 1099", "NonQM", True, False, "m", fp="x")
    assert k("Max DTI on Ignite 1099?") == k("  max dti on ignite 1099 ")
    assert k("Max DTI?") != answer_cache.key("Max DTI?", "Ignite P&L", "NonQM", True, False, "m", fp="x")
    assert k("Max DTI?") != answer_cache.key("Max DTI?", "Ignite 1099", "NonQM", True, False, "m", fp="y")   # guides changed


def test_clean_answer_is_cached_and_replayed():
    evs = [{"status": "Reading…"}, {"text": "50%."}, {"sources": [{"file": "a.pdf"}]}]
    assert list(answer_cache.caching(iter(evs), "k1", "Max DTI?")) == evs
    assert answer_cache.get("k1") == [{"text": "50%."}, {"sources": [{"file": "a.pdf"}]}]
    assert list(answer_cache.replay(answer_cache.get("k1")))[0] == {"cached": True}


def test_backup_or_error_answers_are_not_cached():
    list(answer_cache.caching(iter([{"fallback": True}, {"text": "x"}]), "k2", "q"))
    list(answer_cache.caching(iter([{"text": "x"}, {"error": "boom"}]), "k3", "q"))
    assert answer_cache.get("k2") is None and answer_cache.get("k3") is None


def test_marked_wrong_drops_the_cached_answer():
    answer_cache.put("k4", "Max DTI?", [{"text": "60%."}])
    assert answer_cache.forget("max dti") == 1
    assert answer_cache.get("k4") is None


# ---------- Revo rules ----------

def test_revo_rule_needs_scope_keyword_match():
    assert revo_rules.save({"scope": "Ignite", "topic": "Condos", "keywords": "condo, non-warrantable",
                            "rule": "Revo needs a condo questionnaire."}, "d@x.com")["ok"]
    assert revo_rules.save({"scope": "*", "topic": "x", "keywords": "", "rule": "y"}, "d")["error"]
    assert revo_rules.matching("Is a condo OK?", "Ignite 1099", "NonQM")
    assert not revo_rules.matching("Is a condo OK?", "Express 1099", "NonQM")     # other program
    assert not revo_rules.matching("Is a condominium OK?", "Ignite 1099", "NonQM")  # whole words only
    out = list(revo_rules.adding(iter([{"text": "Answer."}]), "condo?", "Ignite 1099", "NonQM"))
    assert "Revo overlay" in out[-1]["text"] and "condo questionnaire" in out[-1]["text"]


def test_revo_rule_by_tab_edit_and_delete():
    r = revo_rules.save({"scope": "FHA", "topic": "Gifts", "keywords": ["gift"], "rule": "Gift letter on Revo form."}, "d")["rule"]
    assert revo_rules.matching("gift funds?", "", "FHA") and not revo_rules.matching("gift funds?", "", "VA")
    revo_rules.save(r | {"rule": "Changed."}, "d")
    assert [x["rule"] for x in revo_rules.load()] == ["Changed."]
    assert revo_rules.delete(r["id"])["ok"] and revo_rules.load() == []


# ---------- MISMO ----------

MISMO = b"""<?xml version="1.0" encoding="UTF-8"?>
<MESSAGE xmlns="http://www.mismo.org/residential/2009/schemas"><DEAL_SETS><DEAL_SET><DEALS><DEAL>
 <COLLATERALS><COLLATERAL><SUBJECT_PROPERTY><ADDRESS><PostalCode>92101-1234</PostalCode><StateCode>CA</StateCode></ADDRESS>
  <PROPERTY_DETAIL><FinancedUnitCount>1</FinancedUnitCount><PropertyEstimatedValueAmount>820000</PropertyEstimatedValueAmount>
  <PropertyUsageType>PrimaryResidence</PropertyUsageType></PROPERTY_DETAIL>
  <SALES_CONTRACTS><SALES_CONTRACT><SALES_CONTRACT_DETAIL><SalesContractAmount>800000</SalesContractAmount></SALES_CONTRACT_DETAIL></SALES_CONTRACT></SALES_CONTRACTS>
 </SUBJECT_PROPERTY></COLLATERAL></COLLATERALS>
 <LOANS><LOAN><TERMS_OF_LOAN><BaseLoanAmount>650000.00</BaseLoanAmount><LoanPurposeType>Purchase</LoanPurposeType></TERMS_OF_LOAN></LOAN></LOANS>
 <PARTIES>
  <PARTY><INDIVIDUAL><NAME><FirstName>Pat</FirstName></NAME></INDIVIDUAL><ROLES><ROLE><BORROWER><CREDIT_SCORES>
   <CREDIT_SCORE><CREDIT_SCORE_DETAIL><CreditScoreValue>742</CreditScoreValue></CREDIT_SCORE_DETAIL></CREDIT_SCORE>
   <CREDIT_SCORE><CREDIT_SCORE_DETAIL><CreditScoreValue>720</CreditScoreValue></CREDIT_SCORE_DETAIL></CREDIT_SCORE>
   <CREDIT_SCORE><CREDIT_SCORE_DETAIL><CreditScoreValue>705</CreditScoreValue></CREDIT_SCORE_DETAIL></CREDIT_SCORE>
  </CREDIT_SCORES></BORROWER></ROLE></ROLES></PARTY>
  <PARTY><ROLES><ROLE><BORROWER><CREDIT_SCORES>
   <CREDIT_SCORE><CREDIT_SCORE_DETAIL><CreditScoreValue>760</CreditScoreValue></CREDIT_SCORE_DETAIL></CREDIT_SCORE>
   <CREDIT_SCORE><CREDIT_SCORE_DETAIL><CreditScoreValue>780</CreditScoreValue></CREDIT_SCORE_DETAIL></CREDIT_SCORE>
  </CREDIT_SCORES></BORROWER></ROLE></ROLES></PARTY>
 </PARTIES>
</DEAL></DEALS></DEAL_SET></DEAL_SETS></MESSAGE>"""


def test_mismo_file_becomes_the_deal_sentence():
    f = mismo.facts(MISMO)
    assert f["fico"] == 720 and f["borrower_scores"] == [720, 760]   # middle of three, lower of two, then the lowest
    s = mismo.sentence(f)
    assert s.startswith("Loan file (MISMO): 720 FICO, $650,000 loan amount, purchase price $800,000, primary residence, purchase")
    assert "ZIP 92101" in s and "Pat" not in s   # no names out of the file


def test_mismo_refi_and_rejects():
    xml = (b"<MESSAGE><DEAL><BaseLoanAmount>400000</BaseLoanAmount><LoanPurposeType>Refinance</LoanPurposeType>"
           b"<RefinanceCashOutDeterminationType>CashOut</RefinanceCashOutDeterminationType>"
           b"<PropertyUsageType>Investment</PropertyUsageType><FinancedUnitCount>2</FinancedUnitCount></DEAL></MESSAGE>")
    s = mismo.read(xml)
    assert "cash-out refinance" in s and "investment property" in s and "2-unit" in s and "Not in the file: credit score" in s
    with pytest.raises(mismo.NotMismo):
        mismo.facts(b"<!DOCTYPE x [<!ENTITY a 'b'>]><MESSAGE/>")
    with pytest.raises(mismo.NotMismo):
        mismo.facts(b"<html><body/></html>")


def test_xml_attachment_is_read_in_python():
    atts = attachments.decode([{"name": "loan.xml", "type": "", "data": base64.b64encode(MISMO).decode()}])
    assert atts[0]["type"] == attachments.XML
    assert "720 FICO" in attachments.block(atts)


# ---------- change feed ----------

def test_tracker_changes_show_on_their_tab():
    updates.CHANGES.write_text("\n".join([
        json.dumps({"id": "ml-2026-20", "agency": "FHA", "title": "ML 2026-20", "url": "https://hud.gov/x",
                    "published": "2026-11-10", "effective": "2027-01-01", "summary": "s",
                    "changes": [{"topic": "Gift donors", "old": "a", "new": "Employers may give"}]}),
        "not json",
        json.dumps({"id": "b1", "agency": "Freddie", "title": "Bulletin 2026-16", "published": "2026-11-12", "changes": []}),
    ]), encoding="utf-8")
    got = updates.changes("2026-11-01")
    assert [(g["id"], g["section"]) for g in got] == [("chg-ml-2026-20", "FHA"), ("chg-b1", "Conventional")]
    assert got[0]["changes"] == ["Gift donors: Employers may give"] and got[0]["effective"] == "2027-01-01"
    assert updates.changes("2026-11-11")[0]["id"] == "chg-b1"


# ---------- regression trigger ----------

def test_regression_baseline_then_new_sheet(monkeypatch):
    from guidebot import agency
    seen = {"events": []}
    monkeypatch.setattr(updates, "SEEN", regress.DIR / "sheets.json")
    monkeypatch.setattr(agency, "load_state", lambda: {"updates": []})
    secs = {"NonQM": lambda p: p.startswith("Ignite"), "FHA": lambda p: p.startswith("FHA")}
    regress.DIR.mkdir(parents=True)
    updates.SEEN.write_text(json.dumps(seen))
    assert regress._changes(secs) == {}   # first look is the baseline
    seen["events"].insert(0, {"date": "2026-11-16", "program": "Ignite 1099", "file": "new.pdf", "kind": "changed"})
    updates.SEEN.write_text(json.dumps(seen))
    assert regress._changes(secs) == {"NonQM": "Ignite 1099 sheet changed: new.pdf"}
    assert regress._changes(secs) == {}   # checked once


def test_regression_flags_newly_broken(monkeypatch):
    from tools import stress_check, stress_run
    monkeypatch.setattr(regress, "cases", lambda sec, paid: [{"id": "a", "q": "qa"}, {"id": "b", "q": "qb"}])
    monkeypatch.setattr(stress_run, "ask", lambda c: c)
    regress._write(regress.LAST, {"a": [], "b": ["old problem"]})
    monkeypatch.setattr(stress_check, "check", lambda r: ["cell 70, expected 75"] if r["id"] == "a" else [])
    out = regress.run("NonQM", "test", paid=False, log=lambda m: None)
    assert [b["id"] for b in out["broke"]] == ["a"] and out["fixed"] == ["b"]
    assert regress.overview()["runs"][0]["section"] == "NonQM"


# ---------- share links, analytics ----------

def test_share_link_round_trip():
    t = shares.create("a@x.com", "Max DTI?", "50%.", [{"file": "a.pdf"}], "Ignite 1099")
    assert shares.get(t)["answer"] == "50%." and shares.get(t)["sources"] == [{"file": "a.pdf"}]
    assert shares.get("nope") is None


def test_analytics_counts_people_not_stress_runs():
    history.save_trace("a@x.com", "c1", "Max DTI?", "Ignite 1099", "guidelines", "m", 4.0, False, [], 100)
    history.save_trace("b@x.com", "c2", "max dti", "Ignite 1099", "guidelines", "cache", 0.1, False, [{"f": 1}], 100)
    history.save_trace("local", "", "stress q", "", "guidelines", "m", 9.0, False, [], 100)   # no chat id: a stress run
    r = analytics.report()
    assert r["totals"]["questions"] == 2 and r["totals"]["people"] == 2 and r["totals"]["cached"] == 1
    assert r["top"][0]["n"] == 2 and r["top"][0]["people"] == 2
    assert [g["q"] for g in r["gaps"]] == ["Max DTI?"]


# ---------- What if ----------

def test_deal_facts_for_what_if():
    from guidebot.server import _deal_facts
    f = _deal_facts("what if it's 680 FICO?", [{"role": "user", "content":
                    "700 FICO, $500k cash-out refi, investment, condo, value is $1M, ZIP 92101"}])
    assert (f["fico"], f["loan"], f["occupancy"], f["purpose"]) == (680, 500000, "Investment Property", "Cash Out Refinance")
    assert (f["property"], f["value"], f["zip"]) == ("condo", 1000000, "92101")
