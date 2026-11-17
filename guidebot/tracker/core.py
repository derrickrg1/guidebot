"""Finds agency releases, summarizes each one and appends it to the feed (feed.py).

Where releases come from:
  * Files agency.check() downloads (inside the server, so the search index has one writer): a new Mortgagee Letter
    or VA Circular is summarized whole; a new edition of HUD 4000.1, a VA 26-7 chapter or Freddie's guide (saved by
    hand) is summarized from the lines it added and removed. Fannie's whole guide is left out: its announcements
    say the same thing better.
  * Fannie Mae Selling Guide announcements (SEL-) and Lender Letters (LL-), found in singlefamily.fanniemae.com's
    sitemap (robots.txt allows it) and read here. The Lender Letters are also saved into agency/ and indexed by
    agency.py's "fannie-ll" source; the announcements never are.
  * Freddie Mac bulletins: guide.freddiemac.com's robots.txt shuts out every bot but search engines, so nothing
    here fetches them. Freddie's changes arrive with the guide PDF Derrick saves each month.

`python -m guidebot.tracker` runs the same thing outside the server: Fannie's releases plus a backfill from files
agency.py already has. It reads agency/state.json and the archive, never writes them or the index.
"""
import html
import json
import re
import tempfile
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from guidebot import agency
from guidebot.tracker import feed
from guidebot.tracker.summarize import diff_text, iso, summarize

FANNIE_SITEMAP = "https://singlefamily.fanniemae.com/sitemap.xml"
UA = {"User-Agent": "AskNick-guideline-watcher/1.0 (internal guideline change tracker; weekly)"}
GAP = 3.0   # seconds between requests to one host
_last_hit: dict[str, float] = {}

# source key -> (agency, how): "doc" = summarize the whole new file, "diff" = summarize what a new edition changed
FROM_AGENCY = {"hud-ml": ("FHA", "doc"), "va-circ": ("VA", "doc"),
               "hud-4000": ("FHA", "diff"), "va-26-7": ("VA", "diff"), "freddie": ("Freddie", "diff")}


# ---------- polite fetching ----------
def get(url: str, timeout: int = 120) -> bytes:
    host = urllib.request.urlparse(url).netloc
    wait = _last_hit.get(host, 0) + GAP - time.time()
    if wait > 0:
        time.sleep(wait)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    finally:
        _last_hit[host] = time.time()


def pdf_text(data: bytes) -> str:
    if not data.startswith(b"%PDF"):
        raise ValueError("not a PDF")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "release.pdf"
        p.write_bytes(data)
        return "\n\n".join(f"[p. {n}]\n{t}" for n, t in enumerate(agency.extract(p), start=1))


# ---------- recording ----------
def record(rid: str, agency_name: str, title: str, url: str, published: str | None, text: str,
           is_diff: bool = False, log=print) -> dict | None:
    """Summarizes one release into the feed. Returns the feed record, or None if it was left out or failed."""
    if feed.known(rid):
        return None
    if len(text.strip()) < 200:
        feed.error(rid, title, "no readable text")
        log(f"{title}: no readable text, skipped")
        return None
    try:
        rel = summarize(agency_name, title, text, is_diff=is_diff)
    except Exception as e:
        feed.error(rid, title, f"{type(e).__name__}: {e}")
        log(f"{title}: couldn't summarize ({type(e).__name__}), will retry next check")
        return None
    if not rel.origination or not rel.changes:
        feed.skip(rid, title, "nothing for origination" if not rel.origination else "no rule changes")
        log(f"{title}: nothing for originators, left out")
        return None
    out = feed.append({"id": rid, "agency": agency_name, "title": title, "url": url,
                       "published": iso(published) or date.today().isoformat(), "effective": rel.effective,
                       "summary": rel.summary, "changes": [c.model_dump() for c in rel.changes]})
    log(f"{title}: {len(out['changes'])} change(s) added to the feed")
    return out


# ---------- Fannie Mae ----------
_FANNIE_SLUG = re.compile(r"/media/document/pdf/(announcement-sel|lender-letter-ll)-(\d{4})-(\d{2})-?([a-z0-9-]*)$")


def fannie_releases(xml_pages: list[str]) -> list[dict]:
    """Selling Guide announcements and Lender Letters in Fannie's sitemap, newest first. An "Updated <date>" summary
    is a reissue: it gets its own id, so the update is summarized too."""
    out = {}
    for xml in xml_pages:
        for url in re.findall(r"<url>.*?</url>", xml, re.S):
            loc = re.search(r"<loc>([^<]+)</loc>", url)
            m = loc and _FANNIE_SLUG.search(loc[1].strip())
            if not m:
                continue
            kind, year, num, words = m[1], m[2], m[3], m[4]
            summary = re.search(r"<summary>(.*?)</summary>", url, re.S)
            summary = agency._plain(html.unescape(summary[1])) if summary else ""
            lastmod = re.search(r"<lastmod>([^<]+)</lastmod>", url)
            published = agency.find_date(summary[:120]) or (lastmod[1][:10] if lastmod else "")
            code = ("SEL" if kind == "announcement-sel" else "LL") + f"-{year}-{num}"
            rid = f"fannie-{code.lower()}"
            if summary.lower().startswith("updated") and published:
                rid += f"-upd-{published}"
            label = "Selling Guide Announcement" if code.startswith("SEL") else "Lender Letter"
            topic = words.replace("-", " ").strip().capitalize()
            title = f"Fannie Mae {label} {code}" + (f": {topic}" if topic and code.startswith("LL") else "")
            out[rid] = {"id": rid, "title": title, "url": loc[1].strip(), "published": published, "blurb": summary}
    return sorted(out.values(), key=lambda r: r["published"], reverse=True)


def fannie_sitemap() -> list[str]:
    index = get(FANNIE_SITEMAP).decode("utf-8", "ignore")
    pages = re.findall(r"<loc>([^<]+sitemap\.xml\?page=\d+)</loc>", index) or [FANNIE_SITEMAP]
    return [index if p == FANNIE_SITEMAP else get(html.unescape(p), timeout=180).decode("utf-8", "ignore")
            for p in pages]


def poll_fannie(days: int = 120, log=print) -> list[dict]:
    """New Fannie releases from the last `days` days into the feed."""
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        releases = [r for r in fannie_releases(fannie_sitemap()) if r["published"] >= since]
    except Exception as e:
        log(f"Fannie Mae releases: couldn't check ({e})")
        return []
    added = []
    for r in releases:
        if feed.known(r["id"]):
            continue
        try:
            text = pdf_text(get(r["url"], timeout=300))
        except Exception as e:
            feed.error(r["id"], r["title"], f"download: {e}")
            log(f"{r['title']}: download failed ({e})")
            continue
        if rec := record(r["id"], "Fannie", r["title"], r["url"], r["published"], text, log=log):
            added.append(rec)
    return added


# ---------- files agency.py keeps ----------
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-")


def agency_release_id(key: str, rel: str, file_hash: str) -> str:
    stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if key == "hud-ml":
        m = re.search(r"(\d{4}-\d{2,3})$", stem)
        return f"fha-ml-{m[1] if m else _slug(stem)}"
    if key == "va-circ":
        return "va-circ-" + _slug(stem.replace("VA Circular", ""))
    return f"{key}-{_slug(stem)}-{file_hash[:6]}"


def from_file(key: str, rel: str, rec: dict, new_pages: list[str], old_pages: list[str] | None, log=print):
    """One new or changed agency file into the feed, if its source is one the feed covers."""
    if key not in FROM_AGENCY:
        return None
    agency_name, how = FROM_AGENCY[key]
    if "Exhibit" in rel.rsplit("/", 1)[-1]:
        return None   # a circular's exhibit comes with the circular
    title = rec.get("title") or rel
    published = rec.get("date") or agency.find_date(new_pages[0][:2000] if new_pages else "")
    rid = agency_release_id(key, rel, rec.get("hash") or "")
    if how == "diff":
        if old_pages is None:
            return None   # the first copy of a guide isn't a change
        d = agency.page_diff(old_pages, new_pages, limit=400)
        if not d["added"] and not d["removed"]:
            return None
        return record(rid, agency_name, title, rec.get("url") or "", published, diff_text(d), is_diff=True, log=log)
    text = "\n\n".join(f"[p. {n}]\n{t}" for n, t in enumerate(new_pages, start=1))
    return record(rid, agency_name, title, rec.get("url") or "", published, text, log=log)


def from_check(pending: list[dict], log=print) -> list[dict]:
    """agency.check() hands over every file it just read: [{key, rel, rec, pages, old}]. Errors stay here."""
    out = []
    for p in pending:
        try:
            if r := from_file(p["key"], p["rel"], p["rec"], p["pages"], p["old"], log=log):
                out.append(r)
        except Exception as e:
            log(f"{p['rel']}: feed failed ({type(e).__name__}: {e})")
    return out


def _cached_pages(rel: str) -> list[str] | None:
    tp = agency._text_path(rel)
    if tp.exists():
        return json.loads(tp.read_text(encoding="utf-8"))["pages"]
    path = agency.DIR / rel
    return agency.extract(path) if path.exists() else None


def backfill(days: int = 120, log=print) -> list[dict]:
    """Releases agency.py already has from the last `days` days that aren't in the feed yet. Read only on agency/."""
    since = (date.today() - timedelta(days=days)).isoformat()
    state, out = agency.load_state(), []
    for rel, rec in sorted(state["files"].items()):
        key = rec.get("source") or next((s["key"] for s in agency.SOURCES if rel.startswith(s["folder"] + "/")), "")
        if FROM_AGENCY.get(key, ("", ""))[1] != "doc":
            continue
        pages = _cached_pages(rel)
        if not pages:
            continue
        published = rec.get("date") or agency.find_date(pages[0][:2000])
        if published < since:
            continue
        if r := from_file(key, rel, rec, pages, None, log=log):
            out.append(r)
    # new editions: the archive keeps the copy each update replaced
    for u in state["updates"]:
        if u.get("kind") != "changed" or u.get("date", "") < since:
            continue
        rel = u["file"]
        rec = state["files"].get(rel)
        key = (rec or {}).get("source") or next((s["key"] for s in agency.SOURCES
                                                 if rel.startswith(s["folder"] + "/")), "")
        old_path = agency.ARCHIVE / u["date"] / rel
        if not rec or FROM_AGENCY.get(key, ("", ""))[1] != "diff" or not old_path.exists():
            continue
        if not u["id"].endswith((rec.get("hash") or "")[:6]):
            continue   # an older edition; the current file isn't its "after"
        new_pages = _cached_pages(rel)
        if new_pages and (r := from_file(key, rel, rec, new_pages, agency.extract(old_path), log=log)):
            out.append(r)
    return out


def run(days: int = 120, log=print) -> list[dict]:
    """The standalone run: Fannie's releases plus the backfill. Never touches the search index."""
    added = poll_fannie(days, log=log) + backfill(days, log=log)
    feed.touch_poll()
    return added


