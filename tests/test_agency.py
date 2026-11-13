import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from guidebot import agency, answer, chat, config
from guidebot.ingest import indexer

PDF_A = config.DOCS_DIR / "FHA Standard" / "FHA Standard Matrix.pdf"
PDF_B = config.DOCS_DIR / "VA Standard" / "VA Standard Matrix.pdf"
have_pdfs = pytest.mark.skipif(not (PDF_A.exists() and PDF_B.exists()), reason="sample PDFs not in docs/")


@pytest.fixture
def tmp_agency(tmp_path, monkeypatch):
    d = tmp_path / "agency"
    monkeypatch.setattr(agency, "DIR", d)
    monkeypatch.setattr(agency, "TEXT_DIR", d / "_text")
    monkeypatch.setattr(agency, "STATE", d / "state.json")
    monkeypatch.setattr(agency, "ARCHIVE", tmp_path / "archive")
    indexed = []
    monkeypatch.setattr(indexer, "sync_index", lambda log=print: indexed.append(1))
    return d, tmp_path / "archive", indexed


def _source(items, folder="FHA/Letters", manages=True, key="t"):
    return {"key": key, "name": "Test", "section": "FHA", "folder": folder, "items": lambda: items(), "manages": manages}


def _item(rel, version, pdf):
    return {"rel": rel, "title": rel.rsplit("/", 1)[-1], "url": "https://x", "version": version, "date": "2026-08-12",
            "fetch": lambda: (pdf.read_bytes(), "")}


def test_find_date():
    assert agency.find_date("Circular 26-26-5 - October 27, 2026") == "2026-10-27"
    assert agency.find_date("(Rescind Jan. 1, 2025)") == "2025-01-01"
    assert agency.find_date("no date here") == ""


def test_only_the_chapter_in_force_today():
    today = "2026-11-12"
    assert agency._chapter_wanted("VA Pamphlet VAP26-7 Chapter 04 Credit Underwriting", today)
    assert not agency._chapter_wanted("VA Pamphlet VAP26-7 Chapter 04 Credit Underwriting - 20260825", today)
    assert not agency._chapter_wanted("VA Pamphlet VAP26-7 Chapter 12 MPR - Effective until May 1, 2026", today)
    assert agency._chapter_wanted("VA Pamphlet VAP26-7 Chapter 12 MPR - Effective after May 1, 2026", today)
    assert not agency._chapter_wanted("VA Pamphlet VAP26-7 Chapter 12 MPR - Effective after May 1, 2027", today)


def test_diff_ignores_running_headers():
    head = "Handbook 4000.1 Published Date: 08/12/2026"
    old = [f"{head}\nThe minimum decision credit score is 500 for this case.\nPage text number {i} stays the same."
           for i in range(10)]
    new = [p.replace("08/12/2026", "11/04/2026") for p in old]
    new[3] = new[3].replace("is 500 for", "is 580 for")
    d = agency.page_diff(old, new)
    assert d["pages"] == [4] and d["added"] == 1 and d["removed"] == 0     # the 500 line still exists on other pages
    assert "580" in d["added_lines"][0][1]


@have_pdfs
def test_check_downloads_archives_and_writes_up(tmp_agency, monkeypatch):
    d, archive, indexed = tmp_agency
    items = [_item("FHA/Letters/FHA ML 2026-01.pdf", "v1", PDF_A)]
    monkeypatch.setattr(agency, "SOURCES", [_source(lambda: items)])
    new = agency.check(log=lambda m: None)
    assert [u["kind"] for u in new] == ["new"] and (d / "FHA/Letters/FHA ML 2026-01.pdf").exists() and indexed
    rec = agency.info("FHA ML 2026-01.pdf")
    assert rec["reviewed"] is False and rec["date"] == "2026-08-12"
    assert "not yet reviewed" in agency.label(rec)

    assert agency.check(log=lambda m: None) == []                      # same version: nothing downloaded

    items[:] = [_item("FHA/Letters/FHA ML 2026-01.pdf", "v2", PDF_B)]   # the agency put out a new copy
    new = agency.check(log=lambda m: None)
    assert new[0]["kind"] == "changed" and new[0]["diff"]["added"] > 0
    assert list(archive.rglob("FHA ML 2026-01.pdf"))                  # old copy kept

    items[:] = [_item("FHA/Letters/FHA ML 2026-02.pdf", "v1", PDF_A)]   # 2026-01 dropped off the agency's list
    agency.check(log=lambda m: None)
    state = agency.load_state()
    assert "FHA/Letters/FHA ML 2026-01.pdf" not in state["files"]
    assert any(u["kind"] == "removed" for u in state["updates"])

    open_id = next(u["id"] for u in state["updates"] if u["file"].endswith("2026-02.pdf"))
    assert agency.mark_reviewed(open_id) == {"ok": True}
    assert agency.info("FHA ML 2026-02.pdf")["reviewed"] is True
    assert agency.mark_reviewed("*") == {"ok": True} and agency.overview()["open"] == 0


@have_pdfs
def test_a_source_that_finds_nothing_archives_nothing(tmp_agency, monkeypatch):
    d, _, _ = tmp_agency
    items = [_item("FHA/Letters/FHA ML 2026-01.pdf", "v1", PDF_A)]
    monkeypatch.setattr(agency, "SOURCES", [_source(lambda: items)])
    agency.check(log=lambda m: None)
    items.clear()                                                       # page layout changed, parser finds nothing
    agency.check(log=lambda m: None)
    assert (d / "FHA/Letters/FHA ML 2026-01.pdf").exists()
    assert "found nothing" in agency.load_state()["sources"]["t"]["error"]


@have_pdfs
def test_fannie_is_a_notice_until_saved_by_hand(tmp_agency, monkeypatch):
    d, _, _ = tmp_agency
    manual = [{"manual": True, "title": "Fannie Mae Selling Guide", "url": "https://f", "version": "2026-11-04",
               "date": "2026-11-04"}]
    monkeypatch.setattr(agency, "SOURCES", [_source(lambda: manual, folder="Conventional/Fannie Mae", manages=False,
                                                    key="fannie")])
    agency.check(log=lambda m: None)
    assert agency.overview()["notices"][0]["version"] == "2026-11-04"
    (d / "Conventional/Fannie Mae").mkdir(parents=True)
    (d / "Conventional/Fannie Mae/Selling Guide.pdf").write_bytes(PDF_A.read_bytes())
    new = agency.check(log=lambda m: None)
    assert new[0]["title"] == "Fannie Mae Selling Guide" and not agency.overview()["notices"]
    assert agency.check(log=lambda m: None) == []                       # same edition: no new notice


def test_circulars_skip_rescinded_and_exhibits(monkeypatch):
    page = """<h2><a id="2026"></a>2026</h2>
    <p><a href="/c/26-26-5.pdf">Circular 26-26-5 - October 27, 2026</a><br /><strong>Fedwire</strong> (Valid until rescinded)</p>
    <p><a href="/c/26-24-19c1.pdf">Circular 26-24-19 (Change 1) - Jun 4, 2026</a> | <a href="/c/26-24-19.pdf">Circular 26-24-19</a>
       | <a href="/c/x.pdf">Exhibit A</a><br /><strong>Invoices</strong> (Valid until rescinded)</p>
    <p><a href="/c/26-24-12.pdf">Circular 26-24-12 - May 29, 2024</a><br /><strong>Relief</strong> (Rescind Jan. 1, 2025)</p>
    <p><a href="/c/26-24-19.pdf">Circular 26-24-19 - Sep 13, 2024</a><br /><strong>Invoices</strong> (Valid until rescinded)</p>"""
    monkeypatch.setattr(agency, "_page", lambda url: page)
    got = {i["rel"].split("/")[-1]: i for i in agency.va_circulars()}
    assert sorted(got) == ["VA Circular 26-24-19 Change 1.pdf", "VA Circular 26-24-19 Exhibit A.pdf",
                           "VA Circular 26-24-19.pdf", "VA Circular 26-26-5.pdf"]   # exhibits hold the tables
    assert got["VA Circular 26-26-5.pdf"]["date"] == "2026-10-27"


def test_mortgagee_letters_single_family_only(monkeypatch):
    rows = [("2026-12", "Build-To-Rent Developments Underwriting Eligibility"), ("2026-05", "Annual Indexing of MAP Guide"),
            ("2026-01", "Creating a Middle Income Housing option for 221(d)(4)"), ("2025-22", "2026 HECM Limits"),
            ("2024-10", "Old letter")]
    page = "".join(f'<tr><td>{n}</td><td><a href="/d/{n}.pdf">{t}</a></td></tr>' for n, t in rows)
    monkeypatch.setattr(agency, "_page", lambda url: page)
    got = [i["rel"] for i in agency.hud_mortgagee_letters()]
    assert got == ["FHA/Mortgagee Letters/FHA Mortgagee Letter 2026-12.pdf"]


def test_unreviewed_agency_pages_get_a_note(monkeypatch):
    recs = {"FHA HUD 4000.1 Handbook.pdf": {"rel": "x", "title": "HUD Handbook 4000.1 (Update 18)",
                                            "downloaded": "2026-11-12", "reviewed": False}}
    monkeypatch.setattr(agency, "info", lambda f: recs.get(f))
    hits = [{"file": "FHA HUD 4000.1 Handbook.pdf", "agency": True}, {"file": "Other.pdf", "agency": True}]
    note, cited = chat._agency_note("Min score 580 (FHA HUD 4000.1 Handbook.pdf, p. 120).", hits)
    assert [h["file"] for h in cited] == ["FHA HUD 4000.1 Handbook.pdf"]
    assert "HUD Handbook 4000.1 (Update 18) was updated here 2026-11-12" in note and "hasn't been reviewed" in note
    pages = [{"file": "FHA HUD 4000.1 Handbook.pdf", "agency": True, "page": p} for p in (176, 204, 942)]
    _, cited = chat._agency_note("MDCS (FHA HUD 4000.1 Handbook.pdf, p. 177; FHA HUD 4000.1 Handbook.pdf, p. 204)", pages)
    assert [h["page"] for h in cited] == [176, 204]   # p. 177 came along with p. 176; p. 942 wasn't cited
    recs["FHA HUD 4000.1 Handbook.pdf"]["reviewed"] = True
    assert chat._agency_note("(FHA HUD 4000.1 Handbook.pdf, p. 1)", hits)[0] == ""


def test_shortened_agency_cites_get_the_full_name(monkeypatch):
    names = ["VA M26-7 Chapter 07 Loans Requiring Special Underwriting.pdf", "VA M26-7 Chapter 08 Fees.pdf",
             "FHA HUD 4000.1 Handbook.pdf"]
    monkeypatch.setattr(agency, "files", lambda: [Path(n) for n in names])
    out = chat._fix_agency_cites("Occupancy (VA M26-7 Chapter 7, p. 2; VA Standard Matrix.pdf, p. 3). "
                                 "Score (FHA HUD 4000.1, p. 177). Fees (VA M26-7, p. 1). Plain (see note).")
    assert "(VA M26-7 Chapter 07 Loans Requiring Special Underwriting.pdf, p. 2; VA Standard Matrix.pdf, p. 3)" in out
    assert "(FHA HUD 4000.1 Handbook.pdf, p. 177)" in out
    assert "(VA M26-7, p. 1)" in out and "(see note)" in out          # ambiguous or not a citation: left alone


def test_hud_pages_outside_origination_rank_last():
    page = lambda part, sub: f"[FHA Agency | x]\n=== x | p. 1 ===\n{part}\n{sub}\nHandbook 4000.1 258\nLast Revised"
    assert not agency.off_topic(page("II. ORIGINATION THROUGH POST-CLOSING/ENDORSEMENT",
                                     "A. Title II Insured Housing Programs Forward Mortgages"))
    assert agency.off_topic(page("II. ORIGINATION THROUGH POST-CLOSING/ENDORSEMENT", "E. Title I Insured Programs"))
    assert agency.off_topic(page("III. SERVICING AND LOSS MITIGATION", "A. Title II Insured Housing Programs Forward"))
    assert not agency.off_topic("=== VA M26-7 Chapter 04.pdf | p. 3 ===\nTopic 2. Income")


def test_agency_section_and_rules():
    assert chat._agency_section("VA", "") == "VA" and chat._agency_section("", "FHA") == "FHA"
    assert chat._agency_section("Ignite 1099", "NonQM") == ""
    rules = " ".join(answer.RULES.split())
    assert "GENERAL GUIDELINE, agency guide" in rules and "Every VA Circular you're given is still in force" in rules
    assert "never an agency letter" in rules
    assert '"depends" (on the program), never "no"' in " ".join(answer.VERDICT_SYSTEM.split())
