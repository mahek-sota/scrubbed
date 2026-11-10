"""Method 2: Microsoft Presidio with its default recognizers plus a custom MRN recognizer.

Presidio entity types are mapped to this project's categories in ENTITY_MAP (the single
documented table). Presidio types with no counterpart (e.g. US_DRIVER_LICENSE, NRP) are
kept as OTHER: an out-of-the-box redaction pipeline would remove them too, so they
count against precision when they are wrong.
"""

from __future__ import annotations

import logging
import os

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer

from deid.categories import OTHER
from deid.methods import Prediction
from deid.spans import Span, merge_overlapping

# Presidio entity -> project category. Everything else maps to OTHER.
ENTITY_MAP: dict[str, str] = {
    "PERSON": "NAME",
    "DATE_TIME": "DATE",
    "PHONE_NUMBER": "PHONE",
    "EMAIL_ADDRESS": "EMAIL",
    "US_SSN": "SSN",
    "MRN": "MRN",  # custom recognizer below
    # spaCy GPE/LOC/FAC all become LOCATION; most hits in these notes are cities.
    "LOCATION": "CITY",
}

# Presidio's default is 0 (return everything). Kept at the default: no tuning on the test
# notes. A threshold sweep (0 / 0.2 / 0.35 / 0.5 / 0.7) only lowered overlap F1.
SCORE_THRESHOLD = float(os.environ.get("DEID_PRESIDIO_THRESHOLD", "0"))


def mrn_recognizer() -> PatternRecognizer:
    """7-8 digit numbers. Weak on their own (score 0.3); a nearby context word such as
    "MRN" lets Presidio's context enhancer raise the score."""
    return PatternRecognizer(
        supported_entity="MRN",
        patterns=[Pattern("mrn_7_8_digits", r"\b\d{7,8}\b", 0.3)],
        context=["mrn", "mr", "medical", "record", "rec", "chart"],
    )


# Presidio logs a warning for every non-English recognizer it skips.
logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)


class PresidioMethod:
    name = "presidio"

    def __init__(self, score_threshold: float = SCORE_THRESHOLD):
        self.engine = AnalyzerEngine()  # default NLP engine: spaCy en_core_web_lg
        self.engine.registry.add_recognizer(mrn_recognizer())
        self.score_threshold = score_threshold

    def predict(self, text: str, run: int = 0) -> Prediction:
        results = self.engine.analyze(
            text=text, language="en", score_threshold=self.score_threshold
        )
        spans = [
            Span(r.start, r.end, ENTITY_MAP.get(r.entity_type, OTHER), text[r.start : r.end])
            for r in results
        ]
        raw = sorted({r.entity_type for r in results})
        return Prediction(merge_overlapping(spans, text), {"presidio_entities": raw})
