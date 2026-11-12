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
`-m guidebot.server` process; the loop brings it back in 5 to 30 seconds. The cloudflared
service tunnels it to asknick.derrickgarcia.dev and guidebot.derrickgarcia.dev, both behind Cloudflare Access
(policy "Derrick Access": his Gmail and nwilson@revofunding.com).
* Citation chips open the PDF in a right-side panel (`ui/viewer.js`, PDF.js 3.11.174 from cdnjs, works on
iPhone). It finds the cited line on the page by word overlap and marks it; if the cited page has no match it
tries one page either side and says "closest match on p. X". Falls back to the browser's PDF viewer on error.
`ui/md.js` is the shared markdown/citation renderer for both pages.
* Admin console: `/admin` (`ui/admin.html`, `guidebot/admin.py`) replaces the Streamlit Verify page: doc
statuses, PDF page beside editable matrix/text, re-read, approve docs and overlay rules. Only his Gmail via
Cloudflare Access, or direct from this PC. Claude never clicks Approve. Tabs: Documents, Chats (every user's
chats, read only), Index (Update index, admin only now).
* Chat history: `guidebot/history.py`, SQLite at `data/chats.db` (git-ignored), keyed to the Cloudflare Access
email ("local" on this PC). The server saves each turn after the answer finishes; people see only their own.
* Search index (ChromaDB in `db/`): update it only through the admin Index tab, never from a second process
while the server runs. Two processes corrupted it on 2026-11-12 (broken copy in `db.broken-2026-11-12/`, rebuilt).
* Mode names on the page: Guidelines and Scenario (was "Deal check"; the radio value is still `deal`).
* Streamlit (old): `streamlit run ui/app.py`. Tests: `.venv\Scripts\python.exe -m pytest` (system Python lacks ollama).
Model race: `python -m tools.compare_models <model> ...`.

## Layout

* `docs/<Program>/` source PDFs. Folder name = program. Folders mirror how NDM lists products:
Conventional; FHA, VA (Government); Express 1099 / 2nd / Bank Statements / DSCR / Full Doc / P\&L (renamed from
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
* `guidebot/retrieval.py` + `ingest/indexer.py`: search for the long guides above. Page-level chunks.
* `ui/app.py` chat. `ui/pages/1\_Verify\_Documents.py` PDF page image beside editable extracted content.

## Why it's built this way (his past problems)

1. Matrices are real tables, and older tools mangled them (merged cells, stacked headers). Fixed with
`parse\_matrix` plus human verification.
2. Older tools pulled the first matching snippet, not the full answer. Fixed by reading the whole program
(Ignite 1099 is \~12k chars) and making overlays an explicit, checked step.

## Current status

* 107 tests passing. Every Express/Ignite matrix parses with clean headers; one test per layout shape.
* Overlay rules live in `overlays/<Program>.json` for all five Ignite programs (quotes checked word for word by a
test) and must be approved on the Verify page before Python applies them. None approved yet, so deal answers stop
at the matrix cell. Express programs have no rules files yet. The race (`tools/compare_models.py`) has hand-worked
answers per Ignite program; DSCR is Q\&A only until its matrix is re-read.
* The page groups programs as Conventional / Government / NonQM (`GROUPS` in `ui/guidebot.html`).
* Revo's own v9 matrices (`../Matrices/Revo Matrices/v9`) extract cleanly; the Ignite 1099 grid matches
guidebot's 234/234. Possible later use as a reading copy (split Express/Ignite halves first); NDM PDFs stay
the source for numbers.
* The Ignite pairs (`X.pdf` and `X Expanded.pdf`) are byte-identical. App skips exact duplicates automatically.
* `guidebot/crosscheck.py` compares parsed grids to the hand-typed grids in the Revo site repo
(`../site/tools/matrices/build_*.py`, checked there by `verify.py`), matched by PDF hash. 7 grids match cell
for cell. A mismatch blocks numbers and approval. 1099, P\&L and Units+ have no site grid yet.
* `header_problems` blocks numbers from matrices with shifted/numbered headers. Approved JSONs saved by the old
parser (Ignite Express DSCR, Express P\&L) need re-verifying with "Re-read the PDF".
* Unverified programs: full-doc answers are told not to state any LTV (`NO\_LTV\_RULE`).
* Express Full Doc was the 2023 Premier matrix; replaced with Express Full Doc 3.2.2026 (old one in `archive/`).
* FHA, VA (from site originals) and Express 2nd (Express 8.15.2026, Premier 3.2.2026) added. They parse as text
only, no LTV grid yet. Conventional still empty (only "Old" PDFs exist, currency unconfirmed).
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
4. LTV grids for FHA, VA and 2nds (2nds have doc-type columns), each with a test, or a vision fallback.
5. Optional: Express vs Ignite comparison sheets and the Loan Setup SOP as programs; a scenario form.

## Related, parked

* `rev-ai` bank statement analyzer (separate project, built earlier). Not the current focus.

