"""Build the comparison tables, charts and results.json used by the article and report.

Run: python -m deid.results
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from deid.categories import CATEGORIES  # noqa: E402
from deid.config import ASSETS_DIR  # noqa: E402
from deid.evaluate import score, trap_hits  # noqa: E402
from deid.integrity import compare  # noqa: E402
from deid.io import load_notes, pred_path, read_jsonl  # noqa: E402
from deid.methods.llm import FAILURE_KINDS, expand_names, find_all  # noqa: E402
from deid.spans import Span, redact  # noqa: E402

SPAN_METHODS = ["regex", "presidio", "llm"]
LABELS = {
    "regex": "Regex",
    "presidio": "Presidio",
    "llm": "LLM (spans)",
    "llm_t0": "LLM (spans, temp 0)",
    "llm_rewrite": "LLM (rewrite)",
}
# Reference palette slots 1-4 (validated adjacent-pair order), see the dataviz skill.
COLORS = {"regex": "#2a78d6", "presidio": "#eb6834", "llm": "#1baf7a", "llm_rewrite": "#eda100"}
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


# --------------------------------------------------------------------------- loading


def load_runs(method: str) -> dict[int, dict[str, dict]]:
    """{run: {note_id: row}}; empty if the method has not been run."""
    path = pred_path(method)
    if not path.exists():
        return {}
    runs: dict[int, dict[str, dict]] = defaultdict(dict)
    for row in read_jsonl(path):
        runs[row["run"]][row["note_id"]] = row
    return dict(runs)


def spans_of(rows: dict[str, dict]) -> dict[str, list[Span]]:
    return {nid: [Span.from_dict(s) for s in r["spans"]] for nid, r in rows.items()}


def _fmt(x: float, pct: bool = True) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{100 * x:.1f}" if pct else f"{x:.3f}"


def _mean_sd(values: list[float]) -> tuple[float, float]:
    vals = [v for v in values if not math.isnan(v)]
    if not vals:
        return math.nan, math.nan
    return statistics.mean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0)


# --------------------------------------------------------------------------- metrics


def detection_metrics(notes: list[dict], runs: dict[int, dict[str, dict]]) -> dict:
    """Overall + per-category metrics, averaged over runs (one run for deterministic methods)."""
    out: dict = {"runs": len(runs), "overall": {}, "by_category": {}}
    per_run = {
        mode: [score(notes, spans_of(rows), mode) for rows in runs.values()]
        for mode in ("exact", "overlap")
    }
    for mode, scores in per_run.items():
        prf = [s.overall.prf() for s in scores]
        o = {}
        for i, k in enumerate(("precision", "recall", "f1")):
            o[k], o[f"{k}_sd"] = _mean_sd([p[i] for p in prf])
        o["partial"] = statistics.mean(s.overall.partial for s in scores)
        o["label_accuracy"] = statistics.mean(s.label_accuracy for s in scores)
        out["overall"][mode] = o
        cats: dict = {}
        for cat in [*CATEGORIES, "OTHER"]:
            rows = [s.by_category.get(cat) for s in scores]
            if all(r is None for r in rows):
                continue
            vals = [r.prf() if r else (math.nan,) * 3 for r in rows]
            cats[cat] = {
                "n_gold": rows[0].n_gold if rows[0] else 0,
                "precision": _mean_sd([v[0] for v in vals])[0],
                "recall": _mean_sd([v[1] for v in vals])[0],
                "f1": _mean_sd([v[2] for v in vals])[0],
                "n_pred": statistics.mean(r.n_pred if r else 0 for r in rows),
            }
        out["by_category"][mode] = cats
    out["_scores_run0"] = per_run["overlap"][0]
    runtimes = [r["runtime_s"] for rows in runs.values() for r in rows.values()]
    out["runtime_mean_s"] = statistics.mean(runtimes)
    out["runtime_median_s"] = statistics.median(runtimes)
    return out


def top_missed(sc, k: int = 8) -> list[dict]:
    by_cat = Counter(m["category"] for m in sc.missed)
    out = []
    for cat, n in by_cat.most_common(k):
        ex = [m for m in sc.missed if m["category"] == cat]
        out.append({"category": cat, "missed": n, "examples": [e["context"] for e in ex[:3]]})
    return out


def name_slices(notes: list[dict], sc_by_method: dict) -> list[dict]:
    """Recall (overlap) on the hard slices: common-word names, lowercase names, roles,
    labeled vs unlabeled MRNs."""
    slices = {
        "NAME: common-word (Hope, Will, Rose...)": lambda s: (
            s["category"] == "NAME" and "common_word" in s["tags"]
        ),
        "NAME: lowercase": lambda s: s["category"] == "NAME" and "lowercase" in s["tags"],
        "NAME: other": lambda s: s["category"] == "NAME" and not s["tags"],
        "NAME: patient": lambda s: s["category"] == "NAME" and s["subtype"] == "patient",
        "NAME: relative": lambda s: s["category"] == "NAME" and s["subtype"] == "relative",
        "NAME: clinician": lambda s: s["category"] == "NAME" and s["subtype"] == "clinician",
        "MRN: labeled": lambda s: s["category"] == "MRN" and s["subtype"] == "labeled",
        "MRN: unlabeled": lambda s: s["category"] == "MRN" and s["subtype"] == "unlabeled",
        "DATE: no year (Mar 4, 3/4)": lambda s: (
            s["category"] == "DATE" and s["tags"][0] in ("abbrev_no_year", "md")
        ),
    }
    rows = []
    for label, pred in slices.items():
        total = sum(1 for n in notes for s in n["spans"] if pred(s))
        row = {"slice": label, "n": total}
        for m, sc in sc_by_method.items():
            missed = sum(1 for x in sc.missed if pred(x))
            row[m] = (total - missed) / total if total else math.nan
        rows.append(row)
    return rows


def llm_consistency(notes: list[dict], runs: dict[int, dict[str, dict]]) -> dict:
    ids = [n["id"] for n in notes]
    sets = {
        r: {nid: {(s["start"], s["end"]) for s in rows[nid]["spans"]} for nid in ids}
        for r, rows in runs.items()
    }
    identical = sum(1 for nid in ids if len({frozenset(sets[r][nid]) for r in sets}) == 1)
    jacc = []
    for a, b in combinations(sets, 2):
        for nid in ids:
            u = sets[a][nid] | sets[b][nid]
            jacc.append(len(sets[a][nid] & sets[b][nid]) / len(u) if u else 1.0)
    # How many of the runs found each gold identifier (overlap)?
    found_k = Counter()
    for note in notes:
        for g in note["spans"]:
            k = sum(
                any(
                    s["start"] < g["end"] and g["start"] < s["end"]
                    for s in runs[r][note["id"]]["spans"]
                )
                for r in runs
            )
            found_k[k] += 1
    failures = {r: Counter() for r in runs}
    for r, rows in runs.items():
        for row in rows.values():
            failures[r].update({k: v for k, v in row["meta"].get("failures", {}).items() if v})
    notes_failed = {
        r: sum(
            1
            for row in rows.values()
            if any(
                row["meta"]["failures"].get(k)
                for k in ("timeout", "http_error", "too_long", "invalid_json")
            )
        )
        for r, rows in runs.items()
    }
    not_found = Counter(
        t
        for rows in runs.values()
        for row in rows.values()
        for t in row["meta"].get("not_found", [])
    )
    prompt_tokens = [
        row["meta"].get("prompt_tokens", 0) for rows in runs.values() for row in rows.values()
    ]
    return {
        "runs": len(runs),
        "identical_notes": identical,
        "n_notes": len(ids),
        "mean_pairwise_jaccard": statistics.mean(jacc) if jacc else math.nan,
        "gold_found_in_k_runs": {str(k): found_k[k] for k in sorted(found_k)},
        "failures_by_run": {str(r): {k: failures[r][k] for k in FAILURE_KINDS} for r in failures},
        "notes_with_call_or_json_failure": {str(r): v for r, v in notes_failed.items()},
        "not_in_note_examples": [t for t, _ in not_found.most_common(10)],
        "max_prompt_tokens": max(prompt_tokens) if prompt_tokens else 0,
    }


def name_expansion(notes: list[dict], runs: dict[int, dict[str, dict]]) -> dict:
    """Post-hoc variant of the LLM span method (see `expand_names`), scored per run."""
    by_id = {n["id"]: n for n in notes}
    base, exp, name_base, name_exp, traps_base, traps_exp = [], [], [], [], [], []
    for rows in runs.values():
        preds = spans_of(rows)
        expanded = {k: expand_names(by_id[k]["text"], v) for k, v in preds.items()}
        a, b = score(notes, preds, "overlap"), score(notes, expanded, "overlap")
        base.append(a.overall.prf()[2])
        exp.append(b.overall.prf()[2])
        name_base.append(a.by_category["NAME"].prf()[1])
        name_exp.append(b.by_category["NAME"].prf()[1])
        traps_base.append(len(trap_hits(notes, preds)[1]))
        traps_exp.append(len(trap_hits(notes, expanded)[1]))
    m = statistics.mean
    return {
        "f1": m(base),
        "f1_expanded": m(exp),
        "name_recall": m(name_base),
        "name_recall_expanded": m(name_exp),
        "trap_hits": m(traps_base),
        "trap_hits_expanded": m(traps_exp),
    }


def integrity(notes: list[dict], redacted: dict[str, dict[str, str]]) -> dict:
    """redacted: {method: {note_id: redacted_text}}."""
    out = {}
    for m, texts in redacted.items():
        res = [(n, compare(n, texts[n["id"]])) for n in notes if n["id"] in texts]
        k = len(res)
        out[m] = {
            "n": k,
            "changed": sum(r.changed for _, r in res) / k,
            "substantive_changed": sum(r.substantive_changed for _, r in res) / k,
            "numbers_changed": sum(r.numbers_changed for _, r in res) / k,
            "meds_changed": sum(r.meds_changed for _, r in res) / k,
            "dropped_per_note": statistics.mean(len(r.dropped) for _, r in res),
            "inserted_per_note": statistics.mean(len(r.inserted) for _, r in res),
            "top_dropped": Counter(t for _, r in res for t in r.dropped).most_common(12),
            "examples": [
                {"note_id": n["id"], "diff": e}
                for n, r in res
                for e in r.examples[:1]
                if r.numbers_changed or r.meds_changed
            ][:6]
            or [{"note_id": n["id"], "diff": e} for n, r in res for e in r.examples[:1]][:6],
        }
    return out


def rewrite_leak_rate(notes: list[dict], rewrites: dict[str, str]) -> dict:
    """Approximate recall for the rewrite approach: a gold identifier counts as leaked if
    its exact text still appears in the rewritten note (counted per occurrence)."""
    total = leaked = 0
    by_cat = Counter()
    tot_cat = Counter()
    for note in notes:
        out = rewrites.get(note["id"])
        if out is None:
            continue
        counts = Counter(s["text"] for s in note["spans"])
        cats = {s["text"]: s["category"] for s in note["spans"]}
        for text, n in counts.items():
            left = min(n, len(find_all(out, text)) if text in out else 0)
            total += n
            leaked += left
            by_cat[cats[text]] += left
            tot_cat[cats[text]] += n
    return {
        "approx_recall": 1 - leaked / total if total else math.nan,
        "by_category": {c: 1 - by_cat[c] / tot_cat[c] for c in CATEGORIES if tot_cat[c]},
    }


# --------------------------------------------------------------------------- charts


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def chart_recall_by_category(by_cat: dict[str, dict], path: Path, methods: list[str]) -> None:
    cats = list(CATEGORIES)
    fig, ax = plt.subplots(figsize=(8, 6.2), dpi=150, facecolor=SURFACE)
    _style(ax)
    h = 0.8 / len(methods)
    for i, m in enumerate(methods):
        vals = [by_cat[m].get(c, {}).get("recall", 0) or 0 for c in cats]
        y = [j + (i - (len(methods) - 1) / 2) * h for j in range(len(cats))]
        ax.barh(
            y,
            vals,
            height=h - 0.04,
            color=COLORS[m],
            label=LABELS[m],
            edgecolor=SURFACE,
            linewidth=1,
        )
        for yy, v in zip(y, vals, strict=True):
            if v == 0:  # an empty bar is invisible; say it
                note = "not attempted" if m == "regex" else "0%"
                ax.text(0.005, yy, note, va="center", fontsize=7, color=INK2)
    ax.set_yticks(range(len(cats)), cats, color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_title("Recall by category (overlap match)", loc="left", color=INK, fontsize=12, pad=24)
    ax.legend(
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        ncol=len(methods),
        frameon=False,
        fontsize=9,
        labelcolor=INK2,
    )
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def chart_overall(overall: dict[str, dict], path: Path, methods: list[str]) -> None:
    metrics = ["precision", "recall", "f1"]
    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=150, facecolor=SURFACE)
    _style(ax)
    h = 0.8 / len(methods)
    for i, m in enumerate(methods):
        vals = [overall[m]["overlap"][k] for k in metrics]
        y = [j + (i - (len(methods) - 1) / 2) * h for j in range(len(metrics))]
        ax.barh(
            y,
            vals,
            height=h - 0.04,
            color=COLORS[m],
            label=LABELS[m],
            edgecolor=SURFACE,
            linewidth=1,
        )
        for yy, v in zip(y, vals, strict=True):
            ax.text(v + 0.01, yy, f"{100 * v:.0f}%", va="center", fontsize=8, color=INK2)
    ax.set_yticks(range(3), ["Precision", "Recall", "F1"], color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.08)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_title(
        "Overall, all categories (overlap match)", loc="left", color=INK, fontsize=12, pad=24
    )
    ax.legend(
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        ncol=len(methods),
        frameon=False,
        fontsize=9,
        labelcolor=INK2,
    )
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def chart_integrity(integ: dict[str, dict], path: Path) -> None:
    methods = list(integ)
    fig, ax = plt.subplots(figsize=(8, 2.8), dpi=150, facecolor=SURFACE)
    _style(ax)
    vals = [integ[m]["substantive_changed"] for m in methods]
    ax.barh(range(len(methods)), vals, height=0.6, color=[COLORS[m] for m in methods])
    for j, v in enumerate(vals):
        ax.text(v + 0.01, j, f"{100 * v:.0f}%", va="center", fontsize=9, color=INK2)
    ax.set_yticks(range(len(methods)), [LABELS[m] for m in methods], color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.08)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_title(
        "Notes where clinical/non-identifier text changed (titles like Dr. ignored)",
        loc="left",
        color=INK,
        fontsize=12,
    )
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------- markdown


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row.values) + " |")
    return "\n".join(lines)


def build() -> dict:
    notes = load_notes()
    all_runs = {m: load_runs(m) for m in ["regex", "presidio", "llm", "llm_t0"]}
    methods = [m for m in SPAN_METHODS if all_runs[m]]
    det = {m: detection_metrics(notes, all_runs[m]) for m in methods}
    if all_runs["llm_t0"]:
        det["llm_t0"] = detection_metrics(notes, all_runs["llm_t0"])

    res: dict = {
        "n_notes": len(notes),
        "methods": methods,
        "n_gold": sum(len(n["spans"]) for n in notes),
    }
    res["gold_by_category"] = dict(Counter(s["category"] for n in notes for s in n["spans"]))
    res["overall"] = {m: d["overall"] for m, d in det.items()}
    res["by_category"] = {m: d["by_category"]["overlap"] for m, d in det.items()}
    res["by_category_exact"] = {m: d["by_category"]["exact"] for m, d in det.items()}
    res["runtime"] = {
        m: {"mean_s": d["runtime_mean_s"], "median_s": d["runtime_median_s"]}
        for m, d in det.items()
    }
    res["runs"] = {m: d["runs"] for m, d in det.items()}
    sc0 = {m: det[m]["_scores_run0"] for m in methods}
    res["top_missed"] = {m: top_missed(sc0[m]) for m in methods}
    res["slices"] = name_slices(notes, sc0)
    res["false_positive_examples"] = {
        m: Counter((f["category"], f["text"]) for f in sc0[m].false_positives).most_common(10)
        for m in methods
    }

    traps = {}
    for m in [*methods, *(["llm_t0"] if "llm_t0" in det else [])]:
        rates = []
        hits_all = []
        for rows in all_runs[m].values():
            n, hits = trap_hits(notes, spans_of(rows))
            rates.append(len(hits) / n)
            hits_all += hits
        traps[m] = {
            "n_terms": n,
            "hit_rate": statistics.mean(rates),
            "top_terms": Counter(h["term"] for h in hits_all).most_common(8),
            "examples": [
                f"{h['term']} -> {h['predicted']} as {h['categories']}" for h in hits_all[:5]
            ],
        }
    res["traps"] = traps

    if all_runs["llm"]:
        res["llm_consistency"] = llm_consistency(notes, all_runs["llm"])
        res["name_expansion"] = name_expansion(notes, all_runs["llm"])
    if all_runs["llm_t0"]:
        res["llm_t0_consistency"] = llm_consistency(notes, all_runs["llm_t0"])

    by_id = {n["id"]: n for n in notes}
    redacted = {
        m: {
            nid: redact(by_id[nid]["text"], [Span.from_dict(s) for s in row["spans"]])
            for nid, row in all_runs[m][0].items()
        }
        for m in methods
    }
    rw_path = pred_path("llm_rewrite")
    if rw_path.exists():
        rw_rows = read_jsonl(rw_path)
        redacted["llm_rewrite"] = {r["note_id"]: r["output"] for r in rw_rows}

        def upper_ratio(t: str) -> float:
            letters = [c for c in t if c.isalpha()]
            return sum(c.isupper() for c in letters) / len(letters) if letters else 0.0

        res["rewrite"] = {
            "all_caps_notes": sum(
                upper_ratio(r["output"]) > 0.8 > upper_ratio(by_id[r["note_id"]]["text"])
                for r in rw_rows
            ),
            "runtime_mean_s": statistics.mean(r["runtime_s"] for r in rw_rows),
            "failures": dict(Counter(r["failure"] for r in rw_rows if r["failure"])),
            **rewrite_leak_rate(notes, redacted["llm_rewrite"]),
        }
    res["integrity"] = integrity(notes, redacted)
    return res


def write_assets(res: dict) -> None:
    ASSETS_DIR.mkdir(exist_ok=True)
    methods = res["methods"]
    ms = [*methods, *(["llm_t0"] if "llm_t0" in res["overall"] else [])]

    # Overall table
    rows = []
    for m in ms:
        o, e = res["overall"][m]["overlap"], res["overall"][m]["exact"]
        sd = f" ± {100 * o['f1_sd']:.1f}" if res["runs"][m] > 1 else ""
        rt = res["runtime"][m]["mean_s"]
        rows.append(
            {
                "Method": LABELS[m],
                "Precision": _fmt(o["precision"]),
                "Recall": _fmt(o["recall"]),
                "F1 (overlap)": _fmt(o["f1"]) + sd,
                "F1 (exact)": _fmt(e["f1"]),
                "Partial leaks": f"{o['partial']:.0f}",
                "Label agreement": _fmt(o["label_accuracy"]),
                "Runtime / note": f"{rt * 1000:.1f} ms" if rt < 1 else f"{rt:.1f} s",
            }
        )
    overall_df = pd.DataFrame(rows)
    overall_df.to_csv(ASSETS_DIR / "metrics_overall.csv", index=False)

    # Per-category recall/precision
    cat_rows = []
    for c in CATEGORIES:
        row = {"Category": c, "Gold": res["gold_by_category"].get(c, 0)}
        for m in ms:
            d = res["by_category"][m].get(c, {})
            row[f"{LABELS[m]} R"] = _fmt(d.get("recall", math.nan))
            row[f"{LABELS[m]} P"] = _fmt(d.get("precision", math.nan))
        cat_rows.append(row)
    cat_df = pd.DataFrame(cat_rows)
    cat_df.to_csv(ASSETS_DIR / "metrics_by_category.csv", index=False)
    long = [
        {"method": m, "mode": mode, "category": c, **v}
        for m in ms
        for mode, key in (("overlap", "by_category"), ("exact", "by_category_exact"))
        for c, v in res[key][m].items()
    ]
    pd.DataFrame(long).to_csv(ASSETS_DIR / "metrics_by_category_long.csv", index=False)

    slice_df = pd.DataFrame(
        [
            {"Slice": s["slice"], "n": s["n"], **{LABELS[m]: _fmt(s[m]) for m in methods}}
            for s in res["slices"]
        ]
    )
    trap_df = pd.DataFrame(
        [
            {
                "Method": LABELS[m],
                "Trap terms flagged": _fmt(t["hit_rate"]) + "%",
                "Most often flagged": ", ".join(f"{term} ({k})" for term, k in t["top_terms"][:4])
                or "–",
            }
            for m, t in res["traps"].items()
        ]
    )

    charts = []
    chart_recall_by_category(res["by_category"], ASSETS_DIR / "recall_by_category.png", methods)
    charts.append("recall_by_category.png")
    chart_overall(res["overall"], ASSETS_DIR / "overall_prf.png", methods)
    charts.append("overall_prf.png")
    if res.get("integrity"):
        chart_integrity(res["integrity"], ASSETS_DIR / "integrity.png")
        charts.append("integrity.png")
    res["charts"] = charts

    md = [
        "# Results",
        "",
        f"{res['n_notes']} synthetic notes, {res['n_gold']} gold identifiers. Recall/precision "
        "ignore the predicted label (did the identifier get removed?). Overlap = any shared "
        "character; exact = same start and end. LLM numbers are the mean of "
        f"{res['runs'].get('llm', 0)} runs (± = standard deviation across runs).",
        "",
        "## Overall",
        "",
        _md_table(overall_df),
        "",
        "*Partial leaks*: identifiers found but not fully covered (part of the text survives "
        "redaction). *Label agreement*: share of found identifiers given the right category.",
        "",
        "![Overall](overall_prf.png)",
        "",
        "## Recall and precision by category (overlap)",
        "",
        _md_table(cat_df),
        "",
        "![Recall by category](recall_by_category.png)",
        "",
        "## Hard slices (recall, overlap)",
        "",
        _md_table(slice_df),
        "",
        "## Eponym traps (false positives)",
        "",
        "Clinical terms containing a person's name (Parkinson's disease, Down syndrome, "
        "Foley catheter, Apgar score...). Share of trap terms that a method redacted:",
        "",
        _md_table(trap_df),
        "",
        "## Top missed identifiers (run 0, overlap)",
        "",
    ]
    for m in methods:
        md.append(f"### {LABELS[m]}")
        md.append("")
        for t in res["top_missed"][m][:5]:
            md.append(f"- **{t['category']}**: {t['missed']} missed, e.g. `{t['examples'][0]}`")
        md.append("")
    md += ["## Most frequent false positives (run 0)", ""]
    for m in methods:
        fps = ", ".join(
            f"`{t}` as {c} ({k})" for (c, t), k in res["false_positive_examples"][m][:6]
        )
        md.append(f"- **{LABELS[m]}**: {fps or 'none'}")
    md.append("")

    if "llm_consistency" in res:
        c = res["llm_consistency"]
        f = c["failures_by_run"]
        md += [
            "## LLM reliability",
            "",
            f"- Runs: {c['runs']} per note at Ollama's default sampling. Notes with an identical "
            f"set of spans in every run: {c['identical_notes']}/{c['n_notes']} "
            f"({100 * c['identical_notes'] / c['n_notes']:.0f}%). Mean pairwise Jaccard of span "
            f"sets: {c['mean_pairwise_jaccard']:.3f}.",
            f"- Gold identifiers found in k of {c['runs']} runs: "
            + ", ".join(f"{k}: {v}" for k, v in c["gold_found_in_k_runs"].items()),
            "- Failures per run: "
            + "; ".join(
                f"run {r}: " + ", ".join(f"{k}={v}" for k, v in d.items() if v)
                if any(d.values())
                else f"run {r}: none"
                for r, d in f.items()
            ),
            f"- Items the model returned that are not in the note (examples): "
            f"{', '.join(repr(t) for t in c['not_in_note_examples'][:6]) or 'none'}",
            f"- Largest prompt: {c['max_prompt_tokens']} tokens (num_ctx 8192, so no truncation).",
        ]
        if "name_expansion" in res:
            ne = res["name_expansion"]
            md.append(
                f"- Post-hoc (not the headline method): also redacting each word of every "
                f"returned NAME raises name recall {_fmt(ne['name_recall'])} -> "
                f"{_fmt(ne['name_recall_expanded'])} and F1 {_fmt(ne['f1'])} -> "
                f"{_fmt(ne['f1_expanded'])}; trap hits per run {ne['trap_hits']:.0f} -> "
                f"{ne['trap_hits_expanded']:.0f}."
            )
        if "llm_t0_consistency" in res:
            t0 = res["overall"]["llm_t0"]["overlap"]
            md.append(
                f"- Same prompt at temperature 0 (1 run): precision {_fmt(t0['precision'])}, "
                f"recall {_fmt(t0['recall'])}, F1 {_fmt(t0['f1'])}."
            )
        md.append("")

    if res.get("integrity"):
        integ = res["integrity"]
        idf = pd.DataFrame(
            [
                {
                    "Method": LABELS[m],
                    "Notes changed": _fmt(v["changed"]) + "%",
                    "Changed, ignoring Dr/Mr/Ms/MD/RN": _fmt(v["substantive_changed"]) + "%",
                    "Numbers changed": _fmt(v["numbers_changed"]) + "%",
                    "Medications changed": _fmt(v["meds_changed"]) + "%",
                    "Words dropped / note": f"{v['dropped_per_note']:.2f}",
                    "Words inserted / note": f"{v['inserted_per_note']:.2f}",
                }
                for m, v in integ.items()
            ]
        )
        md += [
            "## Content integrity",
            "",
            "Each redacted note is compared with the original minus its gold identifiers. "
            "Any other word that disappears, changes or appears counts. For the span methods "
            "this can only come from over-redaction; the rewrite can change anything.",
            "",
            _md_table(idf),
            "",
            "![Integrity](integrity.png)",
            "",
        ]
        if "rewrite" in res:
            rw = res["rewrite"]
            md.append(
                f"Rewrite approach: approximate recall {_fmt(rw['approx_recall'])}% (identifier "
                f"text no longer present), {rw['runtime_mean_s']:.1f} s/note, failures: "
                f"{rw['failures'] or 'none'}. Notes returned mostly in CAPITALS: "
                f"{rw['all_caps_notes']}/{res['n_notes']}."
            )
            md.append("")
        for m, v in integ.items():
            if v["examples"]:
                md.append(f"**{LABELS[m]}** examples (reference -> output):")
                md.append("")
                for e in v["examples"][:3]:
                    md.append(f"- `{e['note_id']}`: `{e['diff']}`")
                md.append("")
    (ASSETS_DIR / "results.md").write_text("\n".join(md))
    public = {k: v for k, v in res.items()}
    (ASSETS_DIR / "results.json").write_text(json.dumps(public, indent=1, default=str))


def main() -> None:
    res = build()
    write_assets(res)
    print(f"wrote {ASSETS_DIR / 'results.md'} and charts: {', '.join(res['charts'])}")


if __name__ == "__main__":
    main()
