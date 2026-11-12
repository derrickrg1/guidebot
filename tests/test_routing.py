import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guidebot import answer
from guidebot.section_reader import _section_of_page, cited_pages, expand_terms

GUIDE = "NonQM-Underwriting-Guidelines.pdf"


def test_shorthand_is_spelled_out():
    q = expand_terms("do we allow an NOD from HOA dues on a DSCR lon?")
    assert q.startswith("do we allow an NOD")
    assert "NOD = Notice of Default, a pre-foreclosure credit event" in q and "HOA = homeowners association" in q
    assert expand_terms("what about the NODE count") == "what about the NODE count"   # whole words only
    assert expand_terms("SS on file") == "SS on file"                                 # ambiguous, left alone


def test_cited_pages_only_for_that_file():
    history = [
        {"role": "user", "content": f"({GUIDE}, p. 99)"},   # user text doesn't count
        {"role": "assistant", "content": f"20% rule ({GUIDE}, section 9.4, p. 64; NonQM Express DSCR.pdf, p. 2). "
                                         f"Seasoning ({GUIDE}, pp. 54)."},
    ]
    assert cited_pages(history, GUIDE) == [64, 54]


def test_page_maps_to_its_mid_level_section():
    toc = [{"num": "7.0", "title": "Credit", "page": 50}, {"num": "7.5", "title": "Derog", "page": 54},
           {"num": "7.5.1", "title": "Seasoning", "page": 55}, {"num": "7.7", "title": "Liens", "page": 56},
           {"num": "9.4", "title": "Condos", "page": 64}]
    assert _section_of_page(55, toc, 80, 0) == "7.5"
    assert _section_of_page(64, toc, 80, 0) == "9.4"
    assert _section_of_page(10, toc, 80, 0) is None


def test_answer_rules_keep_earlier_citations_and_match_aliases():
    assert "Don't call it wrong" in answer.RULES
    assert '"Pre-foreclosure" row covers an NOD' in answer.RULES
