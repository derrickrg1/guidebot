# AskNick (guidebot)

AI chat bot for mortgage guidelines and LTV matrices, shown to users as AskNick (code and folder stay `guidebot`). Built for Derrick, Loan Set-Up and Bank
Statement Analyst at Revolution Funding (Nations Direct Mortgage). Started in a claude.ai chat, continued here.

## How to work with Derrick

* Never use em dashes. Use commas, periods, or rewrite the sentence.
* Casual and direct. No formal or AI-sounding phrasing.
* Execution over previews: build it, test it, then summarize briefly.
* He's on Windows (PowerShell), Python 3.11, Ollama 0.40+. Give PowerShell commands, not bash.
* Explain new commands in one line each when he's running them for the first time.

## Hard rules for this project

* Accuracy is the whole point. A wrong LTV is worse than no answer.
* The AI never produces LTV numbers. It maps a scenario to a matrix row/column and identifies overlays.
Python does the lookup and math (`matrix\_lookup.py`).
* LTV numbers are blocked for any document not approved on the Verify Documents page.
* A number for a specific deal only comes from the matrix lookup (`looks_like_deal` routes it there whatever
the toggles say). A program whose overlay rules aren't approved stops at the matrix cell, no final number.
* Chat runs on the Claude API (Derrick chose this 2026-11-12, replacing "Ollama only"). Questions and guideline
text go to Anthropic; embeddings and the qwen3 fallback stay local. Otherwise free/open tools only.
* Sandbox phase on his personal machine and personal Anthropic account. Real borrower data never goes in here.

## Stack

* Models (`guidebot/llm.py`, picked in `config.py`): `claude-sonnet-5-5` (Sonnet, Haiku and Opus all scored 22/22 in the race; Sonnet was fastest) when `ANTHROPIC_API_KEY` is in `.env`
(git-ignored; key lives in the AskNick workspace of his Claude Console, 90-day expiry), else `qwen3:14b`.
If Claude is down or the key is bad, answers fall back to qwen3. `GUIDEBOT_MODEL` / `GUIDEBOT_EFFORT` in `.env`
override. `nomic-embed-text` (embeddings, Ollama).
* pdfplumber, pandas, ChromaDB + BM25 (hybrid search), Streamlit UI.
* Main UI: `ui/guidebot.html` served by `python -m guidebot.server` on 127.0.0.1:8600. A Windows scheduled
task "Guidebot server" runs `scripts/run_server.ps1` at login, which relaunches the server if it ever exits
(log in `%LOCALAPPDATA%\guidebot\server.log`, with restart markers). To restart by hand, kill the python
`-m guidebot.server` process; the loop brings it back in 5 to 30 seconds. The task runs it through
`conhost.exe --headless` (since 2026-11-12: `-WindowStyle Hidden` still opened a Windows Terminal window, and closing
it killed the loop), plus a trigger every 5 minutes (IgnoreNew) that restarts a dead loop. The cloudflared
service tunnels it to asknick.derrickgarcia.dev and guidebot.derrickgarcia.dev, both behind Cloudflare Access
(policy "Derrick Access": his Gmail, nwilson@ and calderon@revofunding.com; chat only, admin is his Gmail alone).
* Citation chips open the PDF in a right-side panel (`ui/viewer.js`, PDF.js 3.11.174 from cdnjs, works on
iPhone). It finds the cited line on the page by word overlap and marks it; if the cited page has no match it
tries one page either side and says "closest match on p. X". Falls back to the browser's PDF viewer on error. A quote in the answer ("Per DU") is
looked for word for word first; short quotes scored nothing as words and the mark landed on the wrong row.
`ui/md.js` is the shared markdown/citation renderer for both pages.
* Program colors (`PROGRAMS` in `ui/md.js`) come from the Revo site's `scripts/pages/accents.json` (the lifted
"hero" tones; FHA Teal lightened; VA's dot is a tiny US flag, an SVG in `md.js`). Express and Ignite share a product's color. Dots show on
citation chips, table program cells, "Term:" rows, the picker and the viewer title. The header button toggles a
wide chat column (remembered per browser).
* Settings (account row, bottom left; header gear on a phone or with the sidebar hidden): text size (scales chat
messages via `--ts`; sizes inside messages are in em), chat width, program colors on/off. The sidebar collapses
(panel icon by the logo). All kept per browser in localStorage (`asknick.*`).
* Fonts: Lato everywhere (both pages), Bodoni Moda only for the ASKNICK wordmark. Matte page: soft vignette plus
an SVG grain; flat translucent answer cards; the sidebar casts a shadow over the chat; section tabs centered.
* Admin console: `/admin` (`ui/admin.html`, `guidebot/admin.py`) replaces the Streamlit Verify page: doc
statuses, PDF page beside editable matrix/text, re-read, approve docs and overlay rules. Only his Gmail via
Cloudflare Access, or direct from this PC. Claude never clicks Approve. Tabs: Documents, Chats (every user's
chats, read only), Index (Update index, admin only now), Updates (agency guide changes, below). Claude never
clicks Mark reviewed either.
* Agency guides (`guidebot/agency.py`, files in `agency/`, git-ignored): the general guideline under NDM's FHA,
VA and Conventional sheets. HUD 4000.1 + single family Mortgagee Letters from this year and last; VA Pamphlet
26-7 (moved to KnowVA, pulled one chapter at a time from its public JSON API and printed to PDF with headless
Edge; only the copy in force today) + active VA Circulars; Fannie Mae Selling Guide. The server checks weekly
(thread started in `server.main`, first look 10 min after start, then hourly for a week-old `last_check`); the
admin Updates tab has Check now. New/changed files: old copy to `archive/agency/<date>/`, text re-read
(pypdfium2 under `PDFIUM_LOCK`), a write-up of lines added/removed (running headers ignored), index updated
inside the server (indexer `_lock`). Files the agency stops listing are archived. Answers use new text right
away; cited unreviewed guides get a "hasn't been reviewed yet" line from Python. A Mortgagee Letter or Circular
newer than the handbook wins over it; NDM's sheet wins over all (`RULES`). Fannie blocks automated downloads
(the classifier stopped a workaround, rightly): the check only posts a notice, Derrick saves the PDF into
`agency/Conventional/Fannie Mae/` and the next check reads it. Loaded 2026-11-12: Selling Guide published 11/4/2026, 1,199 pages, from the link Derrick found (`singlefamily.fanniemae.com/media/48416/display`; the page's own Download link 403s). Its pages are the general guideline for Fannie programs only (Fannie Mae, HomeReady, RefiNow); Freddie programs (`chat.FREDDIE`) get Freddie's Seller/Servicer Guide instead (`agency_folder`): the whole current guide in one PDF (3,433 pages as of Bulletin 2026-15), link `guide.freddiemac.com/ci/okcsFattach/get/1002095_2` from Derrick. guide.freddiemac.com's robots.txt disallows bots, so nothing fetches it: from the 10th of each month the Updates tab asks for a fresh copy until one dated that month is saved in `agency/Conventional/Freddie Mac/`. Its servicing chapters (Series 7000-9000 and
1000-3000, about 1,800 pages) rank last (`agency.off_topic`), like HUD's non-origination pages. Indexed as program "<Section> Agency".
`chat._agency_hits` takes 6 handbook pages and 3 letter/circular pages separately (ML 2026-11 crowded the
handbook out and the 500 MDCS floor went missing), and each handbook page brings the next one along (that rule
starts on p. 176, the standard is on p. 177). A newer letter is flagged "Newer agency guidance (supersedes
handbook)", never "Overlay". The top 3 handbook hits also bring the previous page (FHA gift donor list starts a page early); HUD pages outside forward origination rank last (`agency.off_topic`, Title I/HECM answered a gifts question once). Every VA Circular loaded is in force and beats the Pamphlet chapter (Ch. 8 fee table predates Circular 26-23-6), and a circular brings its exhibits (`_exhibits`). Shortened citations get their full filename back (`_fix_agency_cites`); filenames carry no commas (`_safe`). First load 2026-11-12: 127 files, 2,551 pages (4000.1 Update 18 is 1,872).
* Chat history: `guidebot/history.py`, SQLite at `data/chats.db` (git-ignored), keyed to the Cloudflare Access
email ("local" on this PC). The server saves each turn after the answer finishes; people see only their own.
* Search index (ChromaDB in `db/`): update it only through the admin Index tab, never from a second process
while the server runs. Two processes corrupted it on 2026-11-12 (broken copy in `db.broken-2026-11-12/`, rebuilt).
* Mode names on the page: Guidelines and Scenario (was "Deal check"; the radio value is still `deal`).
* Streamlit (old): `streamlit run ui/app.py`. Tests: `.venv\Scripts\python.exe -m pytest` (system Python lacks ollama).
Model race: `python -m tools.compare_models <model> ...`.

## Layout

* `docs/<Program>/` source PDFs. Folder name = program. Folders mirror how NDM lists products:
Conventional (one folder per sheet since 2026-11-12: `Conventional Fannie Mae`, `Conventional HomeReady`, ... grouped Fannie Mae /
Freddie Mac in the picker); FHA (`FHA Standard`, `FHA Streamline`), VA (`VA Standard`, `VA Cash-Out`, `VA IRRRL`). Approvals moved
with the files. A FHA/VA deal with no program picked runs every sheet in the tab through `gov_matrix`; Express 1099 / 2nd / Bank Statements / DSCR / Full Doc / P\&L (renamed from
"NonQM ..." 2026-11-12, approvals moved with them; `NonQM Shared` keeps its name),
Ignite 1099 / Bank Statements / Express DSCR / Full Doc / P\&L.
* `guidebot/extract\_pdf.py` PDF to clean pages. `parse\_matrix` rebuilds NDM matrices: compacts pdfplumber's
None/empty cells, fills merged loan amount cells down, combines header rows ("Investment Property / Cash Out").
Non-matrix tables become `cell | cell` rows; text outside tables extracted separately.
* `guidebot/library.py` programs, duplicate detection (by file hash), verified storage in `verified/\*.json`
keyed to the source file hash (file changes flip status to "changed").
* `guidebot/matrix\_lookup.py` range dims (loan amount brackets), tier dims (FICO rounds down), text dims.
`apply\_adjustments`: caps take the lower value, reductions subtract, ineligible wins.
* `guidebot/answer.py` scenario mode (criteria JSON, Python lookup, overlay JSON with quotes checked word for
word against the doc) and full-document Q\&A mode (whole program in context, not chunks).
* All-programs guideline questions (`chat._broad_answer`): every program sheet up to `BROAD_SHEET_MAX` (20k chars,
the Express/Ignite sheets) is read whole in one cached system block, the shared guide is routed to its sections,
and longer guides (FHA, VA, 2nds) are searched. Before 2026-11-12 it read only the shared guide and missed
program-sheet rules like Ignite DSCR's 660 first time investor minimum. Never states LTVs.
* A program sheet is an overlay. If it doesn't state a requirement, there's no overlay and the general guideline
(shared NonQM guide, or the program's own UW guide like 2nds) applies: answers say "No overlay, general guideline
applies" with that rule, never "not addressed on the sheet" (rule in `RULES`/`BROAD_SYSTEM`). When the sheet's
rule differs from the general guideline, answers flag "Overlay (supersedes general guideline)" and show the general
rule it replaces, cited. Broad questions also
search the shared guide's pages, since the TOC router can miss the section holding the default rule.
* `guidebot/retrieval.py` + `ingest/indexer.py`: search for the long guides above. Page-level chunks.
* Shorthand (`TERMS` in `section_reader.py`: NOD, DIL, BK, FTHB...) is spelled out after the question for routing,
search and the answer prompt; deal lookups still get the original words. "NOD" used to route to condo HOA pages and
miss §7.5 (2026-11-12). A follow-up also reloads the shared guide sections earlier answers cited (`cited_pages`),
so the bot no longer calls its own earlier citation wrong.
* `ui/app.py` chat. `ui/pages/1\_Verify\_Documents.py` PDF page image beside editable extracted content.

## Why it's built this way (his past problems)

1. Matrices are real tables, and older tools mangled them (merged cells, stacked headers). Fixed with
`parse\_matrix` plus human verification.
2. Older tools pulled the first matching snippet, not the full answer. Fixed by reading the whole program
(Ignite 1099 is \~12k chars) and making overlays an explicit, checked step.

## Current status

* 149 tests passing. Every Express/Ignite matrix parses with clean headers; one test per layout shape.
* Overlay rules live in `overlays/<Program>.json` for all five Ignite programs (quotes checked word for word by a
test) and must be approved on the Verify page before Python applies them. None approved yet, so deal answers stop
at the matrix cell. Express programs have no rules files yet. The race (`tools/compare_models.py`) has hand-worked
answers per Ignite program; DSCR is Q\&A only until its matrix is re-read.
* Section tabs across the top: NonQM, Conventional, FHA, VA (`SECTIONS` in `ui/guidebot.html` and `chat.py`).
A tab narrows the picker and the chat server side (no program picked = every program in that tab only; the shared
NonQM guide only with NonQM). NonQM's picker groups Express + Ignite / Express / Ignite; one-program tabs pick it.
Switching tabs mid-chat starts a new chat; a section-wide chat is saved under the section name.
* Revo's own v9 matrices (`../Matrices/Revo Matrices/v9`) extract cleanly; the Ignite 1099 grid matches
guidebot's 234/234. Possible later use as a reading copy (split Express/Ignite halves first); NDM PDFs stay
the source for numbers.
* The Ignite pairs (`X.pdf` and `X Expanded.pdf`) are byte-identical. App skips exact duplicates automatically.
* `guidebot/crosscheck.py` checks every parsed grid against a second source. First `guidebot/site_grids.py`: the
built HTML matrices in the GitHub site repo (`config.SITE_REPO`, `revo-funding-site/assets/matrices`, tables
classed `grid pg-ex|pg-ig`), compared by position only when both effective dates match. The old hand-typed
grids (`../site/tools/matrices/build_*.py`, matched by PDF hash) answer only where the HTML has nothing.
A mismatch blocks numbers and approval. Every Express/Ignite grid matches except (2026-11-12):
  * Express 1099 row $2,000,001-$3,000,000 / 700, Primary Cash Out: NDM PDF (printed 7/31/2026, "Adding AR") says
    65%, the site's copy of the 3.2.2026 sheet says 60%. Waiting on Derrick (`KNOWN_SITE_DIFFS` in tests).
  * Express DSCR Units+: the approved JSON was mangled (FICO is the merged cell there, the parser used to fill loan
    amount instead). Parser fixed (`_fill_lead`); Derrick needs to "Re-read the PDF" and re-approve.
  The site's Express "originals" are Revo-branded copies, not NDM's files, so the site is a check, never the source.
* `header_problems` blocks numbers from matrices with shifted/numbered headers. Approved JSONs saved by the old
parser (Ignite Express DSCR, Express P\&L) need re-verifying with "Re-read the PDF".
* Unverified programs: full-doc answers are told not to state any LTV (`NO\_LTV\_RULE`).
* Express Full Doc was the 2023 Premier matrix; replaced with Express Full Doc 3.2.2026 (old one in `archive/`).
* FHA (Standard, Streamline) and VA (Standard, Cash-Out > 90%, IRRRL) LTV tables: `guidebot/gov_matrix.py`. These
sheets are rows (purpose, min FICO, max loan amount, max LTV, sometimes DTI/AUS), not grids, so cells are placed by
position on the page. Lookup is Python only: every row the deal fits, best LTV first, all the sheet's footnotes
shown (funding fee, 203(h), AZ/UT). "96.5%, 100%³" headlines 96.5 (100% is the footnoted case). IRRRL/Streamline
rows say NA (no LTV limit). Stops at the matrix: the sheets' credit/property pages aren't applied. All five match
the site's HTML tables (`site_grids.gov_check`). The approved JSONs were saved before this, with no table, so
Derrick needs to Re-read and approve each; until then the VA/FHA tabs say so instead of giving a number.
* Conventional LTV tables: `guidebot/conv_matrix.py` (2026-11-12). Each sheet's page 1 table: occupancy, purpose,
  min FICO, units, Conforming and High Balance (Freddie: Super Conforming) columns, plus the per-unit loan limits that
  pick the column. Tried before the gov parser (HomeOne's one-column table also reads as gov). Footnotes stop at the
  "Matrix Updates" legend. Lookup is Python only; "Fixed 97%" notes fixed rate only, ">95%" points to the sheet's
  95.01-97% rules. A picked program reads its sheet; section-wide deals go through `_compare` per program. Approved
  JSONs from before this have only page 2's ARM caps grid, so each sheet needs Re-read and approve; until then the
  answer says so. Tests in `tests/test_conv_matrix.py`.
* Express 2nd (Express 8.15.2026, Premier 3.2.2026) parses as text only, no LTV grid yet.
* NonQM Shared guidelines PDF changed since it was approved: needs a re-check.

## Open questions to confirm with underwriting (flagged in answers, not guessed)

* Loan amount exactly $1,000,000: matrix says "< $1,000,000" and next bracket starts at $1,000,001.
* Property overlays marked \* ("Reduce LTV by 5% from maximum on Investment Properties"): applied as a
separate 5% reduction. Confirm interpretation.
* Whether multiple "5% reduction" items stack.

## Next steps

1. Derrick re-verifies the flagged docs and verifies the new ones, then tests scenarios with overlays.
2. Port the site's calculators (`../site/assets/dscr.js`, `va-irrrl.js`, `fha-streamline.js`, `buydown.js`) to
Python so DSCR picks the right DSCR tier/matrix and IRRRL/streamline answers are computed, not generated.
3. Use the site's `pdf\_rows.py` to keep green NDM-overlay and yellow recent-update highlights from FHA/VA sheets.
4. LTV grid for 2nds (doc-type columns), with a test, or a vision fallback.
5. Optional: Express vs Ignite comparison sheets and the Loan Setup SOP as programs; a scenario form.

## Related, parked

* `rev-ai` bank statement analyzer (separate project, built earlier). Not the current focus.

