from collections import Counter

import pytest

from deid.categories import CATEGORIES
from deid.generate import Ident, Trap, generate, render


@pytest.fixture(scope="module")
def notes():
    return generate(n=200, seed=1234)


def test_render_offsets():
    text, spans, traps = render(
        "Pt {name} has {dx}.",
        {"name": ["Mr. ", Ident("Bell", "NAME")], "dx": Trap("Bell's palsy")},
    )
    assert text == "Pt Mr. Bell has Bell's palsy."
    assert text[spans[0]["start"] : spans[0]["end"]] == "Bell"
    assert text[traps[0]["start"] : traps[0]["end"]] == "Bell's palsy"


def test_every_span_matches_text(notes):
    for n in notes:
        for s in n["spans"] + n["traps"]:
            assert n["text"][s["start"] : s["end"]] == s["text"], (n["id"], s)


def test_spans_valid_and_disjoint(notes):
    for n in notes:
        spans = sorted(n["spans"], key=lambda s: s["start"])
        for s in spans:
            assert s["category"] in CATEGORIES
            assert s["text"] == s["text"].strip() and s["text"]
        for a, b in zip(spans, spans[1:], strict=False):
            assert a["end"] <= b["start"], (n["id"], a, b)
        for t in n["traps"]:
            assert all(t["end"] <= s["start"] or s["end"] <= t["start"] for s in spans)


def test_deterministic():
    assert generate(n=12, seed=7) == generate(n=12, seed=7)
    assert generate(n=12, seed=7) != generate(n=12, seed=8)


def test_corpus_coverage(notes):
    cats = Counter(s["category"] for n in notes for s in n["spans"])
    assert set(cats) == set(CATEGORIES)
    assert min(cats.values()) >= 20
    assert Counter(n["note_type"] for n in notes).most_common()[-1][1] >= 33
    tags = Counter(t for n in notes for s in n["spans"] for t in s["tags"])
    assert tags["lowercase"] > 0 and tags["common_word"] > 0
    assert sum(len(n["traps"]) for n in notes) >= 100
    mrn_kinds = Counter(s["subtype"] for n in notes for s in n["spans"] if s["category"] == "MRN")
    assert mrn_kinds["labeled"] > 0 and mrn_kinds["unlabeled"] > 0


def test_ages_89_and_under_are_not_labeled(notes):
    for n in notes:
        for s in n["spans"]:
            if s["category"] == "AGE_OVER_89":
                assert int(s["text"]) >= 90
