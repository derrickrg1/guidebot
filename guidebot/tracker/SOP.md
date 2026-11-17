# Change tracker: SOP

The steps for running the tracker. How it works is in [DESIGN.md](DESIGN.md).

## Weekly: automatic

Nothing to do while the server is running. To see what came in:
* Open AskNick's What's new banner. Each release has "What changed" under it.
* Or open the admin Updates tab (`/admin`, Updates).

## Monthly: Freddie guide (Derrick)

From the 10th, the Updates tab asks for a fresh Freddie Seller/Servicer Guide.
1. Download it from the link on the notice.
2. Save it into `agency/Conventional/Freddie Mac/`.
3. The next check reads it, indexes it and records what changed.

## After a code change: restart and check

1. Kill the `python -m guidebot.server` process. The loop brings it back in 5 to 30 seconds.
2. On the admin Updates tab, click **Check now**.

## Run the tracker by hand

This is safe while the server runs: it only fetches and summarizes.
```powershell
.venv\Scripts\python.exe -m guidebot.tracker --days 60
```
`--days` sets how far back to look. The default is 120.

## Schedule the standalone run (optional)

The server already runs the tracker weekly. To run it apart from the server too, use the Task Scheduler lines in
the header of `scripts/run_tracker.ps1`. They aren't registered. The log goes to
`%LOCALAPPDATA%\guidebot\tracker.log`.

## A release failed

It's listed under `errors` in `data/tracker_state.json` and retries on the next run.
* Usual cause: every model was down or out of credit.
* Once one is back, run the tracker by hand.

## Re-summarize a release

1. Either delete its line from `data/agency_changes.jsonl`, or delete its entry under `skipped` in
   `data/tracker_state.json`.
2. Run the tracker by hand.

## A letter that shouldn't be in AskNick

* **Multifamily or HECM Mortgagee Letter:** the next check archives it on its own.
* **Lender Letter for servicers only:** add a word from its title to `LL_SERVICING` in `guidebot/agency.py`, then
  restart and check.
* **Let a left-out Mortgagee Letter back in:** remove its entry under `not_single_family` in `agency/state.json`,
  then run a check.

## Before changing the code

Run the tests:
```powershell
$env:GUIDEBOT_RERANK="0"; .venv\Scripts\python.exe -m pytest tests/test_tracker.py tests/test_agency.py -q
```
