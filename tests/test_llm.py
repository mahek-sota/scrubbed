import json

import pytest

from deid.config import OllamaConfig
from deid.methods.llm import LLMMethod, find_all, locate, parse_items
from deid.ollama_client import ChatResult, OllamaClient, OllamaError

NOTE = "Pt Rose Bell seen 3/4/24 for Bell's palsy. Rose's daughter Hope called. Will recheck."


class FakeClient:
    """Stands in for OllamaClient: returns a canned reply or raises."""

    def __init__(self, reply=None, error=None, done_reason="stop"):
        self.cfg = OllamaConfig()
        self.reply, self.error, self.done_reason = reply, error, done_reason
        self.calls = []

    def chat(self, system, user, **kw):
        self.calls.append(kw)
        if self.error:
            raise OllamaError(self.error)
        content = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return ChatResult(content, 100, 50, self.done_reason, 1.5)


def test_find_all_word_boundaries_and_case():
    assert find_all("Rosemary and Rose", "Rose") == [(13, 17)]
    assert find_all("JOHN smith", "John Smith") == [(0, 10)]  # case-insensitive fallback
    assert find_all("John\n  Smith", "John Smith") == [(0, 12)]  # flexible whitespace
    assert find_all("abc", "xyz") == []
    # Case-sensitive match wins, so the verb "will" is not hit for the name "Will".
    assert find_all("Will said he will", "Will") == [(0, 4)]


def test_parse_items():
    assert parse_items('{"identifiers": [{"text": "a", "category": "NAME"}]}') == [
        {"text": "a", "category": "NAME"}
    ]
    assert parse_items('```json\n{"identifiers": []}\n```') == []
    assert parse_items("not json") is None
    assert parse_items('{"identifiers": "x"}') is None
    assert parse_items('{"identifiers": [{"text": 3}]}') == []


def test_locate_all_occurrences_and_failures():
    items = [
        {"text": "Rose", "category": "NAME"},
        {"text": "3/4/24", "category": "DATE"},
        {"text": "Jane Doe", "category": "NAME"},  # not in note
        {"text": "Hope", "category": "PERSON"},  # not our category
    ]
    spans, failures, not_found = locate(NOTE, items)
    got = [(NOTE[s.start : s.end], s.category) for s in spans]
    assert got == [("Rose", "NAME"), ("3/4/24", "DATE"), ("Rose", "NAME"), ("Hope", "OTHER")]
    assert failures == {"not_in_note": 1, "bad_category": 1}
    assert not_found == ["Jane Doe"]


def test_predict_success():
    client = FakeClient({"identifiers": [{"text": "Rose Bell", "category": "NAME"}]})
    pred = LLMMethod(client=client).predict(NOTE, run=2)
    assert [(s.start, s.end, s.category) for s in pred.spans] == [(3, 12, "NAME")]
    assert pred.meta["runtime_s"] == 1.5
    assert sum(pred.meta["failures"].values()) == 0
    assert "run=2" in client.calls[0]["cache_tag"]


@pytest.mark.parametrize("error", ["timeout", "http_error", "too_long"])
def test_predict_call_failures_counted(error):
    pred = LLMMethod(client=FakeClient(error=error)).predict(NOTE)
    assert pred.spans == []
    assert pred.meta["failures"][error] == 1


def test_predict_invalid_json_counted():
    pred = LLMMethod(client=FakeClient("{'identifiers': oops")).predict(NOTE)
    assert pred.spans == [] and pred.meta["failures"]["invalid_json"] == 1


def test_predict_truncated_output_counted():
    pred = LLMMethod(client=FakeClient('{"identifiers": [{"te', done_reason="length")).predict(NOTE)
    f = pred.meta["failures"]
    assert f["output_truncated"] == 1 and f["invalid_json"] == 1


def test_client_refuses_prompt_longer_than_context(tmp_path):
    client = OllamaClient(OllamaConfig(num_ctx=512), cache_dir=tmp_path)
    with pytest.raises(OllamaError) as e:
        client.chat("system", "x" * 5000)
    assert e.value.kind == "too_long"


def test_client_cache_roundtrip(tmp_path, monkeypatch):
    client = OllamaClient(OllamaConfig(), cache_dir=tmp_path)
    calls = []

    def fake_call(payload):
        calls.append(payload)
        return ChatResult("hi", 1, 1, "stop", 0.5)

    monkeypatch.setattr(client, "_call", fake_call)
    a = client.chat("s", "u", cache_tag="run=0")
    b = client.chat("s", "u", cache_tag="run=0")
    c = client.chat("s", "u", cache_tag="run=1")
    assert a == b == c and len(calls) == 2  # run=1 is a separate sample
    assert calls[0]["options"]["num_ctx"] == OllamaConfig().num_ctx


def test_expand_names_catches_surname_only_mentions():
    from deid.methods.llm import expand_names
    from deid.spans import Span

    note = "Patient: Alexander, Tracy. Dr. Bell saw Ms. Alexander. Will recheck."
    spans = [Span(9, 25, "NAME"), Span(31, 35, "NAME")]
    got = [note[s.start : s.end] for s in expand_names(note, spans)]
    assert got == ["Alexander, Tracy", "Bell", "Alexander"]  # "Dr" itself is not expanded
