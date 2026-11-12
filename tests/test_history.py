import pytest

from guidebot import history


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB", tmp_path / "chats.db")


def test_turns_are_saved_in_order():
    assert history.save_turn("c1", "a@x.com", "Max DTI?", "50%.", "Ignite 1099", "guidelines")
    assert history.save_turn("c1", "a@x.com", "And reserves?", "6 months.", "Ignite 1099", "guidelines",
                             [{"file": "x.pdf"}])
    c = history.get_chat("c1", "a@x.com")
    assert c["title"] == "Max DTI?"
    assert [m["content"] for m in c["messages"]] == ["Max DTI?", "50%.", "And reserves?", "6 months."]
    assert c["messages"][3]["sources"] == [{"file": "x.pdf"}]


def test_people_only_see_their_own_chats():
    history.save_turn("c1", "a@x.com", "q", "a")
    history.save_turn("c2", "b@x.com", "q", "a")
    assert [c["id"] for c in history.list_chats("a@x.com")] == ["c1"]
    assert history.get_chat("c2", "a@x.com") is None
    assert not history.delete_chat("c2", "a@x.com")
    assert {c["id"] for c in history.list_chats(None)} == {"c1", "c2"}   # admin sees everyone's


def test_someone_elses_chat_id_cant_be_written_to():
    history.save_turn("c1", "a@x.com", "q", "a")
    assert not history.save_turn("c1", "b@x.com", "sneaky", "a")
    assert len(history.get_chat("c1")["messages"]) == 2


def test_empty_answer_is_not_saved():
    assert not history.save_turn("c1", "a@x.com", "q", "")
    assert history.list_chats(None) == []


def test_delete():
    history.save_turn("c1", "a@x.com", "q", "a")
    assert history.delete_chat("c1", "a@x.com")
    assert history.get_chat("c1") is None


def test_user_from_cloudflare_header():
    assert history.user({"Cf-Access-Authenticated-User-Email": " NWilson@RevoFunding.com "}) == "nwilson@revofunding.com"
    assert history.user({}) == "local"
