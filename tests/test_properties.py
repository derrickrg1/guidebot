"""Property tests (Hypothesis): thousands of generated deals against every approved NonQM grid. Rules that hold
for any deal, not just the ones someone thought to write down:
- a higher FICO never gets a lower max LTV (same loan amount and column)
- a bigger loan never gets a higher max LTV (same FICO and column)
- every loan amount from the grid's lowest to its highest lands in a bracket, except a bare "<" edge
- the lookup's cell is the grid's own cell at the row and column it names
Fixed seed (derandomize), so a failure reproduces.
"""
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from guidebot import library
from guidebot.matrix_lookup import dimension_columns, header_problems, lookup, parse_range, to_number, value_columns

SETTINGS = settings(max_examples=400, derandomize=True, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])


def _grids():
    out = []
    for prog, paths in library.programs().items():
        for path in library.unique_docs(paths)[0]:
            if path.suffix.lower() != ".pdf":
                continue
            doc = library.load(path)
            if doc["status"] != "verified":
                continue
            for page in doc["pages"]:
                for m in page["matrices"]:
                    dims = dimension_columns(m)
                    if dims[:2] == ["Loan Amount", "FICO"] and value_columns(m) and not header_problems(m):
                        out.append(pytest.param(m, id=f"{path.name}-p{page['page']}-{m.attrs.get('title', '')[:30]}"))
    return out


GRIDS = _grids()
pytestmark = pytest.mark.skipif(not GRIDS, reason="no approved NonQM grids")


def _ltv(m, loan, fico, col):
    r = lookup(m, {"Loan Amount": str(loan), "FICO": str(fico)}, col)
    if not r["ok"]:
        return None
    return r["ltv"] or 0.0   # ineligible (N/A) counts as 0


def _span(m):
    los, his = [], []
    for v in dict.fromkeys(m["Loan Amount"].astype(str)):
        lo, hi, _ = parse_range(v)
        los.append(lo or 0)
        his.append(hi or 10_000_000)
    return int(min(los) or 100_000), int(max(his))


@pytest.mark.parametrize("m", GRIDS)
def test_higher_fico_never_lowers_the_ltv(m):
    lo, hi = _span(m)
    cols = value_columns(m)

    @SETTINGS
    @given(loan=st.integers(lo, hi), a=st.integers(600, 850), b=st.integers(600, 850), c=st.sampled_from(cols))
    def check(loan, a, b, c):
        a, b = sorted((a, b))
        la, lb = _ltv(m, loan, a, c), _ltv(m, loan, b, c)
        if la is not None and lb is not None:
            assert lb >= la, f"${loan:,}, {c}: FICO {a} gets {la}%, FICO {b} gets {lb}%"
    check()


@pytest.mark.parametrize("m", GRIDS)
def test_bigger_loan_never_raises_the_ltv(m):
    lo, hi = _span(m)
    cols = value_columns(m)

    @SETTINGS
    @given(x=st.integers(lo, hi), y=st.integers(lo, hi), fico=st.integers(680, 850), c=st.sampled_from(cols))
    def check(x, y, fico, c):
        x, y = sorted((x, y))
        lx, ly = _ltv(m, x, fico, c), _ltv(m, y, fico, c)
        if lx is not None and ly is not None:
            assert ly <= lx, f"FICO {fico}, {c}: ${x:,} gets {lx}%, ${y:,} gets {ly}%"
    check()


@pytest.mark.parametrize("m", GRIDS)
def test_every_loan_amount_lands_in_a_bracket(m):
    lo, hi = _span(m)
    edges = {int(parse_range(v)[1]) for v in dict.fromkeys(m["Loan Amount"].astype(str))
             if parse_range(v)[1] and parse_range(v)[2]}   # bare "<" edges: the open question for underwriting

    @SETTINGS
    @given(loan=st.integers(lo, hi))
    def check(loan):
        r = lookup(m, {"Loan Amount": str(loan), "FICO": "850"}, value_columns(m)[0])
        assert r["ok"] or "outside" not in r["error"] or loan in edges, f"${loan:,}: {r['error']}"
    check()


@pytest.mark.parametrize("m", GRIDS)
def test_lookup_cell_is_the_grids_cell(m):
    lo, hi = _span(m)

    @SETTINGS
    @given(loan=st.integers(lo, hi), fico=st.integers(600, 850), c=st.sampled_from(value_columns(m)))
    def check(loan, fico, c):
        r = lookup(m, {"Loan Amount": str(loan), "FICO": str(fico)}, c)
        if r["ok"]:
            row = m.iloc[r["i"]]
            assert str(row[c]) == r["cell"]
            assert to_number(str(row["FICO"])) <= fico   # FICO rounds down, never up
    check()
