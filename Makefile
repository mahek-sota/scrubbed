PY := .venv/bin/python
SPACY_MODEL := en_core_web_lg

.PHONY: setup test lint format data regex presidio llm llm-t0 rewrite results report all clean

setup:  ## Create a Python 3.11 venv, install the package + dev tools, download the spaCy model
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) -e ".[dev]"
	uv pip install --python $(PY) pip
	$(PY) -m spacy download $(SPACY_MODEL)

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

format:
	$(PY) -m ruff check --fix src tests
	$(PY) -m ruff format src tests

data:       ## 200 synthetic notes with gold spans -> data/notes.jsonl
	$(PY) -m deid.generate

regex:
	$(PY) -m deid.run regex

presidio:
	$(PY) -m deid.run presidio

llm:        ## 3 runs per note at default sampling (~50 min; responses cached in data/llm_cache)
	$(PY) -m deid.run llm --runs 3

llm-t0:     ## 1 run at temperature 0
	$(PY) -m deid.run llm_t0 --runs 1

rewrite:    ## LLM rewrites each note with identifiers redacted (~20 min)
	$(PY) -m deid.integrity

results:    ## tables + charts -> article_assets/
	$(PY) -m deid.results

report: results  ## article_assets/report.html
	$(PY) -m deid.report

all: data regex presidio llm llm-t0 rewrite report

clean:
	rm -rf .pytest_cache .ruff_cache
	find data -type f ! -name .gitkeep -delete
