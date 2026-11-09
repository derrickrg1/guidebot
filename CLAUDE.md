# Guideline Assistant (guidebot)

Local AI chat bot for mortgage guidelines and LTV matrices. Built for Derrick, Loan Set-Up and Bank
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
* 100% local: Ollama only, no cloud APIs. Free/open tools only (he moved off Microsoft for cost).
* Sandbox phase on his personal machine. Real borrower data never goes in here.

## Stack

* Models: `qwen3:14b` (chat, structured JSON via Ollama `format=`), `nomic-embed-text` (embeddings).
* pdfplumber, pandas, ChromaDB + BM25 (hybrid search), Streamlit UI.
* Run: `.venv\\Scripts\\activate` then `streamlit run ui/app.py`. Tests: `pytest`.

## Layout

* `docs/<Program>/` source PDFs. Folder name = program. Folders mirror how NDM lists products:
FHA, Conventional, VA, NonQM 2nd / Express DSCR / Bank Statements / 1099 / Full Doc / P\&L,
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
* `guidebot/retrieval.py` + `ingest/indexer.py` only for "All programs (search)" mode. Page-level chunks.
* `ui/app.py` chat. `ui/pages/1\_Verify\_Documents.py` PDF page image beside editable extracted content.

## Why it's built this way (his past problems)

1. Matrices are real tables, and older tools mangled them (merged cells, stacked headers). Fixed with
`parse\_matrix` plus human verification.
2. Older tools pulled the first matching snippet, not the full answer. Fixed by reading the whole program
(Ignite 1099 is \~12k chars) and making overlays an explicit, checked step.

## Current status

* 18 tests passing. Parser verified against the real Ignite 1099 PDF: all 26 matrix rows match.
* `Ignite 1099.pdf` and `Ignite 1099 Expanded.pdf` are byte-identical. Likely true for the other Ignite pairs too.
App skips exact duplicates automatically.
* Derrick has Ollama, the models, and the venv set up. Next he verifies Ignite 1099 and tests scenarios.

## Open questions to confirm with underwriting (flagged in answers, not guessed)

* Loan amount exactly $1,000,000: matrix says "< $1,000,000" and next bracket starts at $1,000,001.
* Property overlays marked \* ("Reduce LTV by 5% from maximum on Investment Properties"): applied as a
separate 5% reduction. Confirm interpretation.
* Whether multiple "5% reduction" items stack.

## Next steps

1. Verify and test Ignite 1099 with known scenarios, including overlays (condo, 2-4 units, IO, FTHB no rental history).
2. Run `parse\_matrix` on every other program's PDF. NonQM and DSCR matrices may have different layouts
(DSCR ratio dimension, prepay columns). Fix the parser per layout and add a test for each shape.
3. If a layout can't be parsed reliably, add a vision fallback (Qwen2.5-VL in Ollama) that transcribes the page
image to a grid, still gated by human verification.
4. Optional: a scenario form (dropdowns) alongside chat for the most common lookups.

## Related, parked

* `rev-ai` bank statement analyzer (separate project, built earlier). Not the current focus.

