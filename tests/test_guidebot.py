import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from guidebot.ingest.chunker import split_table, split_text
from guidebot.ingest.loaders import load_file, table_to_md
from guidebot.retrieval import Retriever, rrf, tokenize


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * 64
        for tok in tokenize(t):
            v[hash(tok) % 64] += 1
        out.append(v if any(v) else [1.0] + [0.0] * 63)
    return out


def test_tokenize_keeps_numbers():
    assert tokenize("Max LTV 80% at DSCR 1.25") == ["max", "ltv", "80%", "at", "dscr", "1.25"]


def test_split_text_respects_size():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    chunks = split_text(text, size=500, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 600 for c in chunks)


def test_split_table_repeats_header():
    md = table_to_md([["FICO", "LTV"]] + [[str(700 + i), "80%"] for i in range(200)])
    pieces = split_table(md, max_chars=300)
    assert len(pieces) > 1
    assert all(p.startswith("| FICO | LTV |") for p in pieces)


def test_excel_loader(tmp_path):
    path = tmp_path / "matrix.xlsx"
    pd.DataFrame({"FICO": [700, 680], "Max LTV": ["80%", "75%"]}).to_excel(path, sheet_name="DSCR", index=False)
    blocks = load_file(path)
    assert blocks[0].loc == "sheet DSCR"
    assert "| 700 | 80% |" in blocks[0].text


def test_docx_loader(tmp_path):
    import docx

    d = docx.Document()
    d.add_heading("Reserves", level=1)
    d.add_paragraph("6 months PITIA required.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Loan Amount", "Reserves"
    t.cell(1, 0).text, t.cell(1, 1).text = "<= 1,000,000", "6 months"
    path = tmp_path / "g.docx"
    d.save(path)
    blocks = load_file(path)
    assert blocks[0].heading == "Reserves"
    assert "| <= 1,000,000 | 6 months |" in blocks[-1].text


def test_rrf_rewards_agreement():
    assert rrf([["x", "b"], ["b", "y"]])[0] == "b"


def test_index_search_and_sync(tmp_path):
    from guidebot.ingest.indexer import get_collection, sync_index

    docs, db = tmp_path / "docs", tmp_path / "db"
    (docs / "DSCR").mkdir(parents=True)
    (docs / "Agency").mkdir()
    (docs / "DSCR" / "guide.md").write_text("DSCR program. Minimum FICO 660. Minimum DSCR ratio 1.00. Reserves 6 months.")
    (docs / "Agency" / "guide.md").write_text("Agency program. Gift funds allowed from family. Max DTI 50 percent.")

    total = sync_index(docs, db, embed=fake_embed, log=lambda *_: None)
    assert total == 2

    r = Retriever(get_collection(db), embed=lambda q: fake_embed([q])[0])
    assert r.programs() == ["Agency", "DSCR"]
    assert r.search("minimum FICO DSCR", k=1)[0]["program"] == "DSCR"
    assert all(h["program"] == "Agency" for h in r.search("FICO", programs=["Agency"]))

    logs = []
    sync_index(docs, db, embed=fake_embed, log=logs.append)
    assert logs == []  # nothing changed, nothing re-indexed

    (docs / "Agency" / "guide.md").unlink()
    sync_index(docs, db, embed=fake_embed, log=logs.append)
    assert get_collection(db).count() == 1
