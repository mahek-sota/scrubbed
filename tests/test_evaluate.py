import math

import pytest

from deid.evaluate import score, trap_hits
from deid.spans import Span

TEXT = "Pt John Smith, MRN 1234567, seen 3/4/24 for Bell's palsy."
#       0123456789012345678901234567890123456789012345678901234567
NOTE = {
    "id": "n1",
    "text": TEXT,
    "spans": [
        {"start": 3, "end": 13, "text": "John Smith", "category": "NAME"},
        {"start": 19, "end": 26, "text": "1234567", "category": "MRN"},
        {"start": 33, "end": 39, "text": "3/4/24", "category": "DATE"},
    ],
    "traps": [{"start": 44, "end": 56, "text": "Bell's palsy"}],
}


def test_offsets_in_fixture():
    for s in NOTE["spans"] + NOTE["traps"]:
        assert TEXT[s["start"] : s["end"]] == s["text"]


def test_perfect_predictions():
    preds = {"n1": [Span.from_dict(s) for s in NOTE["spans"]]}
    for mode in ("exact", "overlap"):
        sc = score([NOTE], preds, mode)
        p, r, f = sc.overall.prf()
        assert (p, r, f) == (1.0, 1.0, 1.0)
        assert sc.missed == [] and sc.false_positives == []
        assert sc.label_accuracy == 1.0


def test_partial_name_exact_vs_overlap():
    # Only "John" found; MRN found with wrong label; date missed; one false positive on the trap.
    preds = {
        "n1": [
            Span(3, 7, "NAME"),
            Span(19, 26, "PHONE"),
            Span(44, 48, "NAME"),
        ]
    }
    ex = score([NOTE], preds, "exact")
    assert ex.overall.tp_gold == 1  # only the MRN offsets match exactly
    assert ex.overall.tp_pred == 1
    assert {m["text"] for m in ex.missed} == {"John Smith", "3/4/24"}

    ov = score([NOTE], preds, "overlap")
    p, r, f = ov.overall.prf()
    assert ov.overall.tp_gold == 2
    assert r == pytest.approx(2 / 3)
    assert p == pytest.approx(2 / 3)
    assert f == pytest.approx(2 / 3)
    assert [m["text"] for m in ov.missed] == ["3/4/24"]
    assert [m["text"] for m in ov.partial] == ["John Smith"]  # "Smith" leaks
    assert ov.label_accuracy == 0.5  # NAME right, MRN labelled PHONE
    # Per-category: recall is by gold label, precision by predicted label.
    assert ov.by_category["MRN"].tp_gold == 1 and ov.by_category["MRN"].n_pred == 0
    assert ov.by_category["PHONE"].n_pred == 1 and ov.by_category["PHONE"].tp_pred == 1
    assert ov.by_category["NAME"].n_pred == 2 and ov.by_category["NAME"].tp_pred == 1
    assert [fp["text"] for fp in ov.false_positives] == ["Bell"]

    n, hits = trap_hits([NOTE], preds)
    assert n == 1 and hits[0]["term"] == "Bell's palsy" and hits[0]["predicted"] == ["Bell"]


def test_one_prediction_covering_two_gold_spans():
    text = "Springfield, IL 62704"
    note = {
        "id": "a",
        "text": text,
        "spans": [
            {"start": 0, "end": 11, "text": "Springfield", "category": "CITY"},
            {"start": 16, "end": 21, "text": "62704", "category": "ZIP"},
        ],
        "traps": [],
    }
    sc = score([note], {"a": [Span(0, 21, "CITY")]}, "overlap")
    assert sc.overall.tp_gold == 2 and sc.overall.n_pred == 1 and sc.overall.tp_pred == 1


def test_no_predictions_and_missing_note():
    sc = score([NOTE], {}, "overlap")
    p, r, f = sc.overall.prf()
    assert math.isnan(p) and r == 0.0 and f == 0.0
    assert len(sc.missed) == 3
    names = next(row for row in sc.rows() if row["category"] == "NAME")
    assert names["recall"] == 0.0 and names["f1"] == 0.0
