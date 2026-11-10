# Notes

Surprises and observations along the way. Raw material for the article.

## Regex

- **Strength scores look like dates.** "strength 4/5 in the left upper extremity" matches a month/day pattern. These were the only regex false positives: 43 of them.
- **Regex is perfect on its categories, and that's the trap.** 100% recall on phone, email, SSN, ZIP and dates, but the regexes were written by whoever wrote the templates. On real notes, the formats you didn't think of are what leak. On names, addresses, cities, hospitals and old ages it scores exactly 0. Overall recall is 43%.
- Unlabeled MRNs ("Re: pt Lyons, Timothy, 45 yo, 68874410") are invisible to a label-anchored rule: 33 of 200 MRNs.

## Presidio

- **"daily" is a DATE.** spaCy's DATE entity fires on "twice daily" and "3 months": 134 and 34 false positives in run 0.
- **"MD" is Maryland.** "Jane Smith, MD" → `MD` labeled LOCATION, 73 times. "RN" also gets labeled a location. "ED" and "SpO2" get labeled people.
- **Hospitals get through.** Presidio's default spaCy config ignores the `ORG` label, so "Springfield General Hospital" isn't an entity type it reports. Hospital recall is 15%, and most of that is accidental: "Medical Center" tagged as a location, "St. Luke's" as a person.
- **There is no ZIP recognizer.** Presidio has no US ZIP entity by default. The ZIPs it does catch (27%) are side effects: the SSN pattern firing, a LOCATION span running into the ZIP, a DATE. "Durham, NC 27077" redacts the city and leaves the ZIP.
- **Ages over 89 are caught by accident** (40%): spaCy tags "92-year-old" as a DATE. "Age: 92" on its own line is missed.
- **"+1 555 412 2768" isn't a phone number** to Presidio's default phone recognizer. Most of its phone misses are that format.
- **Eponyms:** "Cushing syndrome" → `Cushing` as a person, 14 of 14. "Foley catheter" → `Foley` as a person, 12 of 12. "Glasgow Coma Scale" → `Glasgow` as a city, 10 of 10. Guillain-Barre and Hashimoto are flagged too. Parkinson, Alzheimer and Down mostly survive. Overall, 32% of trap terms were redacted.
- **Common-word names are harder.** Recall is 80% on names like Hope, Will, Ivy, Chase, Faith and Dawn, against 97% on other names. "Ivy" alone was missed 5 times.
- **8-digit MRNs are dates.** spaCy labels "00123456" as DATE, outscoring the custom MRN recognizer. The number still gets redacted, under the wrong label.
- **Partial leaks:** 177 identifiers were found but not fully covered. "Alexander, Tracy" → only one half redacted. "53000 Wong Orchard" → only the street name.
- **The threshold sweep went the "wrong" way:** F1 was best at threshold 0 (return everything). Raising it dropped recall faster than it gained precision.
- **Speed:** about 15 ms/note after a few seconds of model load.

## Local LLM (qwen2.5:7b-instruct, 3 runs, default sampling)

- **About the same F1 as Presidio, roughly 260× slower.** Overlap F1 is 78.8 ± 0.6 for the LLM against 76.5 for Presidio, at about 5 s/note against 19 ms/note.
- **It wins where context matters.** Hospital recall is 82% (Presidio 15%), address 100% (51%), ZIP 87% (27%), age over 89 69% (40%). It finds unlabeled MRNs that regex can't.
- **It loses on names, and mostly for a dull reason.** Name recall is 68% against Presidio's 91%. 180 of 311 missed names in run 0 are short forms of a name the model *did* find. It returns "Alexander, Tracy" once, so the code never redacts "Ms. Alexander" further down. The prompt even says "list each text once", and the model takes that literally.
  - **Post-hoc fix, no new LLM calls:** also redact each word of every returned NAME wherever it appears. Name recall rises to 87–90% and overall F1 to 0.82–0.84 across the 3 runs, with *no* additional trap hits. It's in the article as "what one line of code fixes". The headline numbers are left unchanged because this fix was designed after looking at the test results.
- **Precision is good on names (98%) and poor on dates (53%).** It labels diagnoses and medications as DATE: "asthma", "Raynaud phenomenon", "atrial fibrillation", "amlodipine 5 mg daily". It also labels bare years ("2013") as DATE despite the prompt saying a year alone isn't an identifier. This is a 7B model under a JSON-schema constraint. The category enum may push it to put *something* in a category when it is unsure.
- **AGE_OVER_89 precision is 21%.** It labels ages like "45" and "51 yo F" as over-89 even though the prompt defines the category.
- **It isn't consistent.** At default sampling, only 17 of 200 notes (8%) produced the same span set in all 3 runs. The mean pairwise Jaccard is 0.67. 353 gold identifiers were missed in all 3 runs, and 446 were found in only 1 or 2 runs, so the same note can leak different identifiers each time you run it.
- **No call or JSON failures.** With structured outputs there were 0 invalid-JSON replies, 0 timeouts and 0 truncations in 600 calls. "Text not in note" happened 16–29 times per run: the model normalizes what it copies ("andrea, white" for "white, andrea", "11/9/2023" for a different format, "0.7").
- **Exact-match F1 (59.6) is far below overlap F1 (78.8).** The model includes "Dr." or ", MD" with names, and merges "Durham, NC 27077" into one ADDRESS item.
- **Largest prompt: 781 tokens.** num_ctx 8192 was never close. The point of setting it explicitly is that you don't have to check.
- **Temperature 0 changes nothing on average.** One greedy run scored F1 78.7 against 78.8 ± 0.6 at default sampling. Greedy decoding buys repeatability, not accuracy. Even at temperature 0 there was 1 invalid-JSON reply: generation hit `num_predict` and the JSON got cut off.

## Content integrity

- **Span methods only delete text; the rewrite also inserts.** Words inserted per note: regex 0.00, Presidio 0.06, LLM spans 0.01, LLM rewrite **3.04**.
- **But the span methods delete a lot.** Ignoring titles (Dr/Mr/Ms/MD/RN), non-identifier text changed in 88% of Presidio notes and 84% of LLM-span notes, against 75% for the rewrite. That's over-redaction: Presidio removes "daily", "3 months", "HR", "ED". The LLM spans remove dosing ("mg", "twice", "40") because the model labels "amlodipine 5 mg daily" as a DATE. **LLM spans changed medications in 30% of notes**; Presidio in 0%.
- **The rewrite looks safer on numbers (12% of notes) only because it redacts much less.** Its approximate recall is **61%**, against 80% for LLM spans. It leaves most dates (37% removed) and hospitals (36%) in place. A method that touches less text changes less text.
- **The rewrite's 61% is probably too high.** The leak check looks for an identifier's exact text, and the rewrite sometimes *reformats* an identifier rather than removing it: a DOB comes back as "19 Aug 1970", which the check doesn't see.
- **What the rewrite does to the text:** it writes tags without brackets ("PHONE", "NAME"), rewords labels ("Medical Record Number" → "MRN", "Department" → "DEPT"), invents a category ("insulin glargine 20 units" → "[MEDICATION]"), drops ages ("69-year-old" gone), and returned 7 of 200 notes mostly in capitals.
- **Speed:** the rewrite takes 5.5 s/note, the same as span extraction. It's no cheaper and leaks more.
- **Takeaway for the article:** if you use an LLM, have it point at spans and let code do the redacting. The damage is then limited to deletions you can measure, and every change traces back to a span. The rewrite leaks more, and it alters text in ways you can't trace back to any span.
- **Measurement gotcha (fixed):** at first the tokenizer kept "92-year-old" as one token, so a leaked age showed up as a changed word. Splitting on hyphens fixed it.
