import pytest

from deid.categories import CATEGORIES

pytest.importorskip("presidio_analyzer")


@pytest.fixture(scope="module")
def method():
    from deid.methods.presidio import PresidioMethod

    return PresidioMethod()


def test_entity_map_targets_are_project_categories():
    from deid.methods.presidio import ENTITY_MAP

    assert set(ENTITY_MAP.values()) <= set(CATEGORIES)


def test_mrn_recognizer_and_mapping(method):
    text = "Patient John Smith, MRN: 00123456, phone 555-123-4567, email john.smith@gmail.com"
    got = {(text[s.start : s.end], s.category) for s in method.predict(text).spans}
    # Detected, though spaCy's DATE label can outscore the MRN recognizer (see NOTES.md).
    assert any(t == "00123456" for t, _ in got)
    mrn = method.engine.analyze(text=text, language="en", entities=["MRN"])
    assert [text[r.start : r.end] for r in mrn] == ["00123456"]
    assert mrn[0].score > 0.3  # context word "MRN" boosted it
    assert ("555-123-4567", "PHONE") in got
    assert ("john.smith@gmail.com", "EMAIL") in got
    assert any(c == "NAME" and "John Smith" in t for t, c in got)


def test_spans_are_merged(method):
    spans = method.predict("Seen in Springfield, IL 62704 on March 3, 2024 by Dr. Jane Doe.").spans
    for a, b in zip(spans, spans[1:], strict=False):
        assert a.end <= b.start
