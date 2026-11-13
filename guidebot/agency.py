"""Agency guides: the general guidelines under NDM's FHA, VA and Conventional sheets.

FHA: HUD Handbook 4000.1 plus Mortgagee Letters. VA: VA Pamphlet 26-7 (the Lenders Handbook, one PDF per chapter)
plus active Circulars. Conventional: the Fannie Mae Selling Guide.

check() runs once a week from inside the server (never a second process: two writers corrupted the search index
on 2026-11-12). New or changed files land in agency/<Section>/<Source>/, the old copy goes to
archive/agency/<date>/, the text is re-read, the index updated, and the change is written up for the admin
Updates tab: which pages changed, lines added and removed. Agency text answers right away, labeled "not yet
reviewed" until Derrick marks it reviewed. None of it feeds an LTV lookup.

Fannie Mae refuses automated downloads, so for it the check only spots a new edition and says where to get it.
Any PDF saved into an agency folder by hand is picked up the same way as a download.
"""
import hashlib
import html
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

from guidebot import config
from guidebot.extract_pdf import PDFIUM_LOCK

DIR = config.AGENCY_DIR
TEXT_DIR = DIR / "_text"
STATE = DIR / "state.json"
ARCHIVE = config.ROOT / "archive" / "agency"
SECTIONS = ("FHA", "VA", "Conventional")
UA = {"User-Agent": "AskNick-guideline-watcher/1.0 (Revolution Funding; weekly check)"}
EVERY = 7 * 86400
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
_run_lock = threading.Lock()

HUD_HANDBOOKS = "https://www.hud.gov/hudclips/handbooks/housing"
HUD_MLS = "https://www.hud.gov/hudclips/letters/mortgagee"
KNOWVA = "https://www.knowva.ebenefits.va.gov/system/ws/v11/ss"
KNOWVA_Q = "$lang=en-us&portalId=554400000001018&usertype=customer"
VA_CIRCULARS = "https://www.benefits.va.gov/homeloans/resources_circulars.asp"
FANNIE = "https://selling-guide.fanniemae.com/"
# The whole current guide in one PDF, rebuilt with each bulletin (link from Derrick, 2026-11-12).
FREDDIE_PDF = "https://guide.freddiemac.com/ci/okcsFattach/get/1002095_2"
# Mortgagee Letters for multifamily, healthcare, public housing and reverse mortgage (HECM) programs say nothing
# about a forward single family loan.
NOT_SINGLE_FAMILY = re.compile(r"multifamily|lihtc|tax credit|section 2(?:02|21|23|32|42)\b|section 8\b|section 811|"
                               r"221\(d\)|map guide|hecm|home equity conversion|"
                               r"healthcare|health care|hospital|residential care|nursing|public housing|"
                               r"rental assistance|\brad\b", re.I)


# ---------- small helpers ----------
def _get(url: str, timeout: int = 120) -> tuple[bytes, dict]:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read(), dict(r.headers)


def _page(url: str) -> str:
    return _get(url)[0].decode("utf-8", "ignore")


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def find_date(s: str) -> str:
    """First "August 12, 2026" / "Aug. 12 2026" in the text, as 2026-08-12. "" if none."""
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", s, re.I)
    return f"{m[3]}-{_MONTHS.index(m[1].lower()[:3]) + 1:02d}-{int(m[2]):02d}" if m else ""


def _header_date(headers: dict) -> str:
    try:
        return parsedate_to_datetime(headers.get("Last-Modified", "")).date().isoformat()
    except (TypeError, ValueError):
        return ""


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe(name: str) -> str:
    """A filename part. No commas either: a citation is "(filename, p. X)", so a comma in the name splits it."""
    return re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*\',]', "", name)).strip()[:120]


def _download(url: str) -> tuple[bytes, str]:
    data, headers = _get(url, timeout=600)
    if not data.startswith(b"%PDF"):
        raise ValueError(f"{url} didn't return a PDF")
    return data, _header_date(headers)


# ---------- sources: each returns the files it wants on disk right now ----------
# An item: rel (path under agency/), title, url, version (changes when the agency publishes a new copy),
# date (the document's own date, "" if unknown), fetch() -> (pdf bytes, date). A source with `manages` owns its
# folder: files there it no longer lists (rescinded circulars, superseded chapters) move to the archive.

def hud_handbook() -> list[dict]:
    page = _page(HUD_HANDBOOKS)
    found = re.findall(r'href="([^"]*40001-hsgh-Update-(\d+)\.pdf)"', page, re.I)
    if not found:
        raise ValueError("no 4000.1 PDF link on HUD's handbook page")
    href, n = max(found, key=lambda f: int(f[1]))
    url = urljoin(HUD_HANDBOOKS, href)
    return [{"rel": "FHA/HUD 4000.1/FHA HUD 4000.1 Handbook.pdf", "title": f"HUD Handbook 4000.1 (Update {n})",
             "url": url, "version": url, "date": "", "fetch": lambda: _download(url)}]


def hud_mortgagee_letters() -> list[dict]:
    """Single family Mortgagee Letters from this year and last. Older ones are in the handbook by now; where a
    letter is newer than the handbook, the answer rules say the letter wins."""
    page = _page(HUD_MLS)
    keep_from = date.today().year - 1
    out = []
    for row in re.findall(r"<tr.*?</tr>", page, re.S):
        m = re.search(r"(\d{4})-(\d{2,3}).*?href=\"([^\"]+\.pdf)\"[^>]*>(.*?)</a>", row, re.S | re.I)
        if not m or int(m[1]) < keep_from:
            continue
        title = _plain(m[4])
        if NOT_SINGLE_FAMILY.search(title):
            continue
        num, url = f"{m[1]}-{m[2]}", urljoin(HUD_MLS, m[3])
        out.append({"rel": f"FHA/Mortgagee Letters/FHA Mortgagee Letter {num}.pdf",
                    "title": f"Mortgagee Letter {num}: {title}", "url": url, "version": url, "date": "",
                    "fetch": lambda url=url: _download(url)})
    return out


def _knowva_xml(path: str) -> str:
    return _page(f"{KNOWVA}/{path}{'&' if '?' in path else '?'}{KNOWVA_Q}")


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<(?:ns\d+:)?{name}>(.*?)</(?:ns\d+:)?{name}>", xml, re.S)
    return html.unescape(m[1]) if m else ""


def _chapter_wanted(name: str, today: str) -> bool:
    """KnowVA keeps dated snapshots ("... - 20260825") and before/after copies around a change date
    ("Effective until May 1, 2026"). Only the copy in force today counts."""
    if re.search(r" - \d{8}\s*$", name):
        return False
    until = re.search(r"Effective until (.+)$", name, re.I)
    after = re.search(r"Effective after (.+)$", name, re.I)
    return not ((until and find_date(until[1]) <= today) or (after and find_date(after[1]) > today))


def va_lenders_handbook() -> list[dict]:
    """VA Pamphlet 26-7, one item per chapter. VA moved it from benefits.va.gov to the KnowVA knowledge base,
    where each chapter is a web article; it's saved as a PDF so citations open on a page like every other guide."""
    xml = _knowva_xml("search/kb?$pagenum=1&$pagesize=100&q=" + urllib.request.quote("VA Pamphlet VAP26-7 Chapter"))
    today = date.today().isoformat()
    best = {}
    for art in re.findall(r"<(?:ns\d+:)?article\b.*?</(?:ns\d+:)?article>", xml, re.S):
        name = re.sub(r"\s+", " ", _tag(art, "name")).strip()
        m = re.match(r"VA Pamphlet VAP26-7 Chapter (\d+) (.+)$", name)
        if not m or not _chapter_wanted(name, today):
            continue
        aid, modified = re.search(r'\bid="(\d+)"', art)[1], _tag(art, "lastModifiedDate")
        if m[1] not in best or modified > best[m[1]]["modified"]:
            title = re.sub(r"\s*-\s*Effective (after|until) .+$", "", m[2], flags=re.I)
            best[m[1]] = {"id": aid, "modified": modified, "title": title}
    if len(best) < 15:
        raise ValueError(f"KnowVA listed only {len(best)} Lenders Handbook chapters")
    out = []
    for num, a in sorted(best.items(), key=lambda kv: int(kv[0])):
        title = f"VA Pamphlet 26-7, Chapter {int(num)}: {a['title']}"
        url = f"{KNOWVA}/article/{a['id']}?{KNOWVA_Q}"
        out.append({"rel": f"VA/M26-7/VA M26-7 Chapter {int(num):02d} {_safe(a['title'])}.pdf", "title": title,
                    "url": url, "version": a["modified"], "date": a["modified"][:10],
                    "fetch": lambda a=a, title=title: (_knowva_pdf(a["id"], title, a["modified"][:10]), a["modified"][:10])})
    return out


def _knowva_pdf(article_id: str, title: str, updated: str) -> bytes:
    body = _tag(_knowva_xml(f"article/{article_id}"), "content")
    if len(_plain(body)) < 500:
        raise ValueError(f"KnowVA article {article_id} came back empty")
    body = re.sub(r"<(script|style|iframe)\b.*?</\1>", "", body, flags=re.S | re.I)
    body = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", body)
    page = (f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>"
            "<style>body{font:10.5pt Georgia,serif;margin:0}table{border-collapse:collapse;width:100%}"
            "td,th{border:1px solid #999;padding:3px 5px;vertical-align:top}img{max-width:100%}</style>"
            f"<h1>{html.escape(title)}</h1><p>Source: VA KnowVA, Lenders Handbook (VA Pamphlet 26-7). "
            f"Last updated by VA {updated}. Saved by AskNick {date.today().isoformat()}.</p>{body}")
    return html_to_pdf(page)


def html_to_pdf(page: str) -> bytes:
    """Prints an HTML page to PDF with headless Edge (already on every Windows PC), in its own throwaway profile."""
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d) / "page.html", Path(d) / "out.pdf"
        src.write_text(page, encoding="utf-8")
        subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
                        f"--user-data-dir={Path(d) / 'profile'}", f"--print-to-pdf={out}", src.as_uri()],
                       capture_output=True, timeout=300)
        if not out.exists() or not out.read_bytes().startswith(b"%PDF"):
            raise RuntimeError("Edge didn't print the page to PDF")
        return out.read_bytes()


def va_circulars() -> list[dict]:
    """Every circular on VA's current list that hasn't been rescinded, each change saved alongside the original."""
    page = _page(VA_CIRCULARS)
    start = page.find('<h2><a id="20')
    if start < 0:
        raise ValueError("VA's circulars page has no year headings")
    today = date.today().isoformat()
    out = []
    for p in re.findall(r"<p>(.*?)</p>", page[start:], re.S):
        text = _plain(p)
        if not text.startswith("Circular"):
            continue
        status = re.findall(r"\(([^()]*\b(?:rescind|expire)[^()]*)\)", text, re.I)
        if status and "until rescinded" not in status[-1].lower():
            ends = find_date(status[-1])
            if "rescinded" in status[-1].lower() or (ends and ends <= today):
                continue
        subject = _plain((re.search(r"<strong>(.*?)</strong>", p, re.S) or [None, ""])[1])
        base, entry_date = "", ""
        for href, label in re.findall(r'<a href="([^"]+\.pdf)"[^>]*>(.*?)</a>', p, re.S | re.I):
            label = _plain(label)
            m = re.match(r"Circular (26-\d+-\d+)(?: \(Change (\d+)\))?(?: - (.+))?$", label)
            if m:
                base = base or m[1]
                entry_date = entry_date or find_date(m[3] or "")
                num, when = m[1] + (f" Change {m[2]}" if m[2] else ""), find_date(m[3] or "")
            elif base and label.lower().startswith("exhibit"):
                # Exhibits hold the actual tables (26-23-6's Exhibit B is the current funding fee table)
                num, when = f"{base} {_safe(label)}", entry_date
            else:
                continue
            url = urljoin(VA_CIRCULARS, href)
            if any(o["rel"] == f"VA/Circulars/VA Circular {num}.pdf" for o in out):
                continue   # a change's entry links the original again; the original's own entry comes later
            out.append({"rel": f"VA/Circulars/VA Circular {num}.pdf", "title": f"VA Circular {num}: {subject}",
                        "url": url, "version": url, "date": when, "fetch": lambda url=url: _download(url)})
    return out


def fannie_selling_guide() -> list[dict]:
    """Fannie publishes a dated PDF of the whole guide. Their site blocks automated downloads, so this only
    notices a new edition; Derrick downloads it into agency/Conventional/Fannie Mae/."""
    page = _page(FANNIE)
    m = re.search(r'href="([^"]*selling-guide-[^"]+)"[^>]*>\s*Download PDF Guide.*?Published:\s*([^<)]+)', page, re.S | re.I)
    if not m:
        raise ValueError("no Download PDF Guide link on the Selling Guide page")
    published = find_date(m[2]) or m[2].strip()
    return [{"manual": True, "title": "Fannie Mae Selling Guide", "url": urljoin(FANNIE, m[1]),
             "version": published, "date": published}]


def freddie_guide(today: date | None = None) -> list[dict]:
    """Freddie rebuilds its one-PDF guide with each bulletin, about monthly. guide.freddiemac.com's robots.txt shuts
    out everything but search engines, so nothing here touches it: from the 10th of each month the notice asks for a
    fresh copy until one dated that month or later (its first page says "Published on ...") is in
    agency/Conventional/Freddie Mac/."""
    today = today or date.today()
    y, m = (today.year, today.month) if today.day >= 10 else \
        ((today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12))
    return [{"manual": True, "title": "Freddie Mac Seller/Servicer Guide", "url": FREDDIE_PDF,
             "version": f"{y}-{m:02d}-01"}]


SOURCES = [
    {"key": "hud-4000", "name": "HUD Handbook 4000.1", "section": "FHA", "folder": "FHA/HUD 4000.1",
     "items": hud_handbook, "manages": True},
    {"key": "hud-ml", "name": "FHA Mortgagee Letters", "section": "FHA", "folder": "FHA/Mortgagee Letters",
     "items": hud_mortgagee_letters, "manages": True},
    {"key": "va-26-7", "name": "VA Pamphlet 26-7 (Lenders Handbook)", "section": "VA", "folder": "VA/M26-7",
     "items": va_lenders_handbook, "manages": True},
    {"key": "va-circ", "name": "VA Circulars", "section": "VA", "folder": "VA/Circulars",
     "items": va_circulars, "manages": True},
    {"key": "fannie", "name": "Fannie Mae Selling Guide", "section": "Conventional", "folder": "Conventional/Fannie Mae",
     "items": fannie_selling_guide, "manages": False},
    {"key": "freddie", "name": "Freddie Mac Seller/Servicer Guide", "section": "Conventional",
     "folder": "Conventional/Freddie Mac", "items": freddie_guide, "manages": False},
]


# ---------- state ----------
def load_state() -> dict:
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    for k, v in (("files", {}), ("sources", {}), ("updates", []), ("notices", {}), ("last_check", 0)):
        state.setdefault(k, v)
    return state


def _save(state: dict):
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def files() -> list[Path]:
    return sorted(p for p in DIR.rglob("*.pdf") if TEXT_DIR not in p.parents) if DIR.exists() else []


def section_of(path: Path) -> str:
    return path.relative_to(DIR).parts[0]


def info(filename: str) -> dict | None:
    """The state record for an agency file by its name, as a citation shows it."""
    for rel, rec in load_state()["files"].items():
        if rel.rsplit("/", 1)[-1] == filename:
            return {"rel": rel, **rec}
    return None


def label(rec: dict) -> str:
    """"HUD Handbook 4000.1 (Update 18), dated 2026-08-12, updated here 2026-11-12, not yet reviewed\""""
    parts = [rec.get("title") or rec["rel"]]
    if rec.get("date"):
        parts.append(f"dated {rec['date']}")
    parts.append(f"updated here {rec.get('downloaded', '?')}")
    if not rec.get("reviewed"):
        parts.append("not yet reviewed")
    return ", ".join(parts)


# ---------- text and change write-ups ----------
def extract(path: Path) -> list[str]:
    import pypdfium2 as pdfium
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            return [pdf[i].get_textpage().get_text_range().replace("\r\n", "\n").replace("\r", "\n")
                    for i in range(len(pdf))]
        finally:
            pdf.close()


def _text_path(rel: str) -> Path:
    return TEXT_DIR / (rel.replace("/", "__") + ".json")


def pages(path: Path) -> list[str]:
    """Page texts for an agency file (cached, keyed to the file's hash)."""
    rel, h = path.relative_to(DIR).as_posix(), _hash(path)
    tp = _text_path(rel)
    if tp.exists():
        data = json.loads(tp.read_text(encoding="utf-8"))
        if data.get("hash") == h:
            return data["pages"]
    texts = extract(path)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    tp.write_text(json.dumps({"hash": h, "pages": texts}), encoding="utf-8")
    return texts


# HUD 4000.1 is 1,872 pages and about 500 of them are about originating a forward mortgage. The rest (servicing,
# Title I, reverse mortgages, claims, quality control, the index) answered gift questions with the Title I and
# reverse mortgage gift rules, so those pages rank after the origination ones.
_HUD_ORIGINATION = re.compile(r"II\. ORIGINATION[^\n]*\n(?:A\. Title II Insured Housing Programs Forward|"
                              r"C\. Condominium|[DF]\. Appraiser)")


# Freddie's guide is 3,433 pages; origination is Series 4000-6000. Its servicing chapters (8000-9000, about 1,300
# pages) repeat "Chapter 7 bankruptcy" and the like in a servicing sense, so they rank last too.
_FREDDIE_CHAPTER = re.compile(r"Seller/Servic\w* Guide\s+Chapter\s+(\d)\d{3}")


def off_topic(text: str) -> bool:
    """A HUD handbook page outside forward mortgage origination, or a Freddie guide page outside Series 4000-6000
    (each one's running header says which part it's in)."""
    head = text[:600]
    if "Handbook 4000.1" in head:
        return not _HUD_ORIGINATION.search(head)
    m = _FREDDIE_CHAPTER.search(text)
    return bool(m) and m[1] not in "456"


_pages_cache: dict = {}


def page_text(filename: str, page: int) -> str:
    """One page of an agency file by name (1-based), "" if there's no such page. Kept in memory per file copy."""
    path = next((p for p in files() if p.name == filename), None)
    if not path:
        return ""
    key = (str(path), path.stat().st_mtime)
    if key not in _pages_cache:
        _pages_cache.clear()   # one big handbook at a time is plenty
        _pages_cache[key] = pages(path)
    texts = _pages_cache[key]
    return texts[page - 1] if 0 < page <= len(texts) else ""


def _norm(line: str) -> str:
    return re.sub(r"[^a-z0-9%$.]+", " ", line.lower()).strip()


def _lines(texts: list[str]) -> list[tuple[int, str, str]]:
    out = []
    for n, t in enumerate(texts, start=1):
        for line in t.split("\n"):
            k = _norm(line)
            if len(k) >= 20 and re.search(r"[a-z]{3}", k):
                out.append((n, line.strip(), k))
    return out


def page_diff(old: list[str], new: list[str], limit: int = 60) -> dict:
    """What changed between two copies of a guide, as lines added and removed. Pages shift between editions, so
    lines are matched anywhere in the document, not page to page. Lines repeated on many pages (running headers
    and footers with the edition date) are ignored, or every page would look changed."""
    a, b = _lines(old), _lines(new)

    def boiler(lines, n_pages):
        seen = {}
        for p, _, k in lines:
            seen.setdefault(k, set()).add(p)
        return {k for k, ps in seen.items() if len(ps) > max(3, n_pages * 0.3)}
    skip = boiler(a, len(old)) | boiler(b, len(new))
    old_keys, new_keys = {k for _, _, k in a}, {k for _, _, k in b}
    added = [(p, line) for p, line, k in b if k not in old_keys and k not in skip]
    removed = [(p, line) for p, line, k in a if k not in new_keys and k not in skip]
    return {"added": len(added), "removed": len(removed), "pages": sorted({p for p, _ in added}),
            "added_lines": added[:limit], "removed_lines": removed[:limit]}


# ---------- the check ----------
def _archive(path: Path, today: str) -> Path:
    dest = ARCHIVE / today / path.relative_to(DIR)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return dest


def check(log=print, on_index=None) -> list[dict]:
    """Checks every source, downloads what's new, writes up the changes and updates the search index.
    Returns the new update entries. Only one check runs at a time."""
    if not _run_lock.acquire(blocking=False):
        log("A check is already running.")
        return []
    try:
        state, today = load_state(), date.today().isoformat()
        moved = []
        for src in SOURCES:
            log(f"Checking {src['name']}…")
            try:
                items = src["items"]()
                if not items:
                    raise ValueError("found nothing, the page layout may have changed")
            except Exception as e:
                log(f"{src['name']}: couldn't check ({e})")
                state["sources"][src["key"]] = {**state["sources"].get(src["key"], {}), "error": str(e)[:300],
                                                "checked": time.time()}
                continue
            got = 0
            for it in items:
                if it.get("manual"):
                    # The newest copy in the folder answers it too, notice or not (a guide saved before any
                    # notice went up, dated from its own first page).
                    have = max([state["sources"].get(src["key"], {}).get("have_version") or ""]
                               + [r.get("date") or "" for rel, r in state["files"].items()
                                  if rel.startswith(src["folder"] + "/")])
                    if have < it["version"] and src["key"] not in state["notices"]:
                        state["notices"][src["key"]] = {"title": it["title"], "version": it["version"],
                                                        "url": it["url"], "folder": src["folder"], "seen": today}
                        log(f"New {it['title']} published {it['version']}: download it into agency/{src['folder']}/")
                    continue
                path, rec = DIR / it["rel"], state["files"].get(it["rel"], {})
                if path.exists() and rec.get("version") == it["version"]:
                    continue
                try:
                    data, doc_date = it["fetch"]()
                except Exception as e:
                    log(f"{it['title']}: download failed ({e})")
                    continue
                if path.exists():
                    if hashlib.sha256(data).hexdigest() == _hash(path):
                        state["files"][it["rel"]] = {**rec, "version": it["version"]}
                        continue
                    _archive(path, today)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                state["files"][it["rel"]] = {**rec, "title": it["title"], "url": it["url"], "version": it["version"],
                                             "date": it["date"] or doc_date, "source": src["key"]}
                got += 1
            if src["manages"]:
                wanted = {it["rel"] for it in items}
                for p in sorted((DIR / src["folder"]).glob("*.pdf")):
                    rel = p.relative_to(DIR).as_posix()
                    if rel not in wanted:
                        _archive(p, today)
                        moved.append(rel)
                        log(f"Archived {p.name}: no longer listed")
            state["sources"][src["key"]] = {**state["sources"].get(src["key"], {}), "error": "",
                                            "checked": time.time(), "count": len(items)}
            if got:
                log(f"{src['name']}: {got} new or changed")
        new = _process(state, today, moved, log)
        state["last_check"] = time.time()
        _save(state)
        if new or moved:
            log("Updating the search index…")
            from guidebot.ingest.indexer import sync_index
            sync_index(log=log)
            if on_index:
                on_index()
        log(f"Done. {len(new)} new or changed, {len(moved)} archived." if new or moved else "Done. Nothing changed.")
        return new
    finally:
        _run_lock.release()


def _process(state: dict, today: str, moved: list[str], log) -> list[dict]:
    """Every agency PDF whose bytes changed since last time (downloaded or saved by hand) gets its text re-read,
    compared with the old text, and an update entry."""
    new = []
    for path in files():
        rel = path.relative_to(DIR).as_posix()
        rec, h = state["files"].get(rel, {}), _hash(path)
        if rec.get("hash") == h:
            continue
        tp = _text_path(rel)
        old = json.loads(tp.read_text(encoding="utf-8"))["pages"] if tp.exists() and rec.get("hash") else None
        log(f"Reading {path.name}…")
        try:
            texts = pages(path)
        except Exception as e:
            log(f"{path.name}: couldn't read it ({e})")
            continue
        src = next((s for s in SOURCES if rel.startswith(s["folder"] + "/")), None)
        if src and not src["manages"]:   # saved by hand: it answers the "new edition" notice
            notice = state["notices"].pop(src["key"], None)
            if notice:
                state["sources"].setdefault(src["key"], {})["have_version"] = notice["version"]
                # The guide's own first page dates it best; Freddie's notice version is only a reminder date.
                rec = {**rec, "title": notice["title"], "url": notice["url"],
                       "date": find_date(texts[0][:1500] if texts else "") or notice["version"]}
        state["files"][rel] = {**rec, "hash": h, "title": rec.get("title") or path.stem, "section": section_of(path),
                               "downloaded": today, "reviewed": False, "pages": len(texts)}
        if not rec.get("date"):
            state["files"][rel]["date"] = find_date(texts[0][:1500] if texts else "")
        entry = {"id": f"{today}-{hashlib.sha1(rel.encode()).hexdigest()[:8]}-{h[:6]}", "date": today, "file": rel,
                 "title": state["files"][rel]["title"], "kind": "changed" if old is not None else "new",
                 "pages": len(texts), "diff": page_diff(old, texts) if old is not None else None, "reviewed": False}
        state["updates"].insert(0, entry)
        new.append(entry)
    for rel in moved:
        rec = state["files"].pop(rel, None)
        state["updates"].insert(0, {"id": f"{today}-{hashlib.sha1(rel.encode()).hexdigest()[:8]}-gone", "date": today,
                                    "file": rel, "title": (rec or {}).get("title", rel), "kind": "removed",
                                    "pages": 0, "diff": None, "reviewed": False})
    for rel in [r for r in state["files"] if not (DIR / r).exists()]:
        del state["files"][rel]
    del state["updates"][300:]
    return new


def mark_reviewed(update_id: str) -> dict:
    """Marks one update reviewed, or every open one with id "*" (the first download is 127 of them)."""
    if not _run_lock.acquire(blocking=False):   # a check saves the whole state when it ends
        return {"error": "A check is running. Try again when it's done."}
    try:
        state = load_state()
        entries = [u for u in state["updates"] if (u["id"] == update_id or update_id == "*") and not u["reviewed"]]
        if not entries and update_id != "*":
            return {"error": "Update not found."}
        for entry in entries:
            entry["reviewed"] = True
        for rel, rec in state["files"].items():
            # a file counts as reviewed once no update for its current copy is still open
            if not any(u["file"] == rel and not u["reviewed"] and u["id"].endswith(rec["hash"][:6])
                       for u in state["updates"]):
                rec["reviewed"] = True
        _save(state)
        return {"ok": True}
    finally:
        _run_lock.release()


def overview() -> dict:
    state = load_state()
    srcs = [{"key": s["key"], "name": s["name"], "section": s["section"], "manual": not s["manages"],
             **state["sources"].get(s["key"], {}),
             "files": sum(1 for r in state["files"] if r.startswith(s["folder"] + "/"))} for s in SOURCES]
    return {"last_check": state["last_check"], "running": _run_lock.locked(), "sources": srcs,
            "notices": list(state["notices"].values()), "updates": state["updates"][:100],
            "open": sum(1 for u in state["updates"] if not u["reviewed"])}


def start_scheduler(on_index=None, log=print):
    """A background thread that runs check() when the last one is a week old. It looks once an hour, so a PC that
    was off on the day just checks the next time the server is up."""
    def loop():
        time.sleep(600)   # let the server settle after a restart
        while True:
            try:
                if time.time() - load_state()["last_check"] >= EVERY:
                    check(log=lambda m: log(f"[agency] {m}"), on_index=on_index)
            except Exception as e:
                log(f"[agency] check failed: {e}")
            time.sleep(3600)
    threading.Thread(target=loop, daemon=True, name="agency-check").start()
