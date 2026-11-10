"""Span-level evaluation: precision, recall, F1 per category and overall.

Matching ignores the predicted label: for de-identification what matters is whether the
identifier was removed, not what it was called. Label agreement is reported separately.

Two matching modes:
- exact:   predicted span has the same start and end as the gold span.
- overlap: predicted span shares at least one character with the gold span.

Precision is over predictions (a prediction is correct if it matches any gold span);
recall is over gold spans (a gold span is found if any prediction matches it). So one
prediction covering "Springfield, IL 62704" can find both the CITY and the ZIP gold spans.

In overlap mode a gold span can be found but not fully covered ("partial"): part of the
identifier still leaks. Those are counted and listed.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from deid.categories import CATEGORIES, OTHER
from deid.spans import Span, overlaps

MODES = ("exact", "overlap")
CONTEXT_CHARS = 30


def _match(g: Span, p: Span, mode: str) -> bool:
    if mode == "exact":
        return g.start == p.start and g.end == p.end
    if mode == "overlap":
        return overlaps(g, p)
    raise ValueError(mode)


def _covered(g: Span, preds: list[Span]) -> bool:
    covered = set()
    for p in preds:
        if overlaps(g, p):
            covered.update(range(max(g.start, p.start), min(g.end, p.end)))
    return len(covered) == g.end - g.start


def _prf(tp_pred: int, n_pred: int, tp_gold: int, n_gold: int) -> tuple[float, float, float]:
    p = tp_pred / n_pred if n_pred else math.nan
    r = tp_gold / n_gold if n_gold else math.nan
    if math.isnan(r):
        f = math.nan
    elif math.isnan(p) or p + r == 0:
        f = 0.0
    else:
        f = 2 * p * r / (p + r)
    return p, r, f


@dataclass
class Counts:
    n_gold: int = 0
    tp_gold: int = 0
    n_pred: int = 0
    tp_pred: int = 0
    partial: int = 0

    def prf(self) -> tuple[float, float, float]:
        return _prf(self.tp_pred, self.n_pred, self.tp_gold, self.n_gold)


@dataclass
class Scores:
    mode: str
    overall: Counts = field(default_factory=Counts)
    by_category: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    label_agree: int = 0
    label_total: int = 0
    missed: list[dict] = field(default_factory=list)
    partial: list[dict] = field(default_factory=list)
    false_positives: list[dict] = field(default_factory=list)

    @property
    def label_accuracy(self) -> float:
        return self.label_agree / self.label_total if self.label_total else math.nan

    def rows(self) -> list[dict]:
        """One row per category plus an ALL row, ready for a DataFrame."""
        out = []
        cats = [*CATEGORIES, *sorted(c for c in self.by_category if c not in CATEGORIES)]
        for cat in cats:
            c = self.by_category.get(cat)
            if c is None:
                continue
            p, r, f = c.prf()
            out.append(_row(cat, c, p, r, f, self.mode))
        p, r, f = self.overall.prf()
        out.append(_row("ALL", self.overall, p, r, f, self.mode))
        return out


def _row(cat: str, c: Counts, p: float, r: float, f: float, mode: str) -> dict:
    return {
        "mode": mode,
        "category": cat,
        "n_gold": c.n_gold,
        "found": c.tp_gold,
        "n_pred": c.n_pred,
        "correct_pred": c.tp_pred,
        "partial": c.partial,
        "precision": p,
        "recall": r,
        "f1": f,
    }


def _context(text: str, s: Span) -> str:
    a, b = max(0, s.start - CONTEXT_CHARS), min(len(text), s.end + CONTEXT_CHARS)
    snippet = text[a : s.start] + "<<" + text[s.start : s.end] + ">>" + text[s.end : b]
    return snippet.replace("\n", " ")


def score(
    notes: list[dict],
    predictions: dict[str, list[Span]],
    mode: str = "overlap",
) -> Scores:
    """Score predictions against gold. Notes with no entry in `predictions` count as
    "predicted nothing" (so failed LLM calls show up as misses, not as skipped notes)."""
    sc = Scores(mode=mode)
    for note in notes:
        text = note["text"]
        gold = [Span.from_dict(s) for s in note["spans"]]
        preds = predictions.get(note["id"], [])
        pred_hit = [False] * len(preds)
        for gi, g in enumerate(gold):
            matched = [i for i, p in enumerate(preds) if _match(g, p, mode)]
            for i in matched:
                pred_hit[i] = True
            cg = sc.by_category[g.category]
            cg.n_gold += 1
            sc.overall.n_gold += 1
            info = {
                "note_id": note["id"],
                "category": g.category,
                "text": g.text,
                "subtype": note["spans"][gi].get("subtype", ""),
                "tags": note["spans"][gi].get("tags", []),
                "context": _context(text, g),
            }
            if not matched:
                sc.missed.append(info)
                continue
            cg.tp_gold += 1
            sc.overall.tp_gold += 1
            # Label agreement: does the best (largest-overlap) match carry the gold label?
            best = max(
                matched, key=lambda i: min(g.end, preds[i].end) - max(g.start, preds[i].start)
            )
            sc.label_total += 1
            sc.label_agree += preds[best].category == g.category
            if mode == "overlap" and not _covered(g, [preds[i] for i in matched]):
                cg.partial += 1
                sc.overall.partial += 1
                sc.partial.append(info)
        for p, hit in zip(preds, pred_hit, strict=True):
            cat = p.category if p.category in CATEGORIES else OTHER
            cp = sc.by_category[cat]
            cp.n_pred += 1
            sc.overall.n_pred += 1
            if hit:
                cp.tp_pred += 1
                sc.overall.tp_pred += 1
            else:
                sc.false_positives.append(
                    {
                        "note_id": note["id"],
                        "category": p.category,
                        "text": text[p.start : p.end],
                        "context": _context(text, p),
                    }
                )
    return sc


def trap_hits(notes: list[dict], predictions: dict[str, list[Span]]) -> tuple[int, list[dict]]:
    """Count eponym/clinical trap terms that any prediction overlaps.

    Returns (number of trap terms, list of hits)."""
    n, hits = 0, []
    for note in notes:
        preds = predictions.get(note["id"], [])
        for t in note["traps"]:
            n += 1
            ts = Span(t["start"], t["end"], "TRAP", t["text"])
            over = [p for p in preds if overlaps(ts, p)]
            if over:
                hits.append(
                    {
                        "note_id": note["id"],
                        "term": t["text"],
                        "predicted": [note["text"][p.start : p.end] for p in over],
                        "categories": [p.category for p in over],
                    }
                )
    return n, hits
