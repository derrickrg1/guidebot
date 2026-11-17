# Change tracker

Watches FHA, VA, Fannie Mae and Freddie Mac for guideline changes and writes each release as plain rule changes
to `data/agency_changes.jsonl`. The What's new banner in AskNick reads that file.

## How it works

| Agency | What it watches | How it gets it |
|---|---|---|
| FHA | HUD 4000.1, single family Mortgagee Letters | The server's weekly agency check downloads them |
| VA | Pamphlet 26-7 chapters, Circulars | Same weekly check |
| Fannie | Selling Guide announcements (SEL-), Lender Letters (LL-) | singlefamily.fanniemae.com sitemap, PDFs downloaded |
| Freddie | Seller/Servicer Guide | Derrick saves the PDF monthly. Bulletins are blocked (robots.txt bars bots) |

Each week:

1. The server runs `agency.check()`. It downloads new FHA/VA files, archives old copies and updates the index.
2. After that, it hands every new file to the tracker.
   * A letter or circular is summarized whole.
   * A new handbook edition (4000.1, a 26-7 chapter, the Freddie guide) is summarized from the lines that were added and removed.
3. The tracker checks Fannie's sitemap for new SEL- and LL- releases from the last 120 days.
4. Each release goes through `llm.ask_json` (Claude, then Gemini, then local), which returns:
   * a summary
   * an effective date
   * a list of changes, each with topic, old, new, section and effective
5. Releases with nothing for origination (servicing, HECM, multifamily, admin) are left out and remembered in
   `data/tracker_state.json` under `skipped`. Failures go under `errors` and are retried on the next run.

### Files

| File | What it does |
|---|---|
| `core.py` | Where releases come from (Fannie sitemap, the agency check's files), polite fetching (generic User-Agent, 3 s between hits per site) |
| `summarize.py` | The model prompt and schema |
| `feed.py` | Reads and writes the feed and its state, keeps the schema exact, strips dashes |
| `__main__.py` | The standalone command |
| `scripts/run_tracker.ps1` | Standalone run for Task Scheduler. Logs to `%LOCALAPPDATA%\guidebot\tracker.log` |
| `tests/test_tracker.py` | Tests. Fixtures are in `tests/fixtures/tracker/` |

### Rules

* Only `agency.check()`, inside the server, writes to `agency/` or the search index. The standalone run only fetches and summarizes.
* The feed's fields stay exactly as they are: `id, agency, title, url, published, effective, summary, changes, fetched_at`.
* The model only restates the text. It never adds a rule, number or date of its own.

## SOP

**Weekly (automatic).** Nothing to do while the server is running. To check on it:
* Open the admin Updates tab.
* Or look at the newest lines of `data/agency_changes.jsonl`.

**Monthly (Derrick).** From the 10th, the Updates tab asks for a fresh Freddie guide:
1. Save it into `agency/Conventional/Freddie Mac/`.
2. The next check reads it and the tracker records what changed.

**Run it by hand** (safe while the server runs):
```powershell
.venv\Scripts\python.exe -m guidebot.tracker --days 60
```
`--days` sets how far back to look. The default is 120.

**Schedule the standalone run (optional).** The server already does this weekly. The Task Scheduler lines are in
the header of `scripts/run_tracker.ps1`. Not registered.

**A release failed.** It's listed under `errors` in `data/tracker_state.json` and retries on the next run.
* Usual cause: every model was down or out of credit.
* Run it by hand once one is back.

**Re-summarize a release.** Either:
* Delete its line from `data/agency_changes.jsonl`.
* Or delete its entry under `skipped` in `data/tracker_state.json`.

Then run it by hand.

**Before changing the code**, run the tests:
```powershell
$env:GUIDEBOT_RERANK="0"; .venv\Scripts\python.exe -m pytest tests/test_tracker.py tests/test_agency.py -q
```

## Known gaps

* No Freddie bulletins. Their changes show up only once the monthly guide PDF is saved.
* Fannie Lender Letters go to the feed only. They aren't saved to `agency/` or indexed.
* Multifamily letters: since 2026-11-17 the agency check also reads a Mortgagee Letter's "To:" line
  (`agency.addressed_elsewhere`). A letter sent only to multifamily, healthcare or HECM lenders is archived, dropped
  from the index, and listed under `not_single_family` in `agency/state.json` so it isn't downloaded again.
  ML 2026-12 is still in `agency/` and the index until the server restarts on the new code and runs a check
  (admin Updates tab, Check now).
