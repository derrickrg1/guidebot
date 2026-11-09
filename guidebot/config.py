from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DB_DIR = ROOT / "db"

CHAT_MODEL = "qwen3:14b"
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
