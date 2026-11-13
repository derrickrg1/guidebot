import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DB_DIR = ROOT / "db"
AGENCY_DIR = ROOT / "agency"   # HUD, VA and Fannie guides, kept current by agency.py (not NDM docs, not approved)
SITE_MATRICES = ROOT.parent / "site" / "tools" / "matrices"   # Revo site repo: hand-checked LTV grids
# The current Revo site checkout: its built matrices (assets/matrices) are real HTML tables. GUIDEBOT_SITE_REPO overrides.
SITE_REPO = Path(os.environ.get("GUIDEBOT_SITE_REPO")
                 or Path.home() / "OneDrive" / "Documents" / "GitHub" / "revo-funding-site")


def _load_env(path: Path):
    """KEY=value lines from .env (git-ignored) into the environment. Real env vars win."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

# Chat runs on Claude when an API key is set, else on the local model. GUIDEBOT_MODEL in .env overrides both.
# Either way Python does every lookup and all the math; the model only reads questions and guidelines.
LOCAL_MODEL = "qwen3:14b"
CLAUDE_MODEL = "claude-sonnet-5-5"   # 22/22 in the race, same as Opus, faster and cheaper
CLAUDE_EFFORT = os.environ.get("GUIDEBOT_EFFORT", "medium")
CHAT_MODEL = os.environ.get("GUIDEBOT_MODEL") or (CLAUDE_MODEL if os.environ.get("ANTHROPIC_API_KEY") else LOCAL_MODEL)
EMBED_MODEL = "nomic-embed-text"
EMBED_DOC_PREFIX = "search_document: "    # nomic needs these, set both to "" for other embed models
EMBED_QUERY_PREFIX = "search_query: "
CHAT_OPTIONS = {"temperature": 0.1, "num_ctx": 16384}

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
TABLE_MAX_CHARS = 3000
TOP_K = 10                # sections pulled in All-programs search mode
FULL_DOC_MAX_CHARS = 45000  # above this, a program falls back to search instead of reading everything

SUPPORTED = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"}
