"""python -m guidebot.tracker [--days N]

Fetches and summarizes agency releases into data/agency_changes.jsonl. Safe while the server runs: it reads
agency/ and never writes it or the search index (the server's weekly agency check does that)."""
import argparse
import sys

from guidebot.tracker import core, feed


def main():
    ap = argparse.ArgumentParser(prog="python -m guidebot.tracker")
    ap.add_argument("--days", type=int, default=120, help="how far back to look for releases (default 120)")
    args = ap.parse_args()
    added = core.run(args.days, log=lambda m: print(m, flush=True))
    print(f"Done. {len(added)} new release(s) in {feed.FEED}.")
    errs = feed.load_state()["errors"]
    if errs:
        print(f"{len(errs)} release(s) failed and will be retried next run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
