"""De-identification methods. Each exposes `name` and `predict(text, run) -> Prediction`.

`run` is the repetition index; only the LLM uses it (to cache each repetition separately)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from deid.spans import Span


@dataclass
class Prediction:
    spans: list[Span]
    meta: dict = field(default_factory=dict)


class Method(Protocol):
    name: str

    def predict(self, text: str, run: int = 0) -> Prediction: ...
