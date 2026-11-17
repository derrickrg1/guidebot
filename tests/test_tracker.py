"""The agency change tracker (guidebot/tracker). Offline: Fannie's sitemap comes from saved copies in
tests/fixtures/tracker/, and the model is a stand-in that returns a fixed write-up."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from guidebot import agency
from guidebot.ingest import indexer
from guidebot.tracker import core, feed, summarize as S

FIX = Path(__file__).parent / "fixtures" / "tracker"
SITEMAP_INDEX = (FIX / "fannie_sitemap_index.xml").read_text(encoding="utf-8")
SITEMAP_PAGE = (FIX / "fannie_sitemap_page.xml").read_text(encoding="utf-8")
EMPTY_PAGE = '<?xml version="1.0"?><urlset></urlset>'


@pytest.fixture
def tmp_feed(tmp_path, monkeypatch):
    monkeypatch.setattr(feed, "FEED", tmp_path / "agency_changes.jsonl")
    monkeypatch.setattr(feed, "STATE", tmp_path / "tracker_state.json")
    monkeypatch.setattr(core, "GAP", 0)
    return tmp_path


def _release(origination=True, changes=1, effective="February 22, 2027"):
    return S.Release(origination=origination, summary="Changes the manual underwriting matrix — credit scores.",
                     effective=effective,
                     changes=[S.Change(topic="Minimum credit score", old="620 for ARMs", new="640 for all loans",
                                       section="B3-5.1-01", effective=None)] * changes)


@pytest.fixture
def fake_model(monkeypatch):
    calls = []

    def fake(agency_name, title, text, is_diff=False):
        calls.append({"agency": agency_name, "title": title, "text": text, "diff": is_diff})
        rel = fake.reply
        rel.effective = S.iso(rel.effective)
        for c in rel.changes:
            c.effective = S.iso(c.effective) or rel.effective
        return rel
    fake.reply = _release()
    fake.calls = calls
    monkeypatch.setattr(core, "summarize", fake)
    return fake


LONG = "Fannie Mae updates the Eligibility Matrix for manually underwritten loans. " * 10


# ---------- parsing Fannie's sitemap ----------
def test_fannie_releases_from_sitemap():
    rel = core.fannie_releases([SITEMAP_PAGE])
    ids = [r["id"] for r in rel]
    assert "fannie-sel-2026-10" in ids and "fannie-ll-2026-09" in ids
    assert not any("svc" in i for i in ids)                 # servicing guide announcements aren't watched
    sel = next(r for r in rel if r["id"] == "fannie-sel-2026-10")
    assert sel["published"] == "2026-11-04"                  # the date in the summary, not the sitemap's lastmod
    assert sel["title"] == "Fannie Mae Selling Guide Announcement SEL-2026-10"
    ll = next(r for r in rel if r["id"] == "fannie-ll-2026-09")
    assert ll["title"] == "Fannie Mae Lender Letter LL-2026-09: Manual underwriting eligibility"
    assert ll["url"].startswith("https://singlefamily.fanniemae.com/media/document/pdf/lender-letter-ll-2026-09")
    # "Updated October 21, 2026" is a reissue with its own id, so the update gets summarized too
    assert any(i.startswith("fannie-ll-2026-04-upd-2026-10-21") for i in ids)
    assert [r["published"] for r in rel] == sorted((r["published"] for r in rel), reverse=True)


def test_iso_dates():
    assert S.iso("October 1, 2026") == "2026-10-01"
    assert S.iso("effective 2/22/2027") == "2027-02-22"
    assert S.iso("2026-12-01") == "2026-12-01"
    assert S.iso("immediately") is None and S.iso(None) is None


# ---------- the feed ----------
def test_record_writes_exact_schema(tmp_feed, fake_model):
    out = core.record("fannie-ll-2026-09", "Fannie", "LL-2026-09", "https://x/ll", "2026-11-04", LONG)
    lines = feed.FEED.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert list(rec) == list(feed.FIELDS)
    assert list(rec["changes"][0]) == list(feed.CHANGE_FIELDS)
    assert rec["agency"] == "Fannie" and rec["published"] == "2026-11-04" and rec["effective"] == "2027-02-22"
    assert rec["changes"][0]["effective"] == "2027-02-22"     # a change with no date of its own takes the release's
    assert "—" not in rec["summary"] and "–" not in json.dumps(rec)
    assert rec["fetched_at"] and out == rec
    # the same release again: nothing new, no second model call
    assert core.record("fannie-ll-2026-09", "Fannie", "LL-2026-09", "https://x/ll", "2026-11-04", LONG) is None
    assert len(fake_model.calls) == 1 and len(feed.read()) == 1


def test_non_origination_left_out_and_remembered(tmp_feed, fake_model):
    fake_model.reply = _release(origination=False)
    assert core.record("fannie-ll-2026-05", "Fannie", "LL-2026-05", "u", "2026-06-24", LONG) is None
    assert feed.read() == [] and "fannie-ll-2026-05" in feed.load_state()["skipped"]
    assert core.record("fannie-ll-2026-05", "Fannie", "LL-2026-05", "u", "2026-06-24", LONG) is None
    assert len(fake_model.calls) == 1                         # not summarized again next week


def test_model_failure_is_retried(tmp_feed, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("every model failed")
    monkeypatch.setattr(core, "summarize", down)
    assert core.record("fha-ml-2026-14", "FHA", "ML 2026-14", "u", "2026-10-01", LONG, log=lambda m: None) is None
    assert "fha-ml-2026-14" in feed.load_state()["errors"]
    assert not feed.known("fha-ml-2026-14")                   # an error isn't "seen": the next check tries again


def test_bad_agency_rejected():
    with pytest.raises(ValueError):
        feed.shape({"id": "x", "agency": "HUD"})


def test_read_keeps_last_copy_of_a_repeated_id(tmp_feed):
    feed.FEED.write_text(json.dumps({"id": "a", "title": "one"}) + "\n" + json.dumps({"id": "b"}) + "\n"
                         + json.dumps({"id": "a", "title": "two"}) + "\n{half a line", encoding="utf-8")
    assert [(r["id"], r.get("title")) for r in feed.read()] == [("b", None), ("a", "two")]


# ---------- polling Fannie ----------
def test_poll_fannie(tmp_feed, fake_model, monkeypatch):
    fetched = []

    def fake_get(url, timeout=120):
        fetched.append(url)
        if url == core.FANNIE_SITEMAP:
            return SITEMAP_INDEX.encode()
        if "sitemap.xml?page=" in url:
            return (SITEMAP_PAGE if url.endswith("page=1") else EMPTY_PAGE).encode()
        return b"%PDF-1.6 fake"
    monkeypatch.setattr(core, "get", fake_get)
    monkeypatch.setattr(core, "pdf_text", lambda data: LONG)
    monkeypatch.setattr(core, "date", type("D", (), {"today": staticmethod(lambda: date(2026, 11, 16))}))
    added = core.poll_fannie(days=30, log=lambda m: None)
    # last 30 days: SEL-2026-10 (Nov 4), LL-2026-09 (Nov 4), LL-2026-04's Oct 21 update; not SEL-2026-08 (Sept 23)
    assert sorted(r["id"] for r in added) == ["fannie-ll-2026-04-upd-2026-10-21", "fannie-ll-2026-09",
                                              "fannie-sel-2026-10"]
    assert all(r["agency"] == "Fannie" for r in added)
    pdfs = [u for u in fetched if "/media/document/pdf/" in u]
    assert len(pdfs) == 3
    # a second poll downloads no PDFs
    fetched.clear()
    assert core.poll_fannie(days=30, log=lambda m: None) == []
    assert not [u for u in fetched if "/media/document/pdf/" in u]


def test_polite_user_agent_names_no_one():
    ua = core.UA["User-Agent"]
    assert "AskNick" in ua and "@" not in ua and "Revolution" not in ua


# ---------- files agency.py keeps ----------
def test_ids_for_agency_files():
    assert core.agency_release_id("hud-ml", "FHA/Mortgagee Letters/FHA Mortgagee Letter 2026-14.pdf", "ab") == "fha-ml-2026-14"
    assert core.agency_release_id("va-circ", "VA/Circulars/VA Circular 26-26-5 Change 1.pdf", "ab") == "va-circ-26-26-5-change-1"
    assert core.agency_release_id("hud-4000", "FHA/HUD 4000.1/FHA HUD 4000.1 Handbook.pdf", "abcdef99") \
        == "hud-4000-fha-hud-4000.1-handbook-abcdef"


def test_from_file_doc_and_diff(tmp_feed, fake_model):
    rec = {"title": "Mortgagee Letter 2026-14: Gift funds", "url": "https://hud/ml.pdf", "date": "", "hash": "h" * 64}
    pages = ["Mortgagee Letter 2026-14\nDate: October 1, 2026\n" + LONG]
    out = core.from_file("hud-ml", "FHA/Mortgagee Letters/FHA Mortgagee Letter 2026-14.pdf", rec, pages, None)
    assert out["id"] == "fha-ml-2026-14" and out["agency"] == "FHA" and out["published"] == "2026-10-01"
    assert fake_model.calls[-1]["diff"] is False
    # a guide's first copy isn't a change; a new edition is summarized from its diff
    hb = {"title": "HUD Handbook 4000.1 (Update 19)", "url": "https://hud/h.pdf", "date": "2026-11-01", "hash": "c" * 64}
    rel = "FHA/HUD 4000.1/FHA HUD 4000.1 Handbook.pdf"
    old = ["The minimum decision credit score is 500 for all borrowers on this program."]
    new = ["The minimum decision credit score is 580 for all borrowers on this program."]
    assert core.from_file("hud-4000", rel, hb, new, None) is None
    out = core.from_file("hud-4000", rel, hb, new, old)
    call = fake_model.calls[-1]
    assert call["diff"] is True and "REMOVED" in call["text"] and "is 500" in call["text"] and "is 580" in call["text"]
    assert out["published"] == "2026-11-01"
    # sources the feed doesn't cover, and circular exhibits, are ignored
    assert core.from_file("fannie", "Conventional/Fannie Mae/x.pdf", rec, pages, None) is None
    assert core.from_file("va-circ", "VA/Circulars/VA Circular 26-23-6 Exhibit B.pdf", rec, pages, None) is None


def test_agency_check_feeds_the_tracker(tmp_path, tmp_feed, fake_model, monkeypatch):
    """A new Mortgagee Letter downloaded by the weekly check lands in the feed, after the index update."""
    import pypdfium2 as pdfium
    d = tmp_path / "agency"
    monkeypatch.setattr(agency, "DIR", d)
    monkeypatch.setattr(agency, "TEXT_DIR", d / "_text")
    monkeypatch.setattr(agency, "STATE", d / "state.json")
    monkeypatch.setattr(agency, "ARCHIVE", tmp_path / "archive")
    order = []
    monkeypatch.setattr(indexer, "sync_index", lambda log=print: order.append("index"))
    monkeypatch.setattr(agency, "extract", lambda path: ["Mortgagee Letter 2026-14\nOctober 1, 2026\n" + LONG])
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 200)
    src_pdf = tmp_path / "ml.pdf"
    pdf.save(str(src_pdf))
    pdf.close()
    item = {"rel": "FHA/Mortgagee Letters/FHA Mortgagee Letter 2026-14.pdf", "title": "Mortgagee Letter 2026-14",
            "url": "https://hud/ml.pdf", "version": "v1", "date": "", "fetch": lambda: (src_pdf.read_bytes(), "")}
    monkeypatch.setattr(agency, "SOURCES", [{"key": "hud-ml", "name": "MLs", "section": "FHA",
                                             "folder": "FHA/Mortgagee Letters", "items": lambda: [item],
                                             "manages": True}])
    real_from_check = core.from_check
    monkeypatch.setattr(core, "from_check", lambda p, log=print: (order.append("feed"), real_from_check(p, log))[1])
    import guidebot.tracker as T
    monkeypatch.setattr(T, "from_check", core.from_check)
    new = agency.check(log=lambda m: None)
    assert len(new) == 1 and order == ["index", "feed"]
    assert [r["id"] for r in feed.read()] == ["fha-ml-2026-14"]
    assert agency.check(log=lambda m: None) == [] and len(fake_model.calls) == 1


def test_real_sources_include_fannie_news_but_check_skips_its_files():
    src = next(s for s in agency.SOURCES if s["key"] == "fannie-news")
    assert src["releases"] and "items" not in src


# ---------- Fannie Lender Letters saved and indexed ----------
def test_lender_letters_from_sitemap(tmp_feed):
    got = agency.fannie_lender_letters([SITEMAP_PAGE], today=date(2026, 11, 17))
    assert [i["rel"].rsplit("/", 1)[-1] for i in got] == ["Fannie Mae Lender Letter LL-2026-04.pdf",
                                                          "Fannie Mae Lender Letter LL-2026-09.pdf"]
    assert all(i["rel"].startswith(agency.LL_FOLDER + "/") for i in got)
    ll04 = got[0]
    assert ll04["date"] == "2026-10-21" and ll04["version"].endswith("#2026-10-21")   # the reissue is the copy kept
    feed.skip("fannie-ll-2026-04", "LL-2026-04", "nothing for origination")      # tracker: nothing for originators
    got = agency.fannie_lender_letters([SITEMAP_PAGE], today=date(2026, 11, 17))
    assert [i["rel"].rsplit(" ", 1)[-1] for i in got] == ["LL-2026-09.pdf"]
    assert agency.fannie_lender_letters([SITEMAP_PAGE], today=date(2028, 1, 5)) == []   # older than last year
    servicing = SITEMAP_PAGE.replace("lender-letter-ll-2026-09-manual-underwriting-eligibility",
                                     "lender-letter-ll-2026-09-updates-retention-workout-options")
    assert agency.fannie_lender_letters([servicing], today=date(2026, 11, 17)) == []       # servicers only


def test_lender_letters_are_a_fannie_source_of_their_own(tmp_path, tmp_feed, monkeypatch):
    """Saved under the Fannie folder (so only Fannie programs get them), but the Selling Guide's notice and its
    edition date ignore them, and the model is told a newer letter wins."""
    d = tmp_path / "agency"
    monkeypatch.setattr(agency, "DIR", d)
    monkeypatch.setattr(agency, "TEXT_DIR", d / "_text")
    monkeypatch.setattr(agency, "STATE", d / "state.json")
    monkeypatch.setattr(agency, "ARCHIVE", tmp_path / "archive")
    indexed = []
    monkeypatch.setattr(indexer, "sync_index", lambda log=print: indexed.append(1))
    monkeypatch.setattr(agency, "_download", lambda url: (b"%PDF-1.4 " + url.encode(), ""))
    monkeypatch.setattr(agency, "extract", lambda path: ["Lender Letter LL-2026-09\nNovember 4, 2026\n" + LONG])
    guide = {"manual": True, "title": "Fannie Mae Selling Guide", "url": "https://fannie/guide", "version": "2026-11-04",
             "date": "2026-11-04"}
    fannie = {"key": "fannie", "name": "Selling Guide", "section": "Conventional", "folder": "Conventional/Fannie Mae",
              "items": lambda: [guide], "manages": False}
    ll = {"key": "fannie-ll", "name": "Lender Letters", "section": "Conventional", "folder": agency.LL_FOLDER,
          "items": lambda: agency.fannie_lender_letters([SITEMAP_PAGE], today=date(2026, 11, 17)), "manages": True}
    monkeypatch.setattr(agency, "SOURCES", [fannie, ll])
    new = agency.check(log=lambda m: None)
    state = agency.load_state()
    assert sorted(u["file"].rsplit(" ", 1)[-1] for u in new) == ["LL-2026-04.pdf", "LL-2026-09.pdf"] and indexed
    assert "fannie" in state["notices"]                                    # letters don't count as the guide
    rel = f"{agency.LL_FOLDER}/Fannie Mae Lender Letter LL-2026-09.pdf"
    assert agency.source_of(rel) is ll and agency.source_of("Conventional/Fannie Mae/Selling Guide.pdf") is fannie
    assert "Lender Letter" in agency.label({"rel": rel, **state["files"][rel]})
    assert "Lender Letter" not in agency.label({"rel": "Conventional/Fannie Mae/Selling Guide.pdf"})
    assert {s["key"]: s["files"] for s in agency.overview()["sources"]} == {"fannie": 0, "fannie-ll": 2}

    feed.skip("fannie-ll-2026-04", "LL-2026-04", "nothing for origination")   # tracker left it out: archived
    agency.check(log=lambda m: None)
    assert not (d / agency.LL_FOLDER / "Fannie Mae Lender Letter LL-2026-04.pdf").exists()
    assert (d / rel).exists()
