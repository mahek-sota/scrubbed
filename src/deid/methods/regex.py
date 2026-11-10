"""Method 1: regular expressions.

Covers PHONE, EMAIL, SSN, MRN (labeled only), DATE and ZIP. Names, ages, addresses,
cities and hospitals are deliberately not attempted: they have no reliable surface
pattern, and the results should show that gap rather than hide it behind a name list.
"""

from __future__ import annotations

import re

from deid.methods import Prediction
from deid.spans import Span, merge_overlapping

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_MM = r"(?:0?[1-9]|1[0-2])"
_DD = r"(?:0?[1-9]|[12]\d|3[01])"

# (category, pattern). If a pattern has a group named "v", only that group is the span.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    (
        "PHONE",
        re.compile(
            r"(?<![\w-])(?:\+1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\w-])"
        ),
    ),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("SSN", re.compile(r"\bSSN\s*[:#]?\s*(?P<v>\d{3}[\s-]?\d{2}[\s-]?\d{4})\b", re.I)),
    (
        "MRN",
        re.compile(
            r"\b(?:MRN|MR\s?#|Med(?:ical)?\s+Rec(?:ord)?\.?\s*(?:No\.?|Number|#))\s*[:#]?\s*"
            r"(?P<v>\d{6,10})\b",
            re.I,
        ),
    ),
    # Dates. Numeric month/day ranges keep blood pressures like 135/92 out.
    ("DATE", re.compile(rf"\b{_MM}/{_DD}(?:/(?:\d{{4}}|\d{{2}}))?\b(?!/)")),
    ("DATE", re.compile(r"\b\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")),
    ("DATE", re.compile(rf"\b{_DD}-{_MONTH}-\d{{2,4}}\b")),
    ("DATE", re.compile(rf"\b{_MONTH}\.?\s+{_DD}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b")),
    ("DATE", re.compile(rf"\b{_MONTH}\s+\d{{4}}\b")),
    # ZIP only after a two-letter state code; a bare 5-digit rule would hit MRNs and labs.
    ("ZIP", re.compile(r"\b[A-Z]{2}\s+(?P<v>\d{5}(?:-\d{4})?)\b")),
]


class RegexMethod:
    name = "regex"

    def predict(self, text: str, run: int = 0) -> Prediction:
        spans = []
        for cat, pat in PATTERNS:
            for m in pat.finditer(text):
                start, end = m.span("v") if "v" in pat.groupindex else m.span()
                spans.append(Span(start, end, cat, text[start:end]))
        return Prediction(merge_overlapping(spans, text))
