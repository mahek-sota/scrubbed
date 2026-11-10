# Decisions

Non-obvious choices, with the reason for each. Newest at the bottom.

## Step 1: Scaffold

- **Python pinned to 3.11 (`>=3.11,<3.12`).** spaCy/Presidio wheels are reliable there; one version keeps runs reproducible.
- **`uv` + a local `.venv`, driven by `make`.** `make setup` is the single install path, so the README has one command to point at.
- **Ollama `num_ctx` set explicitly (default 8192, env `DEID_OLLAMA_NUM_CTX`).** Ollama's default context is small and it truncates over-long prompts silently, which would look like missed identifiers rather than an error. 8192 covers prompt + longest note + JSON output with headroom; step 6 will check prompt token counts against it.
- **Ollama URL must be localhost.** `OllamaConfig.from_env()` refuses non-local URLs, so a misconfigured env var can't send note text off the machine. Notes are synthetic, but the point of the project is the local-only workflow.
- **All config via `DEID_*` env vars with defaults in `src/deid/config.py`.** One place to look; no config file format to maintain.
- **spaCy model `en_core_web_lg`.** It is what Presidio uses by default, so the Presidio numbers reflect its out-of-the-box behaviour rather than a weaker small model.
- **Generated data is git-ignored.** It is reproducible from the fixed seed (`DEID_SEED`, default 1234); results in `article_assets/` are what get committed.
- **Ruff E501 (line length) is ignored in lint.** `ruff format` handles line length. What's left is long string literals (prompts, templates, regexes), and splitting those would make them harder to read.

## Step 2: Synthetic notes

- **Templates use placeholders, and `render` records offsets as it builds the string.** A placeholder's value is plain text, an `Ident` (identifier plus category), a `Trap`, or a list of those. Offsets are computed while the string is being built, never searched for afterwards, so gold labels can't drift. A test checks every span against the text.
- **Span boundaries:** titles and labels are not part of a span. In "Dr. Jane Smith", "Mr. Bell" and "MRN: 0012345", only the name or number is labeled. "Jane Smith, MD" labels only "Jane Smith".
- **Address split into three categories.** The street is `ADDRESS`, the city is `CITY`, the ZIP is `ZIP`. The state is not labeled: Safe Harbor allows states, and only smaller geographic units are identifiers.
- **AGE_OVER_89 labels only the number.** "92" in "92-year-old" or "Age: 92". Ages 89 and under appear unlabeled, so they test for false positives.
- **Years on their own are not identifiers.** Safe Harbor allows the year, so "diagnosed in 2015" is unlabeled. Month-year ("March 2024") and month-day without a year ("Mar 4", "3/4") are labeled DATE.
- **Hospital names are labeled `HOSPITAL` as a whole.** In "Springfield General Hospital", the city inside the name is not labeled separately. That keeps gold spans disjoint.
- **Real US city/state pairs, not Faker's cities.** Faker makes up names like "East Jessicaport". spaCy has never seen those, so using them would be unfair to Presidio. Street names, ZIPs (`zipcode_in_state`) and people are fake.
- **Messiness is built in at fixed rates.** About 30% of first names and 20% of last names are also common words (Hope, Will, Rose, Bell, Young...). Names in about 35% of phone messages and 10% of other notes are lowercased. Dates come in 7 formats plus month-year. MRNs appear labeled 4 different ways or bare. SSNs are mostly dashed, sometimes spaced, sometimes undelimited. Clinician names are never lowercased; only names in sloppy patient-facing text are.
- **Trap terms:** 12 eponymous diagnoses (Parkinson's disease, Down syndrome, Hodgkin lymphoma, ...) plus Foley catheter, Murphy's sign, Glasgow Coma Scale and Apgar score (in pediatric notes only). They are stored in a separate `traps` list. They are never gold identifiers, and the step 8 results count any prediction that overlaps one.
- **Age bands: 12% children, 63% adults, 25% over 89.** Over-89 patients are oversampled so AGE_OVER_89 has real support (37 spans). Children get pediatric conditions and medications.
- **Note types rotate (i mod 6)**, giving 33–34 notes per type rather than a random mix.
- **Determinism:** the same seed gives the same corpus with the same Faker version (40.53). Faker data can change between versions, so the README records the version.

## Step 3: Evaluation

- **Matching ignores the label.** The question is whether the identifier was removed; calling a phone number an MRN still redacts it. Label agreement is reported as its own metric.
- **Precision is computed over predictions and recall over gold spans.** One prediction covering "Springfield, NC 27701" finds both the CITY and the ZIP gold spans. Two predictions inside one name both count as correct.
- **Partial leaks are counted.** Under overlap matching, a name counts as found if any part of it was redacted. Redacting "John" alone still leaks "Smith". Gold spans that are overlapped but not fully covered are counted and listed.
- **Per-category recall uses the gold category; per-category precision uses the predicted category.** Predicted types with no counterpart in our categories are counted as `OTHER`.
- **A note with no predictions counts every identifier as missed.** Failed LLM calls therefore lower recall instead of being dropped from the average.

## Step 4: Regex

- **Labeled MRNs only.** A bare 7–8 digit rule would be a guess. The cost shows up as a separate result: recall on unlabeled MRNs.
- **ZIP only directly after a two-letter state code.** A bare `\d{5}` rule would hit lab values and MRN fragments.
- **Month/day ranges are restricted (1–12, 1–31).** This keeps blood pressures like 135/92 out. Motor strength scores like "4/5" still match, and that is left in deliberately as a real failure.
- **Overlapping matches are merged into their union** (`merge_overlapping`). Every method does this, so redaction never leaves half of an identifier.
- **Caveat: the regexes were written by the same person (and model) that wrote the templates.** Their near-perfect recall on pattern categories is an upper bound, not something to expect on real notes.

## Step 5: Presidio

- **Default `AnalyzerEngine` (spaCy `en_core_web_lg`) with all default recognizers, plus one custom MRN recognizer.** The MRN pattern is 7–8 digits at score 0.3, with context words (mrn, medical, record, ...) that raise the score.
- **Score threshold stays at Presidio's default of 0.** I swept 0 / 0.2 / 0.35 / 0.5 / 0.7. Overlap F1 was highest at 0 (0.765, falling to 0.640 at 0.7), so the default is also the best setting and nothing was tuned on the test notes.
- **Entity mapping lives in one table, `ENTITY_MAP` in `methods/presidio.py`.** PERSON→NAME, DATE_TIME→DATE, PHONE_NUMBER→PHONE, EMAIL_ADDRESS→EMAIL, US_SSN→SSN, MRN→MRN, LOCATION→CITY. Everything else (US_DRIVER_LICENSE, URL, NRP, UK_NHS, ...) maps to `OTHER` and still counts as a prediction, since a default pipeline would redact it.
- **ORGANIZATION is not enabled.** Presidio's default spaCy config ignores the `ORG` label. Leaving it off is the out-of-the-box behavior, and it is why Presidio misses most hospital names.
- **When the same span gets two labels, the higher-scoring one wins.** spaCy often labels 8-digit MRNs as DATE (0.85) above the MRN recognizer (0.65). Detection still counts; only label agreement suffers.

## Step 6: Local LLM

- **The model returns text and category, and code finds the offsets.** Every occurrence of the returned text is redacted. The search is case-sensitive first, so "Will" doesn't match the verb "will" unless the model wrote it lowercase. If that fails it retries case-insensitive, then with flexible whitespace. Word-boundary checks stop "Rose" matching inside "Rosemary".
- **JSON-schema structured output** (Ollama's `format` field) with a category enum. This is what a careful practitioner would use. Invalid JSON is still detected and counted.
- **Sampling is left at Ollama's defaults** (the model file sets none, so temperature 0.8). It's the out-of-the-box setting, just as Presidio keeps its default threshold, and it makes the 3-run consistency check meaningful. A fourth run at temperature 0 (`llm_t0`) shows what greedy decoding changes.
- **The prompt gives general rules but no dataset examples.** It defines the 11 categories and states general domain rules: eponym diseases and devices are not names, a year alone is not a date, ages 89 and under are not identifiers. That's a real advantage the LLM has over Presidio, which can't be told these things, and it's disclosed here. The prompt never names any of the trap terms.
- **Every failure is counted.** Timeout, HTTP error, prompt too long (refused before sending, never silently truncated), invalid JSON, output truncated (`done_reason == "length"`), text not found in the note, unknown category.
- **Pre-flight length check:** estimated tokens are characters ÷ 3, which overestimates for English. If that plus `num_predict` exceeds `num_ctx`, the call is refused. The largest real prompt was about 1k tokens against num_ctx 8192.
- **Responses are cached on disk** under `data/llm_cache/`. The key is model, options, prompt and run index, so every run is a real, separate sample. Each cached response keeps the runtime measured when it was first produced.

## Step 7: Content integrity

- **One comparison for every method.** The reference is the original note with gold identifiers replaced by tags. Both texts are tokenized into words and numbers, tags are dropped, and the two are diffed. A dropped token is lost content; an added token is either a leak (it belongs to a gold identifier) or inserted content. Whitespace and punctuation changes are ignored.
- **This puts "LLM finds spans, code redacts" and "LLM rewrites the note" on the same scale.** For the span methods, content can only change through over-redaction. The rewrite can change anything.
- **The rewrite has no offsets, so its recall is approximate:** a gold identifier counts as leaked if its exact text is still in the output. Leaks are counted per occurrence.
- **Known blind spot:** if the rewrite inserts a common word that also appears in a gold identifier ("of" from "University Hospital of Tulsa"), it is counted as a leak rather than inserted content. This makes the insertion count slightly low.

## Step 8–9: Results and report

- **LLM detection numbers are the mean of 3 runs, ± their standard deviation.** Examples and integrity use run 0.
- **Charts are static PNGs** (for Medium), using the first four slots of the validated categorical palette in a fixed method order. A 0% bar is labeled ("not attempted" for regex) so it can't be mistaken for missing data.
- **report.html is a single file.** Charts are inlined as base64, there's no JavaScript, and it has light and dark themes. Example notes are picked deterministically (a sloppy phone message, a referral letter with an eponym trap and a common-word name, an ED note) rather than chosen for drama.
- **Integrity tokens split on hyphens**, so a leaked "92-year-old" shows up as the leaked identifier "92" rather than as changed content. Found while reading the first results.
- **Titles reported separately.** An extra column ignores Dr/Mr/Ms/Mrs/MD/RN. Methods differ on whether "Dr." goes with the name, and that alone would otherwise dominate "notes changed". The integrity chart uses this column.
- **Post-hoc name expansion is reported but not adopted.** Redacting each word of every LLM-returned name everywhere lifts name recall from about 68% to about 88%. It's in NOTES and the README as a finding, but the headline LLM numbers don't use it, because it was designed after seeing the test results.

## Report v2: interactive

- **Charts are drawn in the browser from embedded data, not PNGs.** Hand-written SVG and plain JavaScript (`src/deid/report_assets/report.js`); no chart library, no CDN, no network. The page stays one offline file (~670 KB, mostly the 200 notes and their predictions for the explorer). The PNGs are still generated for the Medium post, which can't embed interactive charts.
- **Interaction follows the dataviz guidance.** Filters sit in one sticky row above the charts they scope (match mode, method toggles). Each bar shows a tooltip on hover and on keyboard focus, and its hover target is the whole row, not just the bar. Every chart has a "Show table" view. All data text goes into the page via `textContent`.
- **Colors:** method order regex, Presidio, LLM spans, LLM rewrite, LLM temp 0 → blue, orange, aqua, yellow, violet, validated in light and dark mode. Magenta was tried first for the temperature-0 run and failed next to aqua for deuteranopes in dark mode (ΔE 1.6).
- **The temperature-0 run is off by default** to keep the category chart readable (its numbers track the main LLM run); one click on its chip turns it on.
- **Note explorer:** any of the 200 notes, every enabled method side by side, an LLM run selector, and per-note counts (missed, partial, false positives, traps hit).

## Report v3: structured

- **One question per section, in reading order:** Summary → Accuracy → Blind spots → Consistency → Side effects → Examples → Method. Each opens with a one-sentence takeaway computed from `results.json`, shows one main chart, and puts its tables behind "Show details". A sticky nav highlights the section in view.
- **The per-category bar chart became a heatmap** (types × methods, value printed in each cell, single-hue blue ramp). 44 bars were unreadable; a grid of magnitudes is a heatmap's job. In dark mode the ramp flips so higher values are lighter.
- **The global filter bar was removed.** Each control now sits next to the one thing it changes: the match toggle on the accuracy chart, method tabs and a run selector in the examples.
- **Examples are a two-pane comparison** (original | one method chosen by tab) rather than five panels at once.
- **Method labels in the report are plain-language** ("Local LLM", "LLM rewrite"), and category names are spelled out ("Record numbers", "ZIP codes").
- **Section 02 "The input" shows one sample note before any results:** the note as the methods received it (identifiers highlighted, traps dotted), the correct redaction beside it, a count of what's in it, and its answer key behind "Show details". The sample is chosen automatically (has a trap, widest range of identifier types, shortest on ties), so it isn't hand-picked.
