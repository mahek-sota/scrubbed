from deid.spans import Span, merge_overlapping, redact


def test_merge_takes_union_and_longest_category():
    text = "Springfield, IL 62704"
    spans = [Span(0, 15, "CITY"), Span(13, 21, "ZIP")]
    merged = merge_overlapping(spans, text)
    assert merged == [Span(0, 21, "CITY", text)]


def test_adjacent_spans_not_merged():
    assert len(merge_overlapping([Span(0, 3, "A"), Span(3, 5, "B")])) == 2


def test_redact():
    text = "Call John at 555-123-4567."
    spans = [Span(13, 25, "PHONE"), Span(5, 9, "NAME")]
    assert redact(text, spans) == "Call [NAME] at [PHONE]."
