"""Index the docs folder from the command line: python scripts/ingest.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guidebot.ingest.indexer import sync_index

if __name__ == "__main__":
    total = sync_index()
    print(f"Done. {total} chunks in the index.")
