"""Character spans, overlap handling, and redaction."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, order=True)
class Span:
    start: int
    end: int
    category: str
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Span:
        return cls(d["start"], d["end"], d["category"], d.get("text", ""))


def overlaps(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def merge_overlapping(spans: list[Span], text: str | None = None) -> list[Span]:
    """Merge overlapping spans into their union, so redaction never leaves a partial identifier.

    The merged span takes the category of its longest contributor. Touching (adjacent)
    spans are kept separate.
    """
    out: list[Span] = []
    longest: list[int] = []
    for s in sorted(spans, key=lambda s: (s.start, -s.end)):
        if out and s.start < out[-1].end:
            prev = out[-1]
            cat = s.category if (s.end - s.start) > longest[-1] else prev.category
            longest[-1] = max(longest[-1], s.end - s.start)
            end = max(prev.end, s.end)
            out[-1] = Span(prev.start, end, cat, text[prev.start : end] if text else "")
        else:
            out.append(Span(s.start, s.end, s.category, text[s.start : s.end] if text else s.text))
            longest.append(s.end - s.start)
    return out


def redact(text: str, spans: list[Span]) -> str:
    """Replace each span with [CATEGORY]. Overlapping spans are merged first."""
    parts: list[str] = []
    pos = 0
    for s in merge_overlapping(spans):
        parts.append(text[pos : s.start])
        parts.append(f"[{s.category}]")
        pos = s.end
    parts.append(text[pos:])
    return "".join(parts)
