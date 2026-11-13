"""A guideline question that names a program answers from that program, even with none picked."""
from guidebot import chat


def _run(monkeypatch, question):
    progs = {"Ignite Bank Statements": ["a"], "Express Bank Statements": ["b"], "Ignite 1099": ["c"], chat.SHARED: []}
    monkeypatch.setattr(chat.library, "programs", lambda: progs)
    monkeypatch.setattr(chat, "program_docs", lambda p, allp: (allp[p], []))
    asked = []

    def program_answer(q, history, prog, docs, model):
        asked.append(prog)
        yield {"text": prog}
        return prog, []

    def broad(*a, **k):
        asked.append("ALL")
        yield {"text": "all"}
        return "all", []
    monkeypatch.setattr(chat, "_program_answer", program_answer)
    monkeypatch.setattr(chat, "_broad_answer", broad)
    monkeypatch.setattr(chat.ltv_overview, "asks_ltv", lambda q: False)
    monkeypatch.setattr(chat.calc_chat, "answer", lambda q, m: "")
    monkeypatch.setattr(chat.loan_limits, "answer", lambda q: "")
    list(chat.respond(question, [], "", section="NonQM", model="test"))
    return asked


def test_one_named(monkeypatch):
    assert _run(monkeypatch, "Are non-warrantable condos allowed on Ignite Bank Statements?") == ["Ignite Bank Statements"]


def test_two_named(monkeypatch):
    assert _run(monkeypatch, "Gift funds on Ignite 1099 vs Ignite Bank Statements?") == ["Ignite Bank Statements", "Ignite 1099"]


def test_none_named_reads_all(monkeypatch):
    assert _run(monkeypatch, "Are non-warrantable condos allowed?") == ["ALL"]
