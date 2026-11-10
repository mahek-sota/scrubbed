"""Run a method over every note and save predictions with per-note runtime.

Run: python -m deid.run regex|presidio|llm|llm_t0 [--runs N] [--limit N]
"""

from __future__ import annotations

import argparse
import time

from deid.io import load_notes, pred_path, read_jsonl, write_jsonl
from deid.methods import Method
from deid.spans import Span


def get_method(name: str) -> Method:
    if name == "regex":
        from deid.methods.regex import RegexMethod

        return RegexMethod()
    if name == "presidio":
        from deid.methods.presidio import PresidioMethod

        return PresidioMethod()
    if name == "llm":
        from deid.methods.llm import LLMMethod

        return LLMMethod()
    if name == "llm_t0":  # same prompt, greedy decoding
        from deid.methods.llm import LLMMethod

        return LLMMethod(temperature=0.0, name="llm_t0")
    raise SystemExit(f"unknown method {name!r}")


def run(method: Method, notes: list[dict], runs: int = 1) -> list[dict]:
    rows = []
    for r in range(runs):
        for i, note in enumerate(notes):
            t0 = time.perf_counter()
            pred = method.predict(note["text"], run=r)
            elapsed = time.perf_counter() - t0
            # A cached LLM response reports the runtime measured when it was first produced.
            runtime = pred.meta.pop("runtime_s", elapsed)
            rows.append(
                {
                    "note_id": note["id"],
                    "run": r,
                    "spans": [s.to_dict() for s in pred.spans],
                    "runtime_s": runtime,
                    "meta": pred.meta,
                }
            )
            if (i + 1) % 25 == 0:
                print(f"  run {r} {i + 1}/{len(notes)}", flush=True)
    return rows


def load_predictions(method: str, run: int = 0) -> dict[str, list[Span]]:
    return {
        row["note_id"]: [Span.from_dict(s) for s in row["spans"]]
        for row in read_jsonl(pred_path(method))
        if row["run"] == run
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("method")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    notes = load_notes()[: args.limit]
    t0 = time.perf_counter()
    method = get_method(args.method)
    print(
        f"{args.method}: setup {time.perf_counter() - t0:.1f}s, {len(notes)} notes x {args.runs} run(s)"
    )
    rows = run(method, notes, args.runs)
    write_jsonl(pred_path(args.method), rows)
    total = sum(r["runtime_s"] for r in rows)
    print(f"wrote {len(rows)} rows -> {pred_path(args.method)} ({total / len(rows):.3f}s/note)")


if __name__ == "__main__":
    main()
