"""Turn a guideline PDF into clean pages: text outside tables, regular tables as cell rows,
and matrices rebuilt as real grids (headers combined, merged cells filled down)."""
import re

import pandas as pd
import pdfplumber

VALUE = re.compile(r"^(\d+(\.\d+)?%\*?|N/?A|n/a|-)$")


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


def parse_matrix(rows) -> pd.DataFrame | None:
    """Return a wide DataFrame if this table is an LTV-style matrix, else None."""
    compact = [_compact(r) for r in rows]
    counts = [_trailing_values(c) for c in compact]
    data_idx = [i for i, n in enumerate(counts) if n >= 3]
    if len(data_idx) < 3:
        return None

    n_values = max(set(counts[i] for i in data_idx), key=lambda n: sum(counts[i] == n for i in data_idx))
    header_rows = [compact[i] for i in range(data_idx[0]) if compact[i]]
    if not header_rows:
        return None

    last = header_rows[-1]
    dims = len(last) - n_values
    if dims < 1:
        return None
    dim_names = last[:dims]
    labels = last[dims:]

    groups, title = None, ""
    for upper in reversed(header_rows[:-1]):
        for k in (len(upper), len(upper) - 1):
            # Skip if k==2 and any element looks like a date or program name rather than a
            # property-type group — those rows are title/date rows, not group headers.
            if k > 1 and n_values % k == 0:
                candidates = upper[-k:]
                if k == 2 and any(re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', c) for c in candidates):
                    continue  # date row, not a group header
                title = upper[0] if k == len(upper) - 1 else ""
                span = n_values // k
                groups = [g for g in candidates for _ in range(span)]
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
            for upper in reversed(header_rows[:-1]):
                if len(upper) == n_groups:
                    found_groups = upper
                    break
            if found_groups is None:
                # Try splitting a single merged header cell into n_groups tokens
                for upper in reversed(header_rows[:-1]):
                    if len(upper) == 1:
                        found_groups = _split_merged_groups(upper[0], n_groups)
                        if found_groups:
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

    records, prev = [], None
    for i in data_idx:
        cells = compact[i]
        if counts[i] != n_values:
            continue
        lead, vals = cells[: len(cells) - n_values], cells[len(cells) - n_values:]
        if len(lead) > dims:
            lead = lead[-dims:]
        if len(lead) < dims:
            if prev is None:
                continue
            lead = prev[: dims - len(lead)] + lead
        prev = lead
        records.append(lead + vals)

    df = pd.DataFrame(records, columns=dim_names + columns)
    df.attrs["title"] = title
    df.attrs["dims"] = dim_names
    return df


def matrix_to_md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.values.tolist()]
    return "\n".join(lines)


def _table_rows_text(rows) -> str:
    out = []
    for r in rows:
        cells = _compact(r)
        if cells and (not out or out[-1] != " | ".join(cells)):
            out.append(" | ".join(cells))
    return "\n".join(out)


def parse_page(page) -> dict:
    tables = page.find_tables()
    outside = page
    for t in tables:
        try:
            outside = outside.outside_bbox(t.bbox)
        except ValueError:
            pass
    text_parts = [outside.extract_text() or ""]
    matrices = []
    for t in tables:
        rows = t.extract()
        df = parse_matrix(rows)
        if df is not None:
            matrices.append(df)
        else:
            text_parts.append(_table_rows_text(rows))
    return {"page": page.page_number, "text": "\n\n".join(p for p in text_parts if p.strip()), "matrices": matrices}


def extract_document(path) -> list[dict]:
    with pdfplumber.open(path) as pdf:
        return [parse_page(p) for p in pdf.pages]


def page_image(path, page_number: int, resolution: int = 110):
    with pdfplumber.open(path) as pdf:
        return pdf.pages[page_number - 1].to_image(resolution=resolution).original
