from deid.integrity import compare, strip_wrapper, tokens

TEXT = "Pt John Smith on metformin 500 mg twice daily. BP 135/92. Seen 3/4/24."
NOTE = {
    "id": "n",
    "text": TEXT,
    "spans": [
        {"start": 3, "end": 13, "text": "John Smith", "category": "NAME"},
        {"start": 63, "end": 69, "text": "3/4/24", "category": "DATE"},
    ],
    "traps": [],
}


def test_fixture_offsets():
    assert all(TEXT[s["start"] : s["end"]] == s["text"] for s in NOTE["spans"])


def test_tokens_drop_tags_and_keep_numbers():
    assert tokens("[NAME] on 25/100 mg, BP 135/92, T 38.1") == [
        "on",
        "25/100",
        "mg",
        "BP",
        "135/92",
        "T",
        "38.1",
    ]


def test_perfect_redaction_has_no_change():
    out = "Pt [NAME] on metformin 500 mg twice daily. BP 135/92. Seen [DATE]."
    r = compare(NOTE, out)
    assert not r.changed and r.leaked == []


def test_reformatting_is_not_a_change():
    out = "Pt [NAME]\non metformin 500 mg twice daily;  BP 135/92 — Seen [DATE]"
    assert not compare(NOTE, out).changed


def test_leak_is_not_counted_as_content_change():
    out = "Pt [NAME] on metformin 500 mg twice daily. BP 135/92. Seen 3/4/24."
    r = compare(NOTE, out)
    assert not r.changed and r.leaked == ["3/4/24"]


def test_changed_number_and_medication():
    out = "Pt [NAME] on metformin 1000 mg daily. BP [DATE]. Seen [DATE]."
    r = compare(NOTE, out)
    assert r.changed and r.numbers_changed
    assert "500" in r.dropped and "1000" in r.inserted and "135/92" in r.dropped
    assert not r.meds_changed  # medication name intact
    r2 = compare(NOTE, "Pt [NAME] on [MEDICATION] twice daily. BP 135/92. Seen [DATE].")
    assert r2.meds_changed and r2.dropped == ["metformin", "500", "mg"]


def test_over_redaction_by_span_method_is_a_change():
    # A false-positive span that swallows "BP 135/92" damages content too.
    r = compare(NOTE, "Pt [NAME] on metformin 500 mg twice daily. [DATE]. Seen [DATE].")
    assert r.dropped == ["BP", "135/92"]


def test_strip_wrapper():
    assert strip_wrapper("```\nhello\n```") == "hello"
    assert strip_wrapper("plain") == "plain"


def test_hyphenated_age_leak_is_a_leak_not_a_change():
    text = "A 92-year-old man."
    note = {
        "id": "a",
        "text": text,
        "traps": [],
        "spans": [{"start": 2, "end": 4, "text": "92", "category": "AGE_OVER_89"}],
    }
    r = compare(note, "A 92-year-old man.")
    assert not r.changed and r.leaked == ["92"]


def test_titles_are_trivial_changes():
    r = compare(NOTE, "[NAME] on metformin 500 mg twice daily. BP 135/92. Seen [DATE].")
    assert r.changed and r.dropped == ["Pt"] and r.substantive_changed
    note = {
        "id": "b",
        "text": "Dr. Jo Bell",
        "traps": [],
        "spans": [{"start": 4, "end": 11, "text": "Jo Bell", "category": "NAME"}],
    }
    r2 = compare(note, "[NAME]")
    assert r2.changed and not r2.substantive_changed
