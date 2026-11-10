"""JSONL helpers and loaders for notes and predictions."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from deid.config import DATA_DIR
from deid.spans import Span

NOTES_PATH = DATA_DIR / "notes.jsonl"
PRED_DIR = DATA_DIR / "predictions"


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_notes(path: Path = NOTES_PATH) -> list[dict]:
    return read_jsonl(path)


def gold_spans(note: dict) -> list[Span]:
    return [Span.from_dict(s) for s in note["spans"]]


def trap_spans(note: dict) -> list[Span]:
    return [Span(t["start"], t["end"], "TRAP", t["text"]) for t in note["traps"]]


def pred_path(method: str) -> Path:
    return PRED_DIR / f"{method}.jsonl"
