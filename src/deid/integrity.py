"""Content integrity: does redaction change anything except the identifiers?

Two ways to use an LLM:
- "LLM finds spans, code redacts" (method `llm`): code replaces spans with [CATEGORY], so
  the only possible content damage is over-redaction (false-positive spans).
- "LLM rewrites the note" (`llm_rewrite`): the model returns the whole note with tags in
  place; it can also drop, add or alter words anywhere.

The same comparison is applied to every method's redacted output:
  reference = the original note with the GOLD identifiers removed
  output    = the method's redacted note
Both are tokenized into words/numbers with [TAG] placeholders dropped, then diffed.
- A token in the reference but missing from the output = clinical content dropped/changed.
- A token added in the output that is part of a gold identifier = a leak (a missed
  identifier; scored by the span metrics, not here).
- Any other added token = inserted content.

Run: python -m deid.integrity   (asks the LLM to rewrite every note; cached)
"""

from __future__ import annotations

import argparse
import difflib
import re
from dataclasses import dataclass, field

from deid.generate import MEDICATION_NAMES
from deid.io import load_notes, pred_path, write_jsonl
from deid.methods.llm import CATEGORY_GUIDE
from deid.ollama_client import OllamaClient, OllamaError
from deid.spans import Span, redact

REWRITE_VERSION = "rewrite-v1"

REWRITE_SYSTEM = f"""You redact clinical notes. Return the note the user gives you exactly as written, except replace each patient identifier with its category tag in square brackets, for example [NAME], [DATE] or [MRN].

{CATEGORY_GUIDE}

Change nothing else: keep every other word, number, medication, punctuation mark and line break exactly as it is. Do not summarize, correct or add anything. Output only the redacted note."""

# Hyphens split tokens, so a leaked "92-year-old" shows up as the leaked identifier "92"
# rather than as a changed word.
_TOKEN = re.compile(r"\[[A-Z_0-9]+\]|[A-Za-z0-9]+(?:[.'/+][A-Za-z0-9]+)*")
# Honorifics and credentials that often get redacted along with a name ("Dr. [NAME]" vs
# "[NAME]"). Losing them is a change, but a trivial one; reported separately.
TITLES = {"dr", "mr", "ms", "mrs", "md", "rn"}
_TAG = re.compile(r"^\[[A-Z_0-9]+\]$")
_MED_TOKENS = {t for name in MEDICATION_NAMES for t in _TOKEN.findall(name.lower())}


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text) if not _TAG.match(t)]


@dataclass
class IntegrityResult:
    dropped: list[str] = field(default_factory=list)
    inserted: list[str] = field(default_factory=list)
    leaked: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    @property
    def substantive_changed(self) -> bool:
        return any(t.lower() not in TITLES for t in self.dropped + self.inserted)

    @property
    def changed(self) -> bool:
        return bool(self.dropped or self.inserted)

    @property
    def numbers_changed(self) -> bool:
        return any(any(c.isdigit() for c in t) for t in self.dropped + self.inserted)

    @property
    def meds_changed(self) -> bool:
        return any(t.lower() in _MED_TOKENS for t in self.dropped + self.inserted)

    def to_dict(self) -> dict:
        return {
            "dropped": self.dropped,
            "inserted": self.inserted,
            "leaked": self.leaked,
            "changed": self.changed,
            "substantive_changed": self.substantive_changed,
            "numbers_changed": self.numbers_changed,
            "meds_changed": self.meds_changed,
            "examples": self.examples,
        }


def compare(note: dict, output: str, max_examples: int = 3) -> IntegrityResult:
    gold = [Span.from_dict(s) for s in note["spans"]]
    ref = tokens(redact(note["text"], gold))
    out = tokens(output)
    gold_tokens = {t for s in gold for t in _TOKEN.findall(s.text)}
    res = IntegrityResult()
    sm = difflib.SequenceMatcher(None, ref, out, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        dropped = ref[i1:i2]
        added = out[j1:j2]
        leaks = [t for t in added if t in gold_tokens]
        inserted = [t for t in added if t not in gold_tokens]
        res.dropped += dropped
        res.inserted += inserted
        res.leaked += leaks
        if (dropped or inserted) and len(res.examples) < max_examples:
            before = " ".join(ref[max(0, i1 - 4) : i2 + 4])
            after = " ".join(out[max(0, j1 - 4) : j2 + 4])
            res.examples.append(f"{before}  ->  {after}")
    return res


def strip_wrapper(text: str) -> str:
    """Remove a Markdown code fence if the model wrapped its answer in one."""
    m = re.fullmatch(r"\s*```[a-z]*\n(.*?)\n?```\s*", text, re.S)
    return m.group(1) if m else text


def rewrite(client: OllamaClient, text: str) -> dict:
    try:
        res = client.chat(
            REWRITE_SYSTEM,
            f"Clinical note:\n<<<\n{text}\n>>>",
            num_predict=2048,
            cache_tag=REWRITE_VERSION,
        )
    except OllamaError as e:
        return {"output": "", "failure": e.kind, "runtime_s": 0.0}
    failure = "output_truncated" if res.done_reason == "length" else ""
    out = strip_wrapper(res.content).strip()
    # The prompt wraps the note in <<< >>>; models sometimes echo the markers.
    out = re.sub(r"^(Clinical note:\s*)?<<<\s*|\s*>>>$", "", out)
    return {"output": out, "failure": failure, "runtime_s": res.runtime_s}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Ask the LLM to rewrite every note with identifiers redacted."
    )
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    notes = load_notes()[: args.limit]
    client = OllamaClient()
    rows = []
    for i, note in enumerate(notes):
        rows.append({"note_id": note["id"], **rewrite(client, note["text"])})
        if (i + 1) % 25 == 0:
            print(f"  rewrite {i + 1}/{len(notes)}", flush=True)
    write_jsonl(pred_path("llm_rewrite"), rows)
    print(f"wrote {len(rows)} rewrites -> {pred_path('llm_rewrite')}")


if __name__ == "__main__":
    main()
