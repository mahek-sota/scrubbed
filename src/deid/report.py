"""Write article_assets/report.html: one self-contained, interactive page.

Structure: a short summary first, then one section per question (accuracy, blind spots,
consistency, side effects), a note viewer, and the method. Each section leads with a
one-sentence takeaway and a single chart; detail tables sit behind "Show details".

Charts are drawn in the browser as SVG from data embedded in the page (no external
scripts, no network). The static PNGs in article_assets/ are for the Medium post.

Run: python -m deid.report   (after python -m deid.results)
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path

from deid.categories import CATEGORIES
from deid.config import ASSETS_DIR, OllamaConfig
from deid.io import load_notes, pred_path, read_jsonl
from deid.results import load_runs
from deid.spans import Span, redact

REPORT_PATH = ASSETS_DIR / "report.html"
_ASSETS = Path(__file__).parent / "report_assets"

# Fixed display order; colors follow the validated categorical order in report.css.
METHOD_ORDER = ["regex", "presidio", "llm", "llm_rewrite", "llm_t0"]
LABELS = {
    "regex": "Regex",
    "presidio": "Presidio",
    "llm": "Local LLM",
    "llm_rewrite": "LLM rewrite",
    "llm_t0": "Local LLM (temp 0)",
}
CAT_LABELS = {
    "NAME": "Names", "DATE": "Dates", "AGE_OVER_89": "Ages over 89", "PHONE": "Phone numbers",
    "EMAIL": "Emails", "MRN": "Record numbers", "SSN": "SSNs", "ADDRESS": "Street addresses",
    "CITY": "Cities", "ZIP": "ZIP codes", "HOSPITAL": "Hospitals",
}  # fmt: skip
NOTE_TYPE_PLURALS = {
    "discharge_summary": "discharge summaries", "progress_note": "progress notes",
    "referral_letter": "referral letters", "ed_note": "ED notes",
    "lab_followup": "lab follow-ups", "phone_message": "phone messages",
}  # fmt: skip
NOTE_TYPE_NAMES = {
    "discharge_summary": "discharge summary", "progress_note": "progress note",
    "referral_letter": "referral letter", "ed_note": "ED note",
    "lab_followup": "lab follow-up", "phone_message": "phone message",
}  # fmt: skip
REGEX_CATEGORIES = ["DATE", "PHONE", "EMAIL", "MRN", "SSN", "ZIP"]


# --------------------------------------------------------------------------- formatting


def pct(x: float | None, d: int = 0) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{100 * x:.{d}f}%"


def _speed(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms" if seconds < 1 else f"{seconds:.1f} s"


def _e(s: object) -> str:
    return html.escape(str(s))


def _table(head: list[str], rows: list[list[object]]) -> str:
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _details(summary: str, inner: str) -> str:
    return f"<details><summary>{_e(summary)}</summary>{inner}</details>"


def pick_default_note(notes: list[dict]) -> int:
    """A note that shows a bit of everything: an eponym trap and a common-word name."""
    for i, n in enumerate(notes):
        if (
            n["note_type"] == "referral_letter"
            and n["traps"]
            and any("common_word" in s["tags"] for s in n["spans"])
        ):
            return i
    return 0


# --------------------------------------------------------------------------- sections


def summary_section(res: dict) -> str:
    ov, cat, rt = res["overall"], res["by_category"], res["runtime"]
    R, P, L = (ov[m]["overlap"] for m in ("regex", "presidio", "llm"))
    speedup = rt["llm"]["mean_s"] / rt["presidio"]["mean_s"]

    def card(key: str, good: list[str], bad: list[str]) -> str:
        o = ov[key]["overlap"]
        items = "".join(f'<li class="good"><span class="ic">✓</span>{g}</li>' for g in good)
        items += "".join(f'<li class="bad"><span class="ic">✕</span>{b}</li>' for b in bad)
        return (
            f'<div class="verdict" style="--c:var(--c-{key})"><div class="name">{LABELS[key]}</div>'
            f'<div class="score">{100 * o["f1"]:.0f}<small>F1 score</small></div>'
            f'<div class="speed">removes {pct(o["recall"])} of identifiers · '
            f"{_speed(rt[key]['mean_s'])} per note</div><ul>{items}</ul></div>"
        )

    cards = "".join(
        [
            card(
                "regex",
                [f"Most precise: {pct(R['precision'])} of what it removes is an identifier",
                 "Near-perfect on fixed formats: phones, emails, SSNs, dates"],
                ["Blind to names, addresses, cities and hospitals"],
            ),
            card(
                "presidio",
                [f"Best at names ({pct(cat['presidio']['NAME']['recall'])})",
                 f"Fast: {_speed(rt['presidio']['mean_s'])} per note"],
                [f"Misses most hospitals ({pct(cat['presidio']['HOSPITAL']['recall'])}) and ZIP codes "
                 f"({pct(cat['presidio']['ZIP']['recall'])})",
                 "Reads “daily” as a date and “MD” as Maryland"],
            ),
            card(
                "llm",
                [f"Best at hospitals ({pct(cat['llm']['HOSPITAL']['recall'])}) and addresses "
                 f"({pct(cat['llm']['ADDRESS']['recall'])})",
                 f"More ages over 89 found ({pct(cat['llm']['AGE_OVER_89']['recall'])} vs "
                 f"{pct(cat['presidio']['AGE_OVER_89']['recall'])} for Presidio), but often on younger ages too"],
                [f"Misses short name mentions (names {pct(cat['llm']['NAME']['recall'])})",
                 f"About {speedup:.0f}× slower than Presidio, and varies run to run"],
            ),
        ]
    )  # fmt: skip

    points = []
    ne = res.get("name_expansion")
    if ne:
        points.append(
            "<b>Most of the LLM's misses can be fixed in code.</b> It lists “Alexander, Tracy” "
            "once and the code never redacts a later “Ms. Alexander”. Redacting each word of every "
            f"name it returns lifts name recall from {pct(ne['name_recall'])} to "
            f"{pct(ne['name_recall_expanded'])}, with no new false alarms on medical terms."
        )
    if "rewrite" in res:
        points.append(
            "<b>Let the model point at identifiers; let code remove them.</b> Asking the LLM to "
            f"rewrite the note instead removed only about {pct(res['rewrite']['approx_recall'])} of "
            "identifiers, and it added, reworded and capitalized text along the way."
        )
    c = res.get("llm_consistency")
    if c:
        points.append(
            "<b>The LLM doesn't give the same answer twice.</b> Across three runs on the same "
            f"note, only {pct(c['identical_notes'] / c['n_notes'])} of notes came out identical, so "
            "a single test run tells you little about the next one."
        )
    pts = "".join(f'<div class="point"><p>{p}</p></div>' for p in points)
    return f"""
<section id="summary">
  <div class="num">01 · SUMMARY</div>
  <h2>The short answer</h2>
  <p class="takeaway">Presidio and the local LLM remove about the same share of identifiers
  ({pct(P["recall"])} and {pct(L["recall"])}), but they miss different things, and the LLM is about
  {speedup:.0f}× slower. Regex is a precise but narrow baseline.</p>
  <div class="verdicts">{cards}</div>
  <div class="points">{pts}</div>
</section>"""


def pick_input_example(notes: list[dict]) -> dict:
    """A short note that still shows a lot: has a medical-term trap and the widest range of
    identifier types. Ties go to the shorter note."""
    with_traps = [n for n in notes if n["traps"]] or notes
    return max(
        with_traps, key=lambda n: (len({s["category"] for s in n["spans"]}), -len(n["text"]))
    )


def _highlight_input(note: dict) -> str:
    """The note as the methods received it, with gold identifiers and traps marked."""
    marks = [(s["start"], s["end"], "gold", CAT_LABELS[s["category"]]) for s in note["spans"]]
    marks += [
        (t["start"], t["end"], "trap", "Medical term, not an identifier") for t in note["traps"]
    ]
    out, pos = [], 0
    for start, end, cls, title in sorted(marks):
        out.append(_e(note["text"][pos:start]))
        out.append(f'<span class="{cls}" title="{_e(title)}">{_e(note["text"][start:end])}</span>')
        pos = end
    out.append(_e(note["text"][pos:]))
    return "".join(out)


def _tags_html(text: str) -> str:
    import re

    return re.sub(r"\[([A-Z_0-9]+)\]", r'<span class="tag">[\1]</span>', _e(text))


def input_section(notes: list[dict]) -> str:
    note = pick_input_example(notes)
    gold = [Span.from_dict(s) for s in note["spans"]]
    counts: dict[str, int] = {}
    for s in note["spans"]:
        counts[s["category"]] = counts.get(s["category"], 0) + 1
    chips = "".join(
        f'<span class="pill">{CAT_LABELS[c]} <b>{counts[c]}</b></span>'
        for c in CATEGORIES
        if c in counts
    )
    if note["traps"]:
        chips += f'<span class="pill">medical-term traps <b>{len(note["traps"])}</b></span>'
    types: dict[str, int] = {}
    for n in notes:
        types[n["note_type"]] = types.get(n["note_type"], 0) + 1
    type_list = ", ".join(NOTE_TYPE_PLURALS.get(t, t.replace("_", " ")) for t in types)
    label_rows = [
        [s["text"], s["category"], f"{s['start']}–{s['end']}", s.get("subtype", "")]
        for s in note["spans"][:8]
    ]
    key = json.dumps(
        {k: note["spans"][0][k] for k in ("start", "end", "text", "category")}, ensure_ascii=False
    )
    details = _details(
        "Show the answer key for this note",
        '<p class="card-sub" style="margin-top:8px">Every identifier was recorded with its exact '
        "position while the note was generated, so the answer key is correct by construction. "
        f"Stored as JSON, e.g. <code>{_e(key)}</code>. First {len(label_rows)} of {len(note['spans'])}:</p>"
        + _table(["Text", "Type", "Characters", "Role / format"], label_rows),
    )
    return f"""
<section id="input">
  <div class="num">02 · THE INPUT</div>
  <h2>What each method was given</h2>
  <p class="takeaway">Each method received a plain-text note like this one and had to find every
  identifier in it. The right-hand side is the answer it was scored against.</p>
  <div class="card">
    <div class="card-head"><div><h3>Sample: {_e(note["id"])} · {_e(NOTE_TYPE_NAMES.get(note["note_type"], note["note_type"]))}</h3>
      <div class="card-sub">One of {len(notes)} synthetic notes ({_e(type_list)}). Hover a highlight to see its type.</div></div></div>
    <div class="pane-stats">{chips}</div>
    <div class="panes">
      <div class="pane"><div class="pane-title">Input note</div><pre>{_highlight_input(note)}</pre></div>
      <div class="pane"><div class="pane-title">Correct output</div><pre>{_tags_html(redact(note["text"], gold))}</pre></div>
    </div>
    <div class="key-legend"><span><span class="gold">highlighted</span> identifier to remove</span>
      <span><span class="trap">dotted</span> medical term that contains a name but must stay</span>
      <span><span class="tag">[TAG]</span> replacement</span></div>
    {details}
  </div>
</section>"""


# --------------------------------------------------------------------------- diagram


def _box(x: int, y: int, w: int, h: int, title: str, sub: str = "", bar: str = "") -> str:
    """A component box: title and optional subtitle, centered; optional colored left bar."""
    cx = x + w / 2
    ty = y + h / 2 + (-7 if sub else 4)
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" '
        'style="fill:var(--card);stroke:var(--line);stroke-width:1.5"/>'
    ]
    if bar:
        out.append(
            f'<rect x="{x}" y="{y + 10}" width="4" height="{h - 20}" rx="2" style="fill:var(--c-{bar})"/>'
        )
    out.append(
        f'<text x="{cx}" y="{ty}" text-anchor="middle" font-size="13" font-weight="650" fill="currentColor">{_e(title)}</text>'
    )
    if sub:
        out.append(
            f'<text x="{cx}" y="{ty + 17}" text-anchor="middle" font-size="11" style="fill:var(--ink2)">{_e(sub)}</text>'
        )
    return "".join(out)


def _arrow(d: str, label: str = "", lx: float = 0, ly: float = 0, accent: bool = False) -> str:
    color = "var(--accent)" if accent else "var(--ink3)"
    marker = "arr-accent" if accent else "arr"
    out = f'<path d="{d}" fill="none" style="stroke:{color}" stroke-width="1.6" marker-end="url(#{marker})"/>'
    if label:
        weight = ' font-weight="600"' if accent else ""
        out += (
            f'<text x="{lx}" y="{ly}" text-anchor="middle" font-size="11"{weight} '
            f'style="fill:{"var(--accent)" if accent else "var(--ink2)"}">{_e(label)}</text>'
        )
    return out


def pipeline_diagram() -> str:
    """Inline SVG of the pipeline. The point it makes: methods only see note text, the answer
    key goes straight to scoring, the LLM returns text that code locates, and the rewrite
    variant has no spans. Everything sits inside one machine."""
    parts = [
        "<defs>",
        '<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,0 L10,5 L0,10 z" style="fill:var(--ink3)"/></marker>',
        '<marker id="arr-accent" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,0 L10,5 L0,10 z" style="fill:var(--accent)"/></marker>',
        "</defs>",
        # The machine boundary.
        '<rect x="10" y="10" width="1080" height="370" rx="16" fill="none" '
        'style="stroke:var(--ink3)" stroke-width="1.2" stroke-dasharray="6 5"/>',
        '<text x="30" y="36" font-size="12" font-weight="600" letter-spacing=".04em" '
        'style="fill:var(--ink3)">ONE LAPTOP · NO NETWORK CALLS</text>',
        # Data generation.
        _box(30, 180, 150, 84, "Note generator", "Faker + 6 templates"),
        _box(220, 100, 140, 56, "Note text", "200 notes"),
        _box(220, 310, 140, 56, "Answer key", "2,803 positions"),
        _arrow("M180,210 H198 V128 H218"),
        _arrow("M180,234 H198 V338 H218"),
        # The four methods, fed the same text from one bus.
        '<path d="M360,128 H390 M390,86 V290" fill="none" style="stroke:var(--ink3)" stroke-width="1.6"/>',
        _box(420, 60, 210, 52, "Regex", "hand-written patterns", "regex"),
        _box(420, 124, 210, 52, "Presidio", "spaCy NER + MRN rule", "presidio"),
        _box(420, 188, 210, 52, "Local LLM", "Ollama · returns text + type", "llm"),
        _box(420, 264, 210, 52, "LLM rewrite", "Ollama · returns whole note", "llm_rewrite"),
        _arrow("M390,86 H418"),
        _arrow("M390,150 H418"),
        _arrow("M390,214 H418"),
        _arrow("M390,290 H418"),
        '<text x="300" y="214" text-anchor="middle" font-size="11" style="fill:var(--ink2)">same text</text>',
        '<text x="300" y="229" text-anchor="middle" font-size="11" style="fill:var(--ink2)">to every method</text>',
        # Span handling: code does the redacting.
        '<rect x="670" y="60" width="150" height="180" rx="10" '
        'style="fill:var(--card);stroke:var(--line);stroke-width:1.5"/>',
        '<text x="745" y="128" text-anchor="middle" font-size="13" font-weight="650" '
        'fill="currentColor">Code redacts</text>',
        '<text x="745" y="152" text-anchor="middle" font-size="11" style="fill:var(--ink2)">find positions</text>',
        '<text x="745" y="168" text-anchor="middle" font-size="11" style="fill:var(--ink2)">merge overlaps</text>',
        '<text x="745" y="184" text-anchor="middle" font-size="11" style="fill:var(--ink2)">replace with [TAG]</text>',
        _arrow("M630,86 H668", "spans", 650, 79),
        _arrow("M630,150 H668", "spans", 650, 143),
        _arrow("M630,214 H668", "text", 650, 207),
        # Scoring.
        _box(860, 96, 160, 60, "Detection score", "precision · recall · F1"),
        _box(860, 244, 160, 66, "Integrity check", "what else changed"),
        _arrow("M820,126 H858", "spans", 840, 119),
        _arrow("M745,240 V262 H858", "redacted note", 800, 256),
        _arrow("M630,292 H858", "rewritten note · no positions", 745, 304),
        # The answer key bypasses every method.
        _arrow(
            "M360,338 H1050 V126 H1022",
            "answer key: used for scoring only, never shown to a method",
            705,
            330,
            accent=True,
        ),
        _arrow("M1050,277 H1022", accent=True),
    ]
    claim = (
        "Pipeline: a generator writes note text and an answer key. All four methods get only the "
        "text. Regex and Presidio return positions; the local LLM returns identifier text, which "
        "code locates and redacts; the rewrite variant returns a whole note. The answer key goes "
        "straight to scoring. Everything runs on one laptop."
    )
    return (
        f'<svg viewBox="0 0 1100 390" role="img" aria-label="{_e(claim)}" '
        'style="width:100%;height:auto;display:block;color:var(--ink);font-family:inherit">'
        + "".join(parts)
        + "</svg>"
    )


def design_section() -> str:
    return f"""
<section id="design">
  <div class="num">03 · HOW IT WORKS</div>
  <h2>System design</h2>
  <p class="takeaway">Every method gets the same note text and nothing else. The answer key goes
  straight to scoring, and every step runs on the same machine.</p>
  <figure class="card diagram">
    <div class="scroll-x"><div class="diagram-inner">{pipeline_diagram()}</div></div>
    <figcaption>The local LLM is never trusted with character positions: it names each
    identifier and its type, and code finds and removes it. The rewrite variant hands the whole
    note to the model, so the only way to check it is to compare its text with the original.</figcaption>
  </figure>
</section>"""


def accuracy_section(res: dict) -> str:
    ov, rt = res["overall"], res["runtime"]
    R, L = ov["regex"]["overlap"], ov["llm"]["overlap"]
    rows = []
    for m in [k for k in METHOD_ORDER if k in ov]:
        o, e = ov[m]["overlap"], ov[m]["exact"]
        sd = f" ± {100 * o['f1_sd']:.1f}" if res["runs"][m] > 1 else ""
        rows.append([
            LABELS[m], pct(o["precision"], 1), pct(o["recall"], 1), pct(o["f1"], 1) + sd,
            pct(e["f1"], 1), f"{o['partial']:.0f}", pct(o["label_accuracy"], 1), _speed(rt[m]["mean_s"]),
        ])  # fmt: skip
    table = _table(
        [
            "Method",
            "Precision",
            "Recall",
            "F1",
            "F1 (exact)",
            "Partly removed",
            "Right label",
            "Time / note",
        ],  # fmt: skip
        rows,
    )
    note = (
        '<p class="card-sub" style="margin-top:8px">“Partly removed”: identifiers where part of '
        "the text survived (e.g. only the first name redacted). “Exact”: the redaction must match "
        "the identifier boundaries exactly. The local LLM is the mean of 3 runs.</p>"
    )
    return f"""
<section id="accuracy">
  <div class="num">04 · ACCURACY</div>
  <h2>How much each method removes</h2>
  <p class="takeaway">Regex is the most precise ({pct(R["precision"])}) but removes only
  {pct(R["recall"])} of identifiers. Presidio and the local LLM each remove about
  {pct(L["recall"])}.</p>
  <div class="card">
    <div class="card-head"><div><h3>Precision, recall and F1</h3>
      <div class="card-sub">Hover a bar for details. An identifier counts as removed regardless of the label given.</div></div>
      <div id="acc-controls"></div></div>
    <div id="acc-legend"></div>
    <div class="chart" id="acc-chart"></div>
    {_details("Show details", table + note)}
  </div>
</section>"""


def blind_spots_section(res: dict) -> str:
    cat, traps = res["by_category"], res["traps"]
    ne = res.get("name_expansion", {})
    callouts = f"""
  <div class="callouts">
    <div class="callout" style="--c:var(--c-regex)"><b>Regex: only fixed formats</b>
      It doesn't attempt names, addresses, cities, hospitals or ages, and misses record numbers without a label.</div>
    <div class="callout" style="--c:var(--c-presidio)"><b>Presidio: hospitals and ZIP codes</b>
      It has no ZIP recognizer, and spaCy's organization label is switched off by default
      ({pct(cat["presidio"]["HOSPITAL"]["recall"])} of hospitals).</div>
    <div class="callout" style="--c:var(--c-llm)"><b>Local LLM: names</b>
      It names each person once and later short mentions slip through. One line of code
      raises this to {pct(ne.get("name_recall_expanded"))}.</div>
  </div>"""
    top_terms = "".join(
        f'<div class="callout" style="--c:var(--c-{m})"><b>{LABELS[m]}: {pct(traps[m]["hit_rate"])}</b>'
        + (_e(", ".join(t for t, _ in traps[m]["top_terms"][:3])) or "none")
        + "</div>"
        for m in ("regex", "presidio", "llm")
    )
    prec_rows = [
        [CAT_LABELS[c], *[pct(cat[m].get(c, {}).get("precision"), 1) for m in ("regex", "presidio", "llm")]]
        for c in CATEGORIES
    ]  # fmt: skip
    slice_rows = [
        [s["slice"], s["n"], *[pct(s[m], 1) for m in ("regex", "presidio", "llm")]] for s in res["slices"]
    ]  # fmt: skip
    details = _details(
        "Show details",
        "<h3 style='margin-top:10px'>Precision by type</h3>"
        + _table(["Type", "Regex", "Presidio", "Local LLM"], prec_rows)
        + "<h3 style='margin-top:16px'>Recall on the hard cases</h3>"
        + _table(["Case", "n", "Regex", "Presidio", "Local LLM"], slice_rows),
    )
    return f"""
<section id="blind-spots">
  <div class="num">05 · BLIND SPOTS</div>
  <h2>Where each method misses</h2>
  <p class="takeaway">Each method has a different blind spot, which the overall score hides.
  Read across a row to see who handles each identifier type.</p>
  <div class="card">
    <div class="card-head"><div><h3>Share of each identifier type removed</h3>
      <div class="card-sub">Hover a cell for counts.</div></div></div>
    <div id="heatmap"></div>
    {callouts}
    {details}
  </div>
  <div class="card">
    <div class="card-head"><div><h3>False alarms on medical terms</h3>
      <div class="card-sub">Terms like “Cushing syndrome”, “Foley catheter” or “Glasgow Coma Scale” contain a name but are not identifiers. Lower is better.</div></div></div>
    <div class="chart" id="trap-chart"></div>
    <div class="callouts">{top_terms}</div>
  </div>
</section>"""


def consistency_section(res: dict) -> str:
    c = res.get("llm_consistency")
    if not c:
        return ""
    k = c["gold_found_in_k_runs"]
    flaky = sum(v for kk, v in k.items() if kk not in ("0", str(c["runs"])))
    f = c["failures_by_run"]
    hard = sum(f[r][x] for r in f for x in ("timeout", "http_error", "invalid_json", "too_long"))
    calls = c["runs"] * c["n_notes"]
    t0 = res["overall"].get("llm_t0", {}).get("overlap", {}).get("f1")
    stats = [
        (pct(c["identical_notes"] / c["n_notes"]), "of notes came out identical in all 3 runs"),
        (f"{flaky:,}", "identifiers removed in some runs, left in others"),
        (f"{hard}", f"timeouts or broken JSON in {calls} calls"),
    ]
    if t0 is not None:
        stats.append((f"{100 * t0:.1f}", "F1 at temperature 0: no better"))
    tiles = "".join(
        f'<div class="stat"><div class="v">{v}</div><div class="k">{_e(t)}</div></div>'
        for v, t in stats
    )
    return f"""
<section id="consistency">
  <div class="num">06 · CONSISTENCY</div>
  <h2>Does the LLM give the same answer twice?</h2>
  <p class="takeaway">No. Each note was run three times with the model's default settings, and
  only {c["identical_notes"]} of {c["n_notes"]} came out the same every time. It never crashed,
  though: JSON-schema output kept every reply parseable.</p>
  <div class="stats-row">{tiles}</div>
  <div class="card">
    <div class="card-head"><div><h3>How many of the 3 runs removed each identifier</h3>
      <div class="card-sub">{sum(k.values()):,} identifiers. Hover a segment for counts.</div></div></div>
    <div id="cons-bar"></div>
  </div>
</section>"""


def side_effects_section(res: dict) -> str:
    integ = res.get("integrity")
    if not integ:
        return ""
    compare = ""
    if "llm_rewrite" in integ and "rewrite" in res:
        a, b = integ["llm"], integ["llm_rewrite"]
        L = res["overall"]["llm"]["overlap"]
        rows = [
            ("Identifiers removed", pct(L["recall"]), "~" + pct(res["rewrite"]["approx_recall"]), 0),
            ("Words inserted per note", f"{a['inserted_per_note']:.2f}", f"{b['inserted_per_note']:.2f}", 0),
            ("Every change traceable to a marked span", "Yes", "No", 0),
            ("Notes with a number changed", pct(a["numbers_changed"]), pct(b["numbers_changed"]), 1),
            ("Notes with medication text changed", pct(a["meds_changed"]), pct(b["meds_changed"]), 1),
            ("Time per note", _speed(res["runtime"]["llm"]["mean_s"]), _speed(res["rewrite"]["runtime_mean_s"]), None),
        ]  # fmt: skip
        trs = "".join(
            f"<tr><td>{_e(label)}</td><td{' class=better' if w == 0 else ''}>{_e(x)}</td>"
            f"<td{' class=better' if w == 1 else ''}>{_e(y)}</td></tr>"
            for label, x, y, w in rows
        )
        compare = f"""
  <div class="card">
    <div class="card-head"><div><h3>Two ways to use the LLM</h3>
      <div class="card-sub">Bold marks the better result. The rewrite changes fewer numbers mainly because it removes less.</div></div></div>
    <div class="scroll-x"><table class="compare"><thead><tr><th></th><th>LLM marks identifiers, code removes them</th><th>LLM rewrites the note</th></tr></thead>
    <tbody>{trs}</tbody></table></div>
  </div>"""
    full = _table(
        ["Method", "Any change", "Change (titles ignored)", "Numbers", "Medications", "Words dropped / note", "Words inserted / note"],  # fmt: skip
        [
            [LABELS[m], pct(v["changed"], 1), pct(v["substantive_changed"], 1), pct(v["numbers_changed"], 1),
             pct(v["meds_changed"], 1), f"{v['dropped_per_note']:.2f}", f"{v['inserted_per_note']:.2f}"]
            for m, v in integ.items()
        ],
    )  # fmt: skip
    return f"""
<section id="side-effects">
  <div class="num">07 · SIDE EFFECTS</div>
  <h2>What else gets changed</h2>
  <p class="takeaway">Every method removes some text that isn't an identifier, like “daily”, a dose
  or an age. Methods that mark spans only ever delete; asking the LLM to rewrite the note also
  inserts and rewords text, and leaks more identifiers.</p>
  <div class="card">
    <div class="card-head"><div><h3>Notes where non-identifier text changed</h3>
      <div class="card-sub">Compared with the original minus its true identifiers. Lost titles like “Dr.” are ignored.</div></div></div>
    <div class="chart" id="integ-chart"></div>
    {_details("Show details", full)}
  </div>
  {compare}
</section>"""


def examples_section(n_notes: int) -> str:
    return f"""
<section id="examples">
  <div class="num">08 · EXAMPLES</div>
  <h2>See it on a note</h2>
  <p class="takeaway">Pick any of the {n_notes} notes and compare the original with each
  method's output.</p>
  <div class="card"><div id="ex-viewer"></div></div>
</section>"""


def method_section(res: dict) -> str:
    model = OllamaConfig().model
    return f"""
<section id="method">
  <div class="num">09 · METHOD</div>
  <h2>How this was measured</h2>
  <div class="cols">
    <div class="card"><h3 style="margin-bottom:8px">Setup</h3><ul>
      <li>{res["n_notes"]} synthetic notes from 6 templates (discharge summaries, ED notes, phone messages…), with every identifier's position recorded as it was inserted.</li>
      <li>11 identifier types from the HIPAA Safe Harbor list, plus names that are ordinary words (Hope, Will, Rose), lowercase names, and medical terms that contain a name.</li>
      <li>Regex: hand-written patterns. Presidio: default settings plus a record-number recognizer. Local LLM: {_e(model)} in Ollama, returning identifier text that code then locates and removes.</li>
      <li>An identifier counts as removed if any of it was redacted (“any overlap”); the exact-boundary score is in the details.</li>
      <li>Everything ran on one laptop. No note text was sent to an external service.</li>
    </ul></div>
    <div class="card"><h3 style="margin-bottom:8px">Limitations</h3><ul>
      <li>Template notes are much cleaner than real ones; every method will do worse on real data.</li>
      <li>The regex was written by the same author as the templates, so its scores are a best case.</li>
      <li>One local model at 4-bit; bigger models or other prompts will differ.</li>
      <li>English and US formats only. Small types (ages over 89, SSNs) have few examples.</li>
      <li>The rewrite's score is approximate and probably generous.</li>
      <li>Covers 11 of 18 Safe Harbor identifier types. This is a benchmark, not a compliance tool.</li>
    </ul></div>
  </div>
</section>"""


# --------------------------------------------------------------------------- data + page


def build_data(notes: list[dict], res: dict) -> dict:
    """Everything the page's JavaScript needs, as plain JSON."""
    span_methods = [m for m in METHOD_ORDER if m in res["overall"]]
    has_rewrite = "rewrite" in res
    methods = [
        {"key": m, "label": LABELS[m], "spans": m != "llm_rewrite"}
        for m in METHOD_ORDER
        if m in span_methods or (m == "llm_rewrite" and has_rewrite)
    ]
    by_cat = {"overlap": dict(res["by_category"]), "exact": dict(res["by_category_exact"])}
    if has_rewrite:
        by_cat["overlap"]["llm_rewrite"] = {
            c: {"recall": v} for c, v in res["rewrite"]["by_category"].items()
        }

    runs = {m: load_runs(m) for m in span_methods}
    rewrites = {}
    if pred_path("llm_rewrite").exists():
        rewrites = {r["note_id"]: r["output"] for r in read_jsonl(pred_path("llm_rewrite"))}
    note_rows = [
        {
            "id": n["id"],
            "type": n["note_type"],
            "text": n["text"],
            "gold": [[s["start"], s["end"], s["category"]] for s in n["spans"]],
            "traps": [[t["start"], t["end"], t["text"]] for t in n["traps"]],
            "pred": {
                m: [
                    [[s["start"], s["end"], s["category"]] for s in rows[n["id"]]["spans"]]
                    for _, rows in sorted(runs[m].items())
                ]
                for m in span_methods
            },
            "rewrite": rewrites.get(n["id"]),
        }
        for n in notes
    ]
    cons = res.get("llm_consistency")
    return {
        "methods": methods,
        "categories": list(CATEGORIES),
        "catLabels": CAT_LABELS,
        "regexCats": REGEX_CATEGORIES,
        "nGold": res["gold_by_category"],
        "overall": res["overall"],
        "runs": res["runs"],
        "byCat": by_cat,
        "traps": {
            m: {"hit_rate": t["hit_rate"], "n_terms": t["n_terms"]} for m, t in res["traps"].items()
        },
        "integrity": res.get("integrity", {}),
        "consistency": (
            {"runs": cons["runs"], "found": cons["gold_found_in_k_runs"]} if cons else None
        ),
        "notes": note_rows,
        "defaultNote": pick_default_note(notes),
    }


def _json_for_script(data: dict) -> str:
    """JSON safe to embed in <script type="application/json">: NaN -> null, no "</"."""

    def clean(x):
        if isinstance(x, float) and x != x:
            return None
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, list | tuple):
            return [clean(v) for v in x]
        return x

    text = json.dumps(clean(data), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return text.replace("</", "<\\/")


NAV = [
    ("summary", "Summary"), ("input", "The input"), ("design", "How it works"), ("accuracy", "Accuracy"), ("blind-spots", "Blind spots"),
    ("consistency", "Consistency"), ("side-effects", "Side effects"), ("examples", "Examples"),
    ("method", "Method"),
]  # fmt: skip


def build() -> str:
    notes = load_notes()
    res = json.loads((ASSETS_DIR / "results.json").read_text())
    data = build_data(notes, res)
    css = (_ASSETS / "report.css").read_text()
    js = (_ASSETS / "report.js").read_text()
    model = OllamaConfig().model
    nav = "".join(f'<a href="#{i}">{t}</a>' for i, t in NAV)
    sections = "".join(
        [
            summary_section(res),
            input_section(notes),
            design_section(),
            accuracy_section(res),
            blind_spots_section(res),
            consistency_section(res),
            side_effects_section(res),
            examples_section(res["n_notes"]),
            method_section(res),
        ]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clinical De-identification Benchmark</title>
<meta name="description" content="Regex vs Presidio vs a local LLM for removing identifiers from synthetic clinical notes.">
<style>{css}</style></head>
<body>
<header class="hero"><div class="wrap">
  <div class="eyebrow">Local de-identification benchmark</div>
  <h1>Can a local LLM scrub patient identifiers from clinical notes?</h1>
  <p class="lede">Regex, Microsoft Presidio and a 7B model running in Ollama were each asked to
  find names, dates, addresses and eight other identifier types in synthetic clinical notes.
  Nothing left the laptop.</p>
  <div class="facts"><span><b>{res["n_notes"]}</b> notes</span><span><b>{res["n_gold"]:,}</b> identifiers</span>
  <span><b>11</b> identifier types</span><span><b>3</b> methods</span><span>model <b>{_e(model)}</b></span></div>
</div></header>
<nav class="toc" aria-label="Sections"><div class="wrap">{nav}</div></nav>
<main class="wrap">{sections}</main>
<footer><div class="wrap">Synthetic data only. A benchmark, not a compliance tool; nothing here
claims HIPAA compliance. Generated by <code>python -m deid.report</code>.</div></footer>
<div id="tip" role="tooltip"></div>
<script id="data" type="application/json">{_json_for_script(data)}</script>
<script>{js}</script>
</body></html>"""


def main() -> None:
    REPORT_PATH.write_text(build())
    print(f"wrote {REPORT_PATH} ({REPORT_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
