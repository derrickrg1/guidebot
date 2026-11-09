"""Split blocks into chunks. Text gets overlap, tables keep their header row on every piece."""
from guidebot import config


def _units(text: str, size: int):
    for line in text.splitlines():
        line = line.strip()
        while len(line) > size:
            yield line[:size]
            line = line[size:]
        if line:
            yield line


def split_text(text: str, size: int = config.CHUNK_CHARS, overlap: int = config.CHUNK_OVERLAP) -> list[str]:
    chunks, cur, cur_len = [], [], 0
    for unit in _units(text, size):
        if cur and cur_len + len(unit) + 1 > size:
            chunks.append("\n".join(cur))
            carry, carry_len = [], 0
            for prev in reversed(cur):
                if carry_len + len(prev) > overlap:
                    break
                carry.insert(0, prev)
                carry_len += len(prev) + 1
            cur, cur_len = carry, carry_len
        cur.append(unit)
        cur_len += len(unit) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def split_table(md: str, max_chars: int = config.TABLE_MAX_CHARS) -> list[str]:
    lines = md.splitlines()
    header, rows = lines[:2], lines[2:]
    base = len("\n".join(header))
    chunks, cur, cur_len = [], [], base
    for row in rows:
        if cur and cur_len + len(row) + 1 > max_chars:
            chunks.append("\n".join(header + cur))
            cur, cur_len = [], base
        cur.append(row)
        cur_len += len(row) + 1
    chunks.append("\n".join(header + cur))
    return chunks


def chunk_blocks(blocks) -> list[dict]:
    out = []
    for b in blocks:
        pieces = split_table(b.text) if b.kind == "table" else split_text(b.text)
        for piece in pieces:
            text = f"{b.heading}\n{piece}" if b.heading else piece
            out.append({"text": text, "loc": b.loc, "kind": b.kind})
    return out
