"""Method 3: a local LLM via Ollama.

The model returns the exact text and category of each identifier (not character offsets,
which LLMs get wrong). Code then finds every occurrence of that text in the note.

Failures are counted, never hidden:
- timeout / http_error / too_long: the call failed; the note gets no predictions.
- invalid_json: the reply did not parse or did not match the expected structure.
- output_truncated: generation hit num_predict (reply is usually invalid JSON too).
- not_in_note: an item whose text cannot be found in the note (hallucinated or altered).
- bad_category: an item whose category is not one of ours (span kept as OTHER).
"""

from __future__ import annotations

import json
import os
import re

from deid.categories import CATEGORIES, OTHER
from deid.methods import Prediction
from deid.ollama_client import OllamaClient, OllamaError
from deid.spans import Span, merge_overlapping

PROMPT_VERSION = "extract-v1"

CATEGORY_GUIDE = """Categories:
- NAME: any person's name (patient, relative, clinician, staff): first, last, or full name.
- DATE: any date or part of a date more specific than a year (e.g. 03/14/2024, 3/14, March 14, March 2024). A year on its own is not an identifier.
- AGE_OVER_89: an age of 90 or older (only the number). Ages 89 and under are not identifiers.
- PHONE: telephone numbers.
- EMAIL: email addresses.
- MRN: medical record numbers, with or without a label.
- SSN: social security numbers.
- ADDRESS: street address (house number, street, apartment), without the city, state or ZIP.
- CITY: city names.
- ZIP: ZIP codes.
- HOSPITAL: names of hospitals, medical centers and clinics.

Not identifiers:
- Disease, syndrome, sign, score, test and device names, even when they contain a person's name.
- Medications, lab values, vital signs and US states."""

SYSTEM_PROMPT = f"""You are a de-identification assistant. Find every patient identifier in the clinical note the user gives you.

{CATEGORY_GUIDE}

Rules:
- Copy each identifier's text exactly as it appears in the note, character for character.
- If the same text appears several times, list it once.
- Do not include titles or labels such as "Dr.", "Mr.", "MRN:" in the text.

Reply with JSON only: {{"identifiers": [{{"text": "...", "category": "..."}}]}}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "identifiers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                },
                "required": ["text", "category"],
            },
        }
    },
    "required": ["identifiers"],
}

FAILURE_KINDS = (
    "timeout",
    "http_error",
    "too_long",
    "invalid_json",
    "output_truncated",
    "not_in_note",
    "bad_category",
)


def user_prompt(text: str) -> str:
    return f"Clinical note:\n<<<\n{text}\n>>>"


def _bounded(text: str, start: int, end: int) -> bool:
    """True if [start, end) is not glued to a neighbouring letter/digit (so "Rose" does
    not match inside "Rosemary")."""
    before = start == 0 or not text[start - 1].isalnum() or not text[start].isalnum()
    after = end == len(text) or not text[end].isalnum() or not text[end - 1].isalnum()
    return before and after


def find_all(note: str, needle: str) -> list[tuple[int, int]]:
    """Every occurrence of `needle` in `note`: case-sensitive first, then case-insensitive,
    then with flexible whitespace. Returns [] if the text is not in the note."""
    if not needle:
        return []
    for flags in (0, re.IGNORECASE):
        hits = [
            (m.start(), m.end())
            for m in re.finditer(re.escape(needle), note, flags)
            if _bounded(note, m.start(), m.end())
        ]
        if hits:
            return hits
    parts = needle.split()
    if len(parts) > 1:
        pat = r"\s+".join(re.escape(p) for p in parts)
        return [(m.start(), m.end()) for m in re.finditer(pat, note, re.IGNORECASE)]
    return []


def parse_items(content: str) -> list[dict] | None:
    """Return the list of {"text", "category"} items, or None if the reply is unusable."""
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    items = data.get("identifiers") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    return [
        it
        for it in items
        if isinstance(it, dict)
        and isinstance(it.get("text"), str)
        and isinstance(it.get("category"), str)
    ]


def locate(note: str, items: list[dict]) -> tuple[list[Span], dict[str, int], list[str]]:
    spans: list[Span] = []
    failures = {"not_in_note": 0, "bad_category": 0}
    not_found: list[str] = []
    for it in items:
        text, cat = it["text"].strip(), it["category"].strip().upper()
        if cat not in CATEGORIES:
            failures["bad_category"] += 1
            cat = OTHER
        hits = find_all(note, text)
        if not hits:
            failures["not_in_note"] += 1
            not_found.append(text)
            continue
        spans.extend(Span(s, e, cat, note[s:e]) for s, e in hits)
    return merge_overlapping(spans, note), failures, not_found


class LLMMethod:
    def __init__(
        self,
        client: OllamaClient | None = None,
        temperature: float | None = None,
        name: str = "llm",
    ):
        self.name = name
        self.client = client or OllamaClient()
        env_t = os.environ.get("DEID_LLM_TEMPERATURE")
        self.temperature = (
            temperature if temperature is not None else (float(env_t) if env_t else None)
        )

    def predict(self, text: str, run: int = 0) -> Prediction:
        failures = dict.fromkeys(FAILURE_KINDS, 0)
        meta: dict = {"failures": failures, "model": self.client.cfg.model}
        try:
            res = self.client.chat(
                SYSTEM_PROMPT,
                user_prompt(text),
                format=SCHEMA,
                num_predict=1024,
                temperature=self.temperature,
                cache_tag=f"{PROMPT_VERSION}|run={run}|t={self.temperature}",
            )
        except OllamaError as e:
            failures[e.kind] += 1
            return Prediction([], meta)
        meta.update(
            runtime_s=res.runtime_s, prompt_tokens=res.prompt_tokens, eval_tokens=res.eval_tokens
        )
        if res.done_reason == "length":
            failures["output_truncated"] += 1
        items = parse_items(res.content)
        if items is None:
            failures["invalid_json"] += 1
            meta["raw"] = res.content[:500]
            return Prediction([], meta)
        spans, loc_failures, not_found = locate(text, items)
        failures.update(loc_failures)
        meta.update(n_items=len(items), not_found=not_found)
        return Prediction(spans, meta)


_NAME_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_NOT_NAME_WORDS = {"dr", "md", "rn", "mr", "ms", "mrs"}


def expand_names(note: str, spans: list[Span]) -> list[Span]:
    """Post-hoc variant: also redact each word of every predicted NAME wherever it appears.

    The model lists "Alexander, Tracy" once; this also catches a later "Ms. Alexander".
    Designed after looking at the results, so it is reported separately, not as the
    headline LLM method."""
    out = list(spans)
    for s in spans:
        if s.category != "NAME":
            continue
        for word in _NAME_WORD.findall(note[s.start : s.end]):
            if word.lower() not in _NOT_NAME_WORDS:
                out.extend(Span(a, b, "NAME", note[a:b]) for a, b in find_all(note, word))
    return merge_overlapping(out, note)
