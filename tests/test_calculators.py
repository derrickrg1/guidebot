"""guidebot/calculators.py against the Revo site's own calculator JS, on random inputs: node runs the JS, Python runs
the port, every output field must match. Skips when node or the site checkout isn't there."""
import json
import math
import random
import shutil
import subprocess

import pytest

from guidebot import calculators as C, config

SITE = config.ROOT.parent / "site" / "assets"
pytestmark = pytest.mark.skipif(not shutil.which("node") or not (SITE / "dscr.js").exists(),
                                reason="needs node and the site checkout")


def _node(script: str, fn: str, calls: list) -> list:
    src = (f"const m = require({json.dumps(str(SITE / script))});\n"
           f"const calls = {json.dumps(calls)};\n"
           f"console.log(JSON.stringify(calls.map(a => m.{fn}(...a)), (k, v) => v === Infinity ? 'inf' : v));")
    out = subprocess.run(["node", "-"], input=src, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _same(js, py, path=""):
    if isinstance(js, dict):
        for k, v in js.items():
            _same(v, py[k], f"{path}.{k}")
    elif isinstance(js, list):
        assert len(js) == len(py), path
        for i, (a, b) in enumerate(zip(js, py)):
            _same(a, b, f"{path}[{i}]")
    elif js == "inf":
        assert py == math.inf, path
    elif isinstance(js, (int, float)) and not isinstance(js, bool) and py is not None:
        assert py == pytest.approx(js, abs=1e-6), f"{path}: js {js}, py {py}"
    else:
        assert js == py, f"{path}: js {js}, py {py}"


def _date(rng):
    return f"{rng.randint(2023, 2026)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def test_dscr_matches_the_site():
    rng = random.Random(1)
    cases = [{"rent": rng.choice([0, rng.uniform(800, 9000)]), "loan": rng.uniform(80000, 2e6),
              "ratePct": rng.uniform(4, 11), "years": rng.choice([30, 40]), "io": rng.random() < .3,
              "taxes": rng.uniform(0, 900), "hoi": rng.uniform(0, 400), "hoa": rng.choice([0, rng.uniform(0, 600)]),
              "flood": rng.choice([0, 80])} for _ in range(300)]
    js = _node("dscr.js", "calc", [[c] for c in cases])
    for c, j in zip(cases, js):
        py = C.dscr(c["rent"], c["loan"], c["ratePct"], c["years"], c["io"], c["taxes"], c["hoi"], c["hoa"], c["flood"])
        _same(j, py)


def test_buydown_matches_the_site():
    rng = random.Random(2)
    cases = [[round(rng.uniform(60000, 1.5e6), 2), round(rng.uniform(3.5, 9.5), 3), rng.choice([15, 20, 30])]
             for _ in range(300)]
    for c, j in zip(cases, _node("buydown.js", "calc", cases)):
        py = C.buydown(*c)
        py["options"] = [{**o, "rows": o["rows"]} for o in py["options"]]
        _same(j, py)


def test_va_irrrl_matches_the_site():
    rng = random.Random(3)
    loans, ntbs, seas = [], [], []
    for _ in range(300):
        loans.append({"payoff": rng.uniform(80000, 900000), "costsFinanced": rng.uniform(0, 9000),
                      "costsCash": rng.choice([0, rng.uniform(0, 3000)]), "exempt": rng.random() < .2,
                      "oldPI": rng.uniform(500, 6000), "newRate": rng.uniform(4, 8), "newYears": rng.choice([15, 25, 30]),
                      "origYears": rng.choice([15, 30]), "taxesInsurance": rng.uniform(0, 1200),
                      "oldType": rng.choice(["fixed", "arm"]), "newType": rng.choice(["fixed", "arm"]),
                      "points": rng.choice([0, .5, 1, 1.5]), "value": rng.uniform(100000, 1e6),
                      "cashBack": rng.choice([0, 300, 600]), "texas": rng.random() < .1})
        ntbs.append([{"type": rng.choice(list(C.VA_NTB)), "oldRate": round(rng.uniform(4, 8), 3),
                      "newRate": round(rng.uniform(3.5, 8), 3)}])
        seas.append([_date(rng), _date(rng)])
    keys = ["payoff", "costsFinanced", "costsCash", "exempt", "oldPI", "newRate", "newYears", "origYears",
            "taxesInsurance", "oldType", "newType", "points", "value", "cashBack", "texas"]
    for o, j in zip(loans, _node("va-irrrl.js", "loan", [[o] for o in loans])):
        _same(j, C.va_irrrl(*[o[k] for k in keys]))
    for (o,), j in zip(ntbs, _node("va-irrrl.js", "ntb", ntbs)):
        _same(j, C.va_ntb(o["type"], o["oldRate"], o["newRate"]))
    for a, j in zip(seas, _node("va-irrrl.js", "seasoning", seas)):
        _same(j, C.va_seasoning(*a))


def test_fha_streamline_matches_the_site():
    rng = random.Random(4)
    loans, ntbs, seas = [], [], []
    for _ in range(300):
        last = _date(rng)
        loans.append({"principal": rng.uniform(80000, 700000), "originalAmount": rng.uniform(80000, 720000),
                      "perDiem": rng.choice(["daily", "monthly"]), "funding": last[:8] + f"{rng.randint(1, 28):02d}",
                      "lastPayment": last, "dailyInterest": rng.uniform(10, 120), "monthlyInterest": rng.uniform(300, 4000),
                      "proRataMip": rng.uniform(0, 300), "lateCharges": rng.choice([0, 45]),
                      "escrowShortage": rng.choice([0, rng.uniform(0, 1500)]), "unearnedUfmip": rng.uniform(0, 9000)})
        ntbs.append([{"type": rng.choice(list(C.FHA_NTB)), "priorRate": round(rng.uniform(4, 8), 3),
                      "priorMip": rng.choice([.55, .85, 1.05]), "newRate": round(rng.uniform(3.5, 8), 3),
                      "newMip": .55, "termReductionOver3": rng.random() < .2}])
        seas.append([_date(rng), _date(rng)])
    for o, j in zip(loans, _node("fha-streamline.js", "maxLoan", [[o] for o in loans])):
        _same(j, C.fha_max_loan(o["principal"], o["originalAmount"], o["perDiem"], o["funding"], o["lastPayment"],
                                o["dailyInterest"], o["monthlyInterest"], o["proRataMip"], o["lateCharges"],
                                o["escrowShortage"], o["unearnedUfmip"]))
    for (o,), j in zip(ntbs, _node("fha-streamline.js", "ntb", ntbs)):
        _same(j, C.fha_ntb(o["type"], o["priorRate"], o["priorMip"], o["newRate"], o["newMip"], o["termReductionOver3"]))
    for a, j in zip(seas, _node("fha-streamline.js", "seasoning", seas)):
        _same(j, C.fha_seasoning(*a))


# ---------- calculator questions in chat (no model: the inputs are built by hand) ----------

from guidebot import calc_chat  # noqa: E402


@pytest.mark.parametrize("q, kind", [
    ("What's the DSCR on $3,200 rent, $400k at 7.5%, taxes 350, insurance 120?", "dscr"),
    ("Cost of a 2-1 buydown on $500,000 at 6.875%?", "buydown"),
    ("IRRRL from 7.25% to 6.5% fixed to fixed, does it meet NTB?", "va_irrrl"),
    ("What is the IRRRL seasoning requirement?", None),          # a rule question stays a guideline question
    ("Are temporary buydowns allowed on a cash-out refi?", None),
    ("What is the minimum DSCR for Ignite?", None),
])
def test_calculator_questions_are_recognized(q, kind):
    assert calc_chat.kind_of(q) == kind


def test_numbers_not_in_the_question_are_dropped():
    q = "DSCR on $3,200 rent, $400k at 7.5%, taxes $4,200 per year"
    got = calc_chat._checked(calc_chat.CalcInputs(kind="dscr", monthly_rent=3200, loan_amount=400000, rate=7.5,
                                                  taxes=4200, insurance=150), q)
    assert (got.monthly_rent, got.loan_amount, got.rate) == (3200, 400000, 7.5)
    assert got.taxes == 350          # yearly taxes become monthly
    assert got.insurance is None     # never in the question: made up, dropped


def test_dscr_answer_is_python_math():
    out = calc_chat._dscr(calc_chat.CalcInputs(kind="dscr", monthly_rent=3200, loan_amount=400000, rate=7.5,
                                               taxes=350, insurance=120))
    r = C.dscr(3200, 400000, 7.5, taxes=350, hoi=120)
    assert out.startswith(f"**DSCR: {r['dscr']:.2f}**") and f"${r['total']:,.2f}" in out


def test_buydown_answer_shows_the_cost():
    out = calc_chat._buydown(calc_chat.CalcInputs(kind="buydown", loan_amount=400000, rate=7, buydown="2-1"))
    assert "**2-1: cost $9,323.16**" in out and "3-2-1" not in out


@pytest.mark.parametrize("q, want", [
    ("DSCR on $3,200 monthly rent, $400,000 loan at 7.5%, taxes $350 and insurance $120 a month?",
     {"monthly_rent": 3200, "loan_amount": 400000, "rate": 7.5, "taxes": 350, "insurance": 120}),
    ("rent is 2500, loan 350k at 8.25%, $300/mo taxes, $95 insurance, HOA $150, what DSCR?",
     {"monthly_rent": 2500, "loan_amount": 350000, "rate": 8.25, "taxes": 300, "insurance": 95, "hoa": 150}),
    ("cost of a 3-2-1 buydown on $650k at 6.875% 30 year", {"loan_amount": 650000, "rate": 6.875, "buydown": "3-2-1"}),
    ("IRRRL from 7.25% to 6.5% fixed to fixed, does it meet NTB?", {"old_rate": 7.25, "new_rate": 6.5}),
])
def test_python_fills_numbers_the_model_dropped(q, want):
    # qwen3 read rent and loan but not "at 7.5%, taxes $350" (2026-11-13); the regexes back it up
    got = calc_chat._checked(calc_chat._fill(calc_chat.CalcInputs(kind=calc_chat.kind_of(q)), q), q).model_dump()
    assert {k: got[k] for k in want} == want


def test_missing_numbers_get_asked_for_not_guessed():
    out = calc_chat._dscr(calc_chat.CalcInputs(kind="dscr", monthly_rent=3000))
    assert "loan amount" in out and "note rate" in out and "DSCR:" not in out
