from types import SimpleNamespace

from guidebot import answer, config, lessons, llm, usage


def _tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(lessons, "FILE", tmp_path / "lessons.json")


def test_save_scope_and_block(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    assert lessons.save({"scope": "Express", "text": "Check the loan amount tier before the FICO row."}, "a@b")["ok"]
    assert lessons.save({"scope": "*", "text": "A sheet's footnote applies to every row it marks."}, "a@b")["ok"]
    express = lessons.block("Express 1099")
    assert "loan amount tier" in express and "footnote" in express
    fha = lessons.block("FHA Standard", "FHA")
    assert "loan amount tier" not in fha and "footnote" in fha
    assert "(applies to: Express)" in lessons.block(every=True)


def test_pii_refused(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    for bad in ("Call the borrower at 714-555-1212 first", "Email jane@acme.com about reserves",
                "The property at 123 Main St is a condo", "SSN 123-45-6789 had a freeze"):
        assert "error" in lessons.save({"scope": "*", "text": bad}, "a@b"), bad
    assert lessons.load() == []
    assert lessons.pii("Minimum FICO 660 for loans over $1.5M") == ""


def test_edit_and_delete(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    lid = lessons.save({"scope": "*", "text": "first version of the lesson"}, "a@b")["lesson"]["id"]
    lessons.save({"id": lid, "scope": "VA", "text": "second version of the lesson"}, "a@b")
    assert [x["text"] for x in lessons.load()] == ["second version of the lesson"]
    assert lessons.delete(lid)["ok"] and lessons.load() == []


def test_full_doc_fixed_block_is_cached_and_question_free(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    lessons.save({"scope": "*", "text": "Always read the overlay page."}, "a@b")
    msgs = answer.full_doc_messages("my borrower question", "DOCS", [], program="FHA Standard",
                                    extra="AGENCY PAGES")
    assert msgs[0]["cache"] and "Always read the overlay page." in msgs[0]["content"]
    assert "my borrower question" not in msgs[0]["content"] and "AGENCY PAGES" not in msgs[0]["content"]
    assert msgs[1] == {"role": "system", "content": "AGENCY PAGES"}
    system, _ = llm._split(msgs)
    assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": config.CACHE_TTL}
    assert "cache_control" not in system[1]


def test_one_hour_write_priced_double():
    u = SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                        cache_creation_input_tokens=1_000_000,
                        cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=1_000_000, ephemeral_5m_input_tokens=0))
    assert usage.cost(usage.claude_tokens(u), "claude-sonnet-5-5")["usd"] == 4.0
