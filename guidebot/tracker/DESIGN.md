# Change tracker: design

How the tracker works and why. For the day-to-day steps, see [SOP.md](SOP.md).

## What it does

It watches FHA, VA, Fannie Mae and Freddie Mac for guideline changes.

Each release it finds becomes plain rule changes in `data/agency_changes.jsonl`. AskNick's What's new banner reads
that file.

It also keeps AskNick's agency guides current. FHA and VA files, and Fannie Lender Letters, are saved to `agency/`
and indexed, so answers can cite them.

## Sources

| Agency | What | How it gets it | Saved and indexed? | In the feed? |
|---|---|---|---|---|
| FHA | HUD 4000.1 | Weekly agency check downloads it | Yes | Yes, what each new edition added and removed |
| FHA | Single family Mortgagee Letters, this year and last | Weekly agency check | Yes | Yes, the whole letter |
| VA | Pamphlet 26-7 chapters | Weekly agency check (KnowVA) | Yes | Yes, what each new edition added and removed |
| VA | Circulars in force | Weekly agency check | Yes | Yes, the whole circular |
| Fannie | Selling Guide | Derrick saves it (Fannie blocks downloads of it) | Yes | No, the announcements cover it |
| Fannie | Selling Guide announcements (SEL-) | singlefamily.fanniemae.com sitemap | No | Yes |
| Fannie | Lender Letters (LL-), this year and last | Same sitemap | Yes, under `agency/Conventional/Fannie Mae/Lender Letters/` | Yes |
| Freddie | Seller/Servicer Guide | Derrick saves it monthly | Yes | Yes, what each new edition added and removed |
| Freddie | Bulletins | Not fetched: guide.freddiemac.com's robots.txt bars bots | No | No |

## The weekly flow

The server starts `agency.check()` once a week. Derrick can also start it from the admin Updates tab with
**Check now**.

1. **Download.** Each source lists what it wants on disk. New or changed files are downloaded. The old copy goes
   to `archive/agency/<date>/`. A file the agency stops listing is archived too.
2. **Read and write up.** Each new file's text is read, and the check notes which lines were added and removed.
   The change shows on the admin Updates tab.
3. **Index.** The search index is updated, inside the server.
4. **Feed.** The tracker gets every file the check just read, then reads Fannie's sitemap for releases from the
   last 120 days. Each release goes to the model through `llm.ask_json` (Claude, then Gemini, then local). The
   model returns:
   * a summary
   * an effective date
   * a list of changes, each with topic, old, new, section and effective
5. **Filter.** A release with nothing for origination (servicing, HECM, multifamily, admin) is left out and
   remembered in `data/tracker_state.json` under `skipped`. A failure goes under `errors` and is retried next
   time.

## Filters

* **Mortgagee Letters.** A letter is left out when either of these says it isn't single family:
  * its title (`NOT_SINGLE_FAMILY`)
  * its "To:" line, when it goes only to multifamily, healthcare or HECM lenders (`addressed_elsewhere`)

  A letter left out because of its "To:" line is archived. It's listed under `not_single_family` in
  `agency/state.json`, so it isn't downloaded again.
* **Lender Letters.** Left out when either of these is true:
  * the title is servicing only (`LL_SERVICING`: servicing, foreclosure, workouts, MI termination and so on)
  * the tracker already skipped the letter as having nothing for originators

  A letter that's already saved and later gets skipped is archived on the next check.
* **Handbooks.** Pages outside forward origination rank last in search: HUD Title I and HECM, and Freddie's
  servicing chapters.

## How answers use them

* A Mortgagee Letter or VA Circular newer than its handbook wins over it.
* A Fannie Lender Letter dated after the Selling Guide wins over it. This rule is in `answer.py`, and each Lender
  Letter's page header repeats it.
* Search saves 3 spots for letters (Mortgagee Letters, Circulars, Lender Letters), so guide pages don't crowd them
  out (`chat._agency_hits`).
* NDM's own sheet wins over all of them.
* Lender Letters sit inside the Fannie folder, so only Fannie programs get them (Fannie Mae, HomeReady, RefiNow).
  `agency.source_of` picks the deepest folder, so a Lender Letter is never mistaken for the Selling Guide.

## Rules

* Only `agency.check()`, inside the server, writes to `agency/` or the search index. The standalone run
  (`python -m guidebot.tracker`) only fetches and summarizes.
* The feed's fields stay exactly as they are: `id, agency, title, url, published, effective, summary, changes,
  fetched_at`. The banner depends on them.
* The model only restates the text. It never adds a rule, number or date of its own. Dates are checked to look like
  dates.
* Fetching is polite: a generic User-Agent, 3 seconds between hits to a site, and robots.txt is respected.
* No em or en dashes in anything people read.

## Files

| File | What it does |
|---|---|
| `guidebot/agency.py` | Sources, downloads, archive, write-ups, index updates, filters, the weekly check |
| `guidebot/tracker/core.py` | Where releases come from (Fannie sitemap, the check's files) and polite fetching |
| `guidebot/tracker/summarize.py` | The model prompt and schema |
| `guidebot/tracker/feed.py` | Reads and writes the feed and its state, keeps the schema exact |
| `guidebot/tracker/__main__.py` | The standalone command |
| `scripts/run_tracker.ps1` | Standalone run for Task Scheduler |
| `tests/test_tracker.py`, `tests/test_agency.py` | Tests. Fixtures are in `tests/fixtures/tracker/` |
| `data/agency_changes.jsonl` | The feed |
| `data/tracker_state.json` | Releases skipped or failed, and when Fannie was last checked |
| `agency/state.json` | The agency files, updates, notices and the letters left out |

## Known gaps

* No Freddie bulletins. Their changes show up only once the monthly guide PDF is saved.
* Lender Letters older than last year aren't kept, even if Fannie still lists them.
