"""Answer keys for the stress bank's guideline questions, for racing local backup models without Claude.

Each key is the deciding fact from Sonnet's answers in the 2026-11-12 full stress run (data/stress/old.jsonl, every
citation opened and every number/quote found on its cited page by stress_check). "lead" is how the answer must open
(yes/no); "has" is regexes that must all be in the answer (lowercased, markdown stripped). Questions whose answer is
a judgment call or a Python LTV table have no key.
"""
import re

KEYS = {
    # NonQM
    "nq-guide-00": {"has": [r"48 months", r"36 months", r"24 months"]},
    "nq-guide-01": {"has": [r"48 months", r"36 months"]},
    "nq-guide-02": {"has": [r"48 months", r"36 months"]},
    "nq-guide-06": {"lead": "yes"},
    "nq-guide-08": {"has": [r"\b20 acres", r"\b2 acres", r"\b5 acres"]},
    "nq-guide-09": {"lead": "no"},
    "nq-guide-10": {"has": [r"5-8|5 to 8|units\+"]},
    "nq-guide-11": {"has": [r"10[- ]?y(ea)?rs?"]},
    "nq-guide-12": {"lead": "yes", "has": [r"state"]},
    "nq-guide-13": {"has": [r"50%", r"55%"]},
    "nq-guide-14": {"has": [r"primary", r"dscr"]},
    "nq-guide-15": {"has": [r"\b680\b", r"\b660\b"]},
    "nq-guide-16": {"has": [r"2,000,000|\$2m\b|\$2 million", r"two (full )?appraisals|2 (full )?appraisals"]},
    "nq-guide-17": {"has": [r"12[- ]months?"]},
    "nq-guide-18": {"has": [r"50%", r"10%"]},
    "nq-guide-19": {"lead": "yes", "has": [r"1 or 2 years|one or two years"]},
    "nq-guide-20": {"has": [r"\bcpa\b", r"enrolled agent", r"12[- ]month"]},
    "nq-guide-23": {"has": [r"0?\.75"]},
    "nq-guide-25": {"lead": "yes", "has": [r"0x30"]},
    "nq-guide-26": {"has": [r"750,000|\$750k", r"500,000|\$500k"]},
    "nq-guide-27": {"lead": "yes", "has": [r"\bllc\b"]},
    "nq-guide-28": {"has": [r"\b20\b"]},
    "nq-guide-29": {"has": [r"(3|three) trade ?lines?", r"(2|two) trade ?lines?", r"24 months"]},
    "nq-guide-31": {"has": [r"\b9 months", r"\b12 months"]},
    "nq-guide-32": {"lead": "yes", "has": [r"24 months", r"5%"]},
    "nq-guide-33": {"has": [r"expense factor"]},
    "nq-guide-34": {"has": [r"24 months", r"ignite"]},
    "nq-guide-35": {"has": [r"5%"]},
    "nq-guide-36": {"lead": "yes"},
    "nq-guide-38": {"has": [r"ineligible|not (allowed|eligible|permitted)"]},
    "nq-guide-39": {"has": [r"75,000|\$75k"]},
    # Conventional
    "conv-guide-00": {"has": [r"fixed", r"1[- ]unit|one[- ]unit", r"primary|principal"]},
    "conv-guide-01": {"lead": "no"},
    "conv-guide-03": {"lead": "yes", "has": [r"existing loan"]},
    "conv-guide-04": {"has": [r"\blpa\b|loan product advisor"]},
    "conv-guide-05": {"has": [r"4 years|48 months|four years", r"2 years|24 months|two years", r"extenuating"]},
    "conv-guide-06": {"has": [r"80% of (the )?(area median|ami)|80% ami", r"50%"]},
    "conv-guide-08": {"has": [r"six months|6 months"]},
    "conv-guide-09": {"has": [r"5/6", r"7/6", r"10/6", r"2\.75"]},
    "conv-guide-10": {"lead": "yes"},
    "conv-guide-11": {"has": [r"first[- ]time"]},
    "conv-guide-12": {"lead": "yes"},
    "conv-guide-13": {"has": [r"0?\.5%|one-half of (one )?percent"]},
    "conv-guide-14": {"has": [r"\b620\b"]},
    "conv-guide-15": {"has": [r"50%", r"65%"]},
    "conv-guide-16": {"has": [r"75%"]},
    "conv-guide-17": {"lead": "no"},
    "conv-guide-18": {"has": [r"75,000|\$75k"]},
    "conv-guide-19": {"lead": "no"},
    "conv-guide-20": {"has": [r"1[- ]unit|one[- ]unit", r"condo"]},
    "conv-guide-21": {"has": [r"\$250\b"]},
    "conv-guide-22": {"has": [r"multi-?wide"]},
    "conv-guide-23": {"has": [r"\b3%"]},
    "conv-guide-24": {"has": [r"\b12%", r"\b6%"]},
    "conv-guide-25": {"has": [r"4[- ]years?|four years|48 months", r"2[- ]years?|two years|24 months"]},
    "conv-guide-26": {"lead": "yes", "has": [r"homeready"]},
    "conv-guide-27": {"has": [r"refinow"]},
    "conv-guide-29": {"lead": "no"},
    # FHA
    "fha-guide-00": {"has": [r"\b580\b"]},
    "fha-guide-01": {"has": [r"\b55 ?(bps|basis)|0?\.55 ?%"]},
    "fha-guide-02": {"has": [r"0?\.5%|one-half of (one )?percent"]},
    "fha-guide-03": {"lead": "yes", "has": [r"streamline"]},
    "fha-guide-05": {"has": [r"12 months|1 year|one year|12-month", r"court"]},
    "fha-guide-07": {"lead": "no"},
    "fha-guide-10": {"has": [r"2,000", r"\b5%"]},
    "fha-guide-11": {"has": [r"75,000|\$75k"]},
    # VA
    "va-guide-01": {"has": [r"disabilit"]},
    "va-guide-02": {"has": [r"discount points"]},
    "va-guide-06": {"has": [r"county (loan )?limit"]},
    "va-guide-07": {"lead": "yes"},
    "va-guide-08": {"has": [r"210 days", r"six(th)?|6th"]},
    "va-guide-09": {"has": [r"multi-?wide|double-?wide"]},
    "va-guide-10": {"has": [r"41%", r"50%"]},
    "va-guide-11": {"has": [r"2\.15%"]},
}


def plain(answer: str) -> str:
    a = answer.replace("‑", "-").replace("‐", "-").replace("–", "-")
    a = re.sub(r"[*_`]", "", a)
    a = re.sub(r"[\s   ]+", " ", a).lower().strip()
    return re.sub(r"(\d) %", r"\1%", a)   # gpt-oss writes "50 %"


def misses(case_id: str, answer: str) -> list[str]:
    """What the answer got wrong against its key; [] when it passes or has no key."""
    key = KEYS.get(case_id)
    if not key:
        return []
    a = plain(answer)
    out = [f"missing /{rx}/" for rx in key.get("has", []) if not re.search(rx, a)]
    if key.get("lead"):
        start = re.sub(r"^[^a-z]+", "", a)
        if not re.match(rf"{key['lead']}\b", start):
            out.append(f"should open with {key['lead']!r}: {a[:70]!r}")
    return out
