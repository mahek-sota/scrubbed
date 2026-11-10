import json
import re

import pytest

from deid.report import _json_for_script, pct


def test_json_for_script_is_safe_and_strict():
    text = _json_for_script({"a": float("nan"), "b": "</script><b>", "c": [1.5, float("nan")]})
    assert "</" not in text
    assert json.loads(text) == {"a": None, "b": "</script><b>", "c": [1.5, None]}


def test_pct():
    assert pct(0.789) == "79%" and pct(0.789, 1) == "78.9%"
    assert pct(None) == "–" and pct(float("nan")) == "–"


@pytest.fixture(scope="module")
def page():
    from deid.config import ASSETS_DIR
    from deid.report import build

    if not (ASSETS_DIR / "results.json").exists():
        pytest.skip("run `make results` first")
    return build()


def test_page_structure(page):
    assert page.startswith("<!doctype html>")
    ids = re.findall(r'<section id="([^"]+)"', page)
    assert ids == [
        "summary",
        "input",
        "accuracy",
        "blind-spots",
        "consistency",
        "side-effects",
        "examples",
        "method",
    ]
    nav = re.findall(r'<a href="#([^"]+)"', page)
    assert nav == ids  # every section is in the nav, in order
    assert page.count('<p class="takeaway">') == 7  # one headline per results section


def test_page_is_self_contained(page):
    assert "<img" not in page  # charts are drawn from data
    # No external resources or network calls (the SVG namespace URI is not one).
    assert not re.search(r'(src|href)="https?://', page)
    assert "fetch(" not in page and "XMLHttpRequest" not in page
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)
    payload = json.loads(data.group(1))
    assert len(payload["notes"]) == 200 and payload["methods"][0]["key"] == "regex"


def test_never_claims_compliance(page):
    text = re.sub(r"<[^>]+>", " ", page.split('<script id="data"')[0]).lower()
    assert "hipaa compliant" not in text.replace("claims hipaa compliance", "")
    assert "not a compliance tool" in text


def test_ids_are_unique(page):
    markup = re.sub(r"<script>.*?</script>", "", page, flags=re.S)  # ignore JS source
    ids = re.findall(r'\sid="([^"]+)"', markup)
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


def test_input_example_is_short_and_varied():
    from deid.generate import generate
    from deid.report import pick_input_example

    notes = generate(n=30, seed=3)
    pick = pick_input_example(notes)
    assert pick["traps"]
    best = max(len({s["category"] for s in n["spans"]}) for n in notes if n["traps"])
    assert len({s["category"] for s in pick["spans"]}) == best


def test_input_section_shows_note_and_answer(page):
    sec = page.split('<section id="input">')[1].split("</section>")[0]
    assert "Input note" in sec and "Correct output" in sec
    assert 'class="gold"' in sec and '<span class="tag">[NAME]</span>' in sec
