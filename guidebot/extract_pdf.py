"""Turn a guideline PDF into clean pages: text outside tables, regular tables as cell rows,
and matrices rebuilt as real grids (headers combined, merged cells filled down)."""
import re

import pandas as pd
import pdfplumber

VALUE = re.compile(r"^(\*?\d+(\.\d+)?%\*?|N/?A|n/a|-)$")


def _clean(c) -> str:
    return re.sub(r"\s+", " ", str(c)).strip() if c is not None else ""


def _compact(row) -> list[str]:
    return [c for c in (_clean(x) for x in row) if c]


def _trailing_values(cells: list[str]) -> int:
    n = 0
    for c in reversed(cells):
        if not VALUE.match(c):
            break
        n += 1
    return n


def _label_period(labels: list[str]) -> int | None:
    """Return the shortest repeating period of labels, or None if they don't repeat."""
    n = len(labels)
    for p in range(1, n // 2 + 1):
        if n % p == 0 and all(labels[i] == labels[i % p] for i in range(n)):
            return p
    return None


_KNOWN_GROUPS = re.compile(
    r'(Investment\s+Property|Primary\s+Residence|Second\s+Home|Non-?Warrantable\s+Condo'
    r'|Owner[- ]Occupied|Non[- ]Owner)\*?',
    re.IGNORECASE,
)


def _split_merged_groups(text: str, n: int) -> list[str] | None:
    """Try to split a merged header cell into n group names.

    First tries matching known multi-word property-type phrases (Investment Property,
    Second Home, etc.).  Falls back to a greedy token-run split if needed.
    Returns None when it can't produce exactly n non-empty groups.
    """
    # Strip leading title / date info — use greedy .* to drop up to the LAST
    # occurrence of CLTVs / LTV / a date pattern so we get the tail of the cell.
    tail = re.sub(r'^.*(?:CLTV[sS]?|LTV[sS]?|\d{1,2}\.\d{1,2}\.\d{4})\s*', '', text, flags=re.IGNORECASE).strip()
    if not tail:
        tail = text

    # Pass 1: pull out known multi-word phrases first, then collect remaining single tokens
    found = list(_KNOWN_GROUPS.finditer(tail))
    if found:
        leftovers = _KNOWN_GROUPS.sub('\x00', tail).split('\x00')
        single_tokens = [w for part in leftovers for w in part.split() if w]
        candidates = single_tokens + [m.group(0).strip() for m in found]
        # put them back in order of their position in tail
        ordered = sorted(
            [(m.start(), m.group(0).strip()) for m in found]
            + [(tail.index(w), w) for w in single_tokens if w in tail],
            key=lambda x: x[0],
        )
        groups = [g for _, g in ordered]
        if len(groups) == n:
            return groups

    # Pass 2: generic greedy split on uppercase-word boundaries
    tokens = tail.split()
    if len(tokens) < n:
        return None
    phrases: list[list[str]] = [[tokens[0]]]
    for tok in tokens[1:]:
        if tok and tok[0].isupper() and len(phrases) < n:
            phrases.append([tok])
        else:
            phrases[-1].append(tok)
    if len(phrases) != n:
        return None
    return [" ".join(p) for p in phrases]


# Header cells that are the matrix title or effective date, never a column group.
TITLE_LIKE = re.compile(r"LTV|CLTV|Program\s+Max|Effective\s+Date|\d{1,2}\.\d{1,2}\.\d{4}", re.I)


def _cells(row) -> tuple[list[str], bool]:
    """Compact cells. A row pdfplumber squashed into one cell ('≤ $2,000,000 700 70% 65% ...')
    is split back into tokens. Returns (cells, squashed)."""
    cells = _compact(row)
    if len(cells) == 1:
        tokens = cells[0].split()
        if _trailing_values(tokens) >= 3:
            return tokens, True
    return cells, False


DATE_LIKE = re.compile(r"Effective\s+Date|\d{1,2}\.\d{1,2}\.\d{4}", re.I)


def _parse_block(compact: list[list[str]], squashed: set[int], min_rows: int = 3,
                 label: list[str] | None = None) -> tuple[pd.DataFrame | None, set[int]]:
    """One matrix from pre-compacted rows. Returns (df, indices of rows it used).
    label: the table's column-label row, for a later block that doesn't repeat it."""
    counts = [_trailing_values(c) for c in compact]
    data_idx = [i for i, n in enumerate(counts) if n >= 3]
    if len(data_idx) < min_rows:
        return None, set()

    n_values = max(set(counts[i] for i in data_idx), key=lambda n: sum(counts[i] == n for i in data_idx))
    header_idx = [i for i in range(data_idx[0]) if compact[i]]
    if not header_idx:
        return None, set()
    header_rows = [compact[i] for i in header_idx]
    if label and header_rows[-1] != label:
        header_rows.append(label)

    last = header_rows[-1]
    dims = len(last) - n_values
    if dims < 1:
        return None, set()
    dim_names = last[:dims]
    labels = last[dims:]

    groups, title, group_src = None, "", None
    for j in reversed(range(len(header_rows) - 1)):
        upper = header_rows[j]
        # A leading title cell ("Program Maximum LTV/CLTVs") sits in the same row as the groups.
        # Try dropping it first, or 3 cells over 6 columns gets read as 3 groups of 2.
        ks = (len(upper) - 1, len(upper)) if TITLE_LIKE.search(upper[0]) else (len(upper), len(upper) - 1)
        for k in ks:
            if k > 1 and n_values % k == 0:
                candidates = upper[-k:]
                if any(TITLE_LIKE.search(c) for c in candidates):
                    continue  # title/date row, not a group header
                title = upper[0] if k == len(upper) - 1 else ""
                span = n_values // k
                groups = [g for g in candidates for _ in range(span)]
                group_src = j
                break
        if groups:
            break

    # Fallback: if groups not found and labels repeat, extract groups from header text.
    if not groups and len(labels) > 1:
        period = _label_period(labels)
        if period and period < len(labels):
            n_groups = len(labels) // period
            unique_labels = labels[:period]
            found_groups = None
            for j in reversed(range(len(header_rows) - 1)):
                if len(header_rows[j]) == n_groups:
                    found_groups, group_src = header_rows[j], j
                    break
            if found_groups is None:
                # Try splitting a single merged header cell into n_groups tokens
                for j in reversed(range(len(header_rows) - 1)):
                    if len(header_rows[j]) == 1:
                        found_groups = _split_merged_groups(header_rows[j][0], n_groups)
                        if found_groups:
                            group_src = j
                            break
            if found_groups:
                groups = [g for g in found_groups for _ in range(period)]
            else:
                # Last resort: make columns unique with a counter suffix
                seen: dict[str, int] = {}
                deduped = []
                for lbl in labels:
                    seen[lbl] = seen.get(lbl, 0) + 1
                    deduped.append(f"{lbl} ({seen[lbl]})" if labels.count(lbl) > 1 else lbl)
                labels = deduped

    columns = [f"{g} / {l}" for g, l in zip(groups, labels)] if groups else labels

    # Header rows that aren't the groups or the date row name the matrix: "Full Doc with Asset Utilization",
    # "Program Max LTVs/CLTVs DSCR .75 TO .99**". That's how the AI tells sub-matrices apart.
    names = [" ".join(r) for j, r in enumerate(header_rows[:-1])
             if j != group_src and not DATE_LIKE.search(" ".join(r))]
    title = " - ".join(names + ([title] if title and not any(title in n for n in names) else []))

    records, prev, used = [], None, set(range(data_idx[0]))
    for i in data_idx:
        cells = compact[i]
        if counts[i] != n_values:
            continue
        lead, vals = cells[: len(cells) - n_values], cells[len(cells) - n_values:]
        if len(lead) > dims and i in squashed:
            # Squashed row: "≤ $2,000,000 700" -> ["≤ $2,000,000", "700"]
            cut = len(lead) - (dims - 1)
            lead = [" ".join(lead[:cut])] + lead[cut:]
        if len(lead) > dims:
            lead = lead[-dims:]
        if len(lead) < dims:
            if prev is None:
                continue
            lead = prev[: dims - len(lead)] + lead
        prev = lead
        records.append(lead + vals)
        used.add(i)

    df = pd.DataFrame(records, columns=dim_names + columns)
    df.attrs["title"] = title
    df.attrs["dims"] = dim_names
    return df, used


def parse_matrices(rows) -> tuple[list[pd.DataFrame], list]:
    """All LTV matrices in a table, plus the rows that aren't part of any matrix
    (overlay tables, requirements) so their text still reaches the bot.
    A new matrix starts wherever the column-label row repeats, or a title row
    ("Program Max LTVs/CLTVs DSCR .75 TO .99") sits between data rows."""
    parsed = [_cells(r) for r in rows]
    compact = [c for c, _ in parsed]
    squashed = {i for i, (_, s) in enumerate(parsed) if s}
    counts = [_trailing_values(c) for c in compact]
    data_idx = [i for i, n in enumerate(counts) if n >= 3]
    if len(data_idx) < 3:
        return [], rows

    # NonQM Full Doc: pdfplumber drops the "Loan Amount" label onto the first data row, and that row's
    # percentages land one row above its "≤ $1,000,000 | 740" cells. Put both back where they belong.
    i = data_idx[0]
    lead = compact[i][:len(compact[i]) - counts[i]]
    if (lead and not any(re.search(r"\d", c) for c in lead) and i + 1 < len(compact)
            and counts[i + 1] == 0 and any(re.search(r"\d", c) for c in compact[i + 1])):
        above = next((j for j in reversed(range(i)) if compact[j]), None)
        if above is not None:
            compact[above] = lead + compact[above]
            compact[i + 1] = compact[i + 1] + compact[i][len(lead):]
            compact[i] = []
            counts = [_trailing_values(c) for c in compact]
            data_idx = [k for k, n in enumerate(counts) if n >= 3]
    label = next((compact[i] for i in reversed(range(data_idx[0])) if compact[i]), None)
    if label is None:
        return [], rows

    starts = [0]
    for i in range(data_idx[0] + 1, len(rows)):
        if counts[i] < 3 and (compact[i] == label or TITLE_LIKE.search(" ".join(compact[i]))):
            before = [j for j in data_idx if starts[-1] <= j < i]
            if before:
                starts.append(before[-1] + 1)

    mats, used = [], set()
    for a, b in zip(starts, starts[1:] + [len(rows)]):
        # Later blocks follow a repeated label row, so one data row is enough to trust them.
        df, block_used = _parse_block(compact[a:b], {i - a for i in squashed if a <= i < b},
                                      min_rows=3 if a == 0 else 1, label=None if a == 0 else label)
        if df is not None:
            mats.append(df)
            used |= {i + a for i in block_used}
    if not mats:
        return [], rows
    return mats, [r for i, r in enumerate(rows) if i not in used]


def parse_matrix(rows) -> pd.DataFrame | None:
    """Return a wide DataFrame if this table is an LTV-style matrix, else None."""
    mats, _ = parse_matrices(rows)
    return mats[0] if mats else None


def matrix_to_md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.values.tolist()]
    return "\n".join(lines)


def _prose(c) -> str:
    """Cell text as one paragraph. Keeps words hyphenated across a line break ("co-\\nBorrower")."""
    return _clean(re.sub(r"-\n(?=\w)", "-", str(c))) if c is not None else ""


O_MARK = re.compile(r"(?:^|(?<=\s))o(?:\s+(?=[A-Z(])|$)")   # the "o" bullet glyph in NDM sheets


def _is_title(line: str) -> bool:
    """A sub-heading line inside a cell: no closing punctuation, mostly Capitalized Words."""
    words = [w for w in line.split() if len(w) > 3 and w[0].isalpha()]
    return (not re.search(r"[.:;,]$", line) and len(words) >= 2
            and sum(w[0].isupper() for w in words) / len(words) >= 0.9)


def _blocks(cell: str) -> list[tuple[str, str]]:
    """Split a cell's wrapped lines back into its real parts: ("h", sub-heading), ("p", paragraph),
    ("b", • bullet). A line ending in . or : closes a paragraph; anything else is word wrap."""
    lines = []
    for x in re.sub(r"-\n(?=\w)", "-", str(cell)).split("\n"):
        x = _clean(x)
        parts = O_MARK.split(x)
        if len(parts) == 1:
            lines.append(x)
        else:   # "o" sub-bullets; pdfplumber often puts the glyph after its item, so every piece is one
            lines += [f"◦{p.strip()}" for p in parts if p.strip()] or ["◦"]
    lines = [x for x in lines if x]
    for i, x in enumerate(lines):               # a lone "o" between two items marks both of them
        if x == "◦":
            if i and not lines[i - 1].startswith(("•", "◦")) and not lines[i - 1].endswith(":"):
                lines[i - 1] = "◦" + lines[i - 1]
            if i + 1 < len(lines) and not lines[i + 1].startswith(("•", "◦")):
                lines[i + 1] = "◦" + lines[i + 1]
    lines = [x for x in lines if x != "◦"]
    out: list[list[str]] = []
    closed = True
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.startswith("•"):
            out.append(["b", line.lstrip("• ").strip()])
        elif line.startswith("◦"):
            out.append(["o", line[1:]])
        elif closed and nxt[:1].isupper() and _is_title(line):
            out.append(["h", line])
            continue                            # the line after a heading starts fresh
        elif closed or (out[-1][0] == "b" and line[:1].isupper() and line.endswith(":")):
            out.append(["p", line])             # "Ineligible Borrowers:" after a bullet list
        else:
            out[-1][1] += " " + line
        closed = bool(re.search(r"[.:]$", line))
    return [(k, t) for k, t in out]


def _item(cells: list[str]) -> list[str]:
    """A topic row as a list item: sub-topic label first, then the text's paragraphs,
    sub-headings and • bullets nested under it."""
    label = _prose(cells[0]) if len(cells) > 1 else ""
    blocks = [b for c in (cells[1:] if label else cells) for b in _blocks(c)]
    base = 1 if label else 0
    lines = []
    if label:
        if blocks and blocks[0][0] == "p":
            lines.append(f"- {label}: {blocks.pop(0)[1]}")
        else:
            lines.append(f"- {label}")
    depth = base
    parent = 0 if label else -1                 # depth of the line bullets hang under
    last = parent                               # depth of the last non-"o" line
    for kind, text in blocks:
        if kind == "h":
            lines.append("  " * base + f"- {text}")
            depth, parent = base + 1, base
            last = base
        elif kind == "p":
            lines.append("  " * depth + f"- {text}")
            parent = last = depth
        elif kind == "b":
            lines.append("  " * (parent + 1) + f"- {text}")
            last = parent + 1
        else:
            lines.append("  " * (last + 1) + f"- {text}")
    return lines


def _table_rows_text(rows) -> str:
    """Guideline tables (topic | sub-topic | text) become a heading per topic with its rows as a
    list, so the model sees which rule belongs to which topic. Wide tables stay as cell rows."""
    wide = any(len(_compact(r)) > 3 for r in rows)    # a grid, not topic | sub-topic | text
    out, last = [], None
    for r in rows:
        cells = [c for c in r if _clean(c)]
        flat = [_prose(c) for c in cells]
        if not cells or flat == last:
            continue
        last = flat
        if wide:
            out.append(" | ".join(flat))
            continue
        if _clean(r[0]) and len(cells) > 1:     # first column filled: a new topic
            out.append(f"\n### {flat[0]}")
            cells = cells[1:]
        out += _item(cells)
    return "\n".join(out).strip()


def _top_level(tables):
    """Drop tables nested inside another one. pdfplumber finds the label column's cell boxes as
    their own little tables, which repeats the topic names as loose text."""
    def inside(i, j):
        a, b = tables[i].bbox, tables[j].bbox
        if a == b:
            return j < i            # exact repeat: keep the first
        return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]
    return [t for i, t in enumerate(tables) if not any(inside(i, j) for j in range(len(tables)) if j != i)]


def parse_page(page) -> dict:
    tables = _top_level(page.find_tables())
    outside = page
    for t in tables:
        try:
            outside = outside.outside_bbox(t.bbox)
        except ValueError:
            pass
    text_parts = [outside.extract_text() or ""]
    matrices = []
    for t in tables:
        mats, rest = parse_matrices(t.extract())
        matrices += mats
        text_parts.append(_table_rows_text(rest))
    return {"page": page.page_number, "text": "\n\n".join(p for p in text_parts if p.strip()), "matrices": matrices}


def extract_document(path) -> list[dict]:
    with pdfplumber.open(path) as pdf:
        return [parse_page(p) for p in pdf.pages]


def page_image(path, page_number: int, resolution: int = 110):
    with pdfplumber.open(path) as pdf:
        return pdf.pages[page_number - 1].to_image(resolution=resolution).original
