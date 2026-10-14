# Skill extraction: Claude Haiku 5.5 vs. the rules

Model `claude-haiku-5-5`, adaptive thinking at effort `low` (the default; earlier runs differ, see below), structured JSON output, cached system prompt (6,357 characters: instructions and the 184 skills). Labels: `labels.csv` (14 skills) and `labels_extra.csv` (Word, Outlook, MS Teams), see `LABELS.md`.

Answers: 100 of 100 postings; 13 had no task or requirement section and were not sent (the rules' answer counts for them).

Versions: **v1** is the first run (prompt written before looking at any answer). After a smoke test on 3 postings and v1's disagreements, **v2** puts the term before the id in the schema (the id is chosen with the term already written), counts skill areas from the tasks (reports/KPIs, creditor work, social media content) and products of listed tools (SAP SuccessFactors). The **id check** then keeps an id only when the term fits it (the rules' pattern or a word of the skill's label), else moves it to the listed skill that fits or to "other". v2 was tuned on these same postings, so its scores are optimistic, like the rules' (tuned in pass 3 of `LABELS.md`; their untuned baseline is in `report_baseline.md`). **v3** also sends the untitled intro (v1/v2 lost requirements that unstructured postings put there); **v3-thinking-low** is v3 with adaptive thinking at effort `low` instead of thinking off. **v4** sends only the task and requirement sections (not the intro, benefits or company text); postings without them are not sent and keep the rules' answer. **v5** shortens the prompt (skill list without groups, shorter instructions, terms of at most 5 words) and has no id list in the schema; **v5-enum** puts the id list back (the model then thinks less). Runs without a suffix after v3 use thinking off, `-thinking-low` and v5 use effort `low`.

## Input

Only the task and requirement sections (plus single lines about pay or hours) go to the model. They were found in 87 of 100 postings; the others go to the rules only. Sent: 50% of all posting text (1,796 characters per sent posting, 3,151 for a whole posting).

## Skills, random 70 (70 postings)

| Skill | In labels | Rules P / R / F1 | LLM (v5-enum) + id check P / R / F1 | Rules FP / FN | LLM FP / FN |
|---|---|---|---|---|---|
| MS Office | 28 | 97% / 100% / 98% | 97% / 100% / 98% | 1 / 0 | 1 / 0 |
| Excel | 8 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| PowerPoint | 5 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| SAP (any) | 7 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| SQL | 2 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Python | 5 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Java | 0 | – / – / – | – / – / – | 0 / 0 | 0 / 0 |
| JavaScript | 0 | – / – / – | – / – / – | 0 / 0 | 0 / 0 |
| Power BI | 1 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Git | 1 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Controlling / Reporting | 4 | 100% / 75% / 86% | 100% / 75% / 86% | 0 / 1 | 0 / 1 |
| Accounting / Buchhaltung | 2 | 100% / 50% / 67% | 100% / 50% / 67% | 0 / 1 | 0 / 1 |
| Social media | 1 | 100% / 100% / 100% | 50% / 100% / 67% | 0 / 0 | 1 / 0 |
| Driving licence (B) | 5 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Word | 1 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Outlook | 0 | – / – / – | – / – / – | 0 / 0 | 0 / 0 |
| MS Teams | 0 | – / – / – | – / – / – | 0 / 0 | 0 / 0 |

| Overall | Skills | P / R / F1 | FP / FN |
|---|---|---|---|
| Rules | 14 skills (as in the README) | 99% / 97% / 98% | 1 / 2 |
| Rules | all 17 | 99% / 97% / 98% | 1 / 2 |
| LLM (v5-enum) + id check | 14 skills (as in the README) | 97% / 97% / 97% | 2 / 2 |
| LLM (v5-enum) + id check | all 17 | 97% / 97% / 97% | 2 / 2 |
| LLM (v5-enum), ids as returned | 14 skills (as in the README) | 93% / 97% / 95% | 5 / 2 |
| LLM (v5-enum), ids as returned | all 17 | 93% / 97% / 95% | 5 / 2 |
| LLM v1 | 14 skills (as in the README) | 89% / 91% / 90% | 8 / 6 |
| LLM v1 | all 17 | 89% / 91% / 90% | 8 / 6 |
| LLM v2 | 14 skills (as in the README) | 97% / 90% / 93% | 2 / 7 |
| LLM v2 | all 17 | 97% / 90% / 93% | 2 / 7 |
| LLM v3 | 14 skills (as in the README) | 95% / 88% / 92% | 3 / 8 |
| LLM v3 | all 17 | 95% / 89% / 92% | 3 / 8 |
| LLM v3-thinking-low | 14 skills (as in the README) | 97% / 96% / 96% | 2 / 3 |
| LLM v3-thinking-low | all 17 | 97% / 96% / 96% | 2 / 3 |
| LLM v4 | 14 skills (as in the README) | 96% / 97% / 96% | 3 / 2 |
| LLM v4 | all 17 | 96% / 97% / 96% | 3 / 2 |
| LLM v4-thinking-low | 14 skills (as in the README) | 97% / 97% / 97% | 2 / 2 |
| LLM v4-thinking-low | all 17 | 97% / 97% / 97% | 2 / 2 |
| LLM v5 | 14 skills (as in the README) | 97% / 97% / 97% | 2 / 2 |
| LLM v5 | all 17 | 97% / 97% / 97% | 2 / 2 |

## Skills, all 100 (100 postings)

| Skill | In labels | Rules P / R / F1 | LLM (v5-enum) + id check P / R / F1 | Rules FP / FN | LLM FP / FN |
|---|---|---|---|---|---|
| MS Office | 39 | 98% / 100% / 99% | 97% / 97% / 97% | 1 / 0 | 1 / 1 |
| Excel | 15 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| PowerPoint | 8 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| SAP (any) | 11 | 79% / 100% / 88% | 92% / 100% / 96% | 3 / 0 | 1 / 0 |
| SQL | 2 | 67% / 100% / 80% | 100% / 100% / 100% | 1 / 0 | 0 / 0 |
| Python | 10 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Java | 4 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| JavaScript | 2 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Power BI | 2 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Git | 3 | 75% / 100% / 86% | 100% / 100% / 100% | 1 / 0 | 0 / 0 |
| Controlling / Reporting | 5 | 80% / 80% / 80% | 67% / 80% / 73% | 1 / 1 | 2 / 1 |
| Accounting / Buchhaltung | 5 | 100% / 80% / 89% | 100% / 60% / 75% | 0 / 1 | 0 / 2 |
| Social media | 3 | 100% / 33% / 50% | 67% / 67% / 67% | 0 / 2 | 1 / 1 |
| Driving licence (B) | 9 | 100% / 89% / 94% | 100% / 89% / 94% | 0 / 1 | 0 / 1 |
| Word | 4 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| Outlook | 2 | 100% / 100% / 100% | 100% / 100% / 100% | 0 / 0 | 0 / 0 |
| MS Teams | 0 | – / – / – | – / – / – | 0 / 0 | 0 / 0 |

| Overall | Skills | P / R / F1 | FP / FN |
|---|---|---|---|
| Rules | 14 skills (as in the README) | 94% / 96% / 95% | 7 / 5 |
| Rules | all 17 | 94% / 96% / 95% | 7 / 5 |
| LLM (v5-enum) + id check | 14 skills (as in the README) | 96% / 95% / 95% | 5 / 6 |
| LLM (v5-enum) + id check | all 17 | 96% / 95% / 96% | 5 / 6 |
| LLM (v5-enum), ids as returned | 14 skills (as in the README) | 89% / 96% / 92% | 14 / 5 |
| LLM (v5-enum), ids as returned | all 17 | 89% / 96% / 93% | 14 / 5 |
| LLM v1 | 14 skills (as in the README) | 88% / 90% / 89% | 14 / 12 |
| LLM v1 | all 17 | 89% / 90% / 90% | 14 / 12 |
| LLM v2 | 14 skills (as in the README) | 96% / 88% / 92% | 4 / 14 |
| LLM v2 | all 17 | 96% / 89% / 92% | 4 / 14 |
| LLM v3 | 14 skills (as in the README) | 96% / 92% / 94% | 5 / 9 |
| LLM v3 | all 17 | 96% / 93% / 94% | 5 / 9 |
| LLM v3-thinking-low | 14 skills (as in the README) | 96% / 96% / 96% | 5 / 5 |
| LLM v3-thinking-low | all 17 | 96% / 96% / 96% | 5 / 5 |
| LLM v4 | 14 skills (as in the README) | 93% / 93% / 93% | 8 / 8 |
| LLM v4 | all 17 | 94% / 94% / 94% | 8 / 8 |
| LLM v4-thinking-low | 14 skills (as in the README) | 96% / 95% / 95% | 5 / 6 |
| LLM v4-thinking-low | all 17 | 96% / 95% / 96% | 5 / 6 |
| LLM v5 | 14 skills (as in the README) | 94% / 94% / 94% | 7 / 7 |
| LLM v5 | all 17 | 94% / 94% / 94% | 7 / 7 |

## German requirement (random 70)

| Check | Rules | LLM |
|---|---|---|
| Same category as the label (required / plus / none / implicit) | 99% | 96% |
| Needs German: yes / no | 100% | 100% |
| Shown as “open without German” and really are (all strata) | 15/15 | 15/17 |

German level found by the LLM (all 100): unknown 59, fluent 21, c1_plus 16, native 2, basic 1, none 1

## Hourly pay and hours (all 100)

| Check | Rules | LLM |
|---|---|---|
| Labelled hourly rate found | 19/19 | 19/19 |
| … and the amount is right | 19/19 | 19/19 |
| A rate where the label has none | 0 | 0 |
| Hours per week found (no labels) | 31 | 31 |
| Same hours when both found them | 27/28 | |

## What else the LLM returns

- 386 skills in 100 postings: must have 287, nice to have 99; 126 of them not on our list ("other").
- Most frequent "other" terms: ai tools (3), kassiervorgänge mit software (2), kundenberatung (2), angebote erstellen (2), akustische messtechnik (1), kenntnisse in der akustik (1), hr-systeme (1), abrechnung mit krankenkassen (1), ui5 (1), presentation tools (1), kassiervorgänge und zahlungen (1), kundenkontakt (1), verkauf (1), foto (1), capcut (1), low-code cloudtechnologien (1), skripte (1), datenaufbereitung, analyse (1), artificial intelligence (1), change management (1), computerkasse (1), automatisierungstechnik (1), softwareapplikationen (1), mess- und steuerungstechnik (1), inventuren (1)

## Disagreements: MS Office, Excel, Word, Outlook, MS Teams

| Posting | Skill | Label | Rules | LLM |
|---|---|---|---|---|
| 12811-2316142-S | MS Office | – | yes | yes |
| 10001-1001258244-S | MS Office | yes | yes | – |

## Answers

- Stop reasons: {'end_turn': 87, None: 13}; schema problems after the API's own check: 59 ({"genai -> other for 'AI tools'": 3, "sales_skills -> other for 'Angebote erstellen'": 2, "powerpoint -> other for 'presentation tools'": 1, "customer_service -> other for 'Kundenkontakt'": 1, "sales_skills -> other for 'Verkauf'": 1})
- Errors: 0

## Tokens and cost

Prices for `claude-haiku-5-5` (US$ per million tokens): input 0.1, output 0.5, cache read 0.01, cache write 0.125 (5 min) / 0.2 (1 h); the Batch API halves all of them.

**Messages API (measured)** (100 postings): input 67,723, cache read 440,148, cache write 5,118, output 35,868 tokens → $0.0297; per posting 677 input + 4,401 cached + 358 output tokens.

- Per 1,000 postings: **$0.29** (one cache write per run: $0.0006)
- 500 new postings a month (daily runs): **$0.16** a month
- 5,000 new postings a month (daily runs): **$1.47** a month
- 50,000 new postings a month (daily runs): **$14.57** a month

**Batch API, same tokens at batch prices (estimate)** (100 postings): input 67,723, cache read 440,148, cache write 5,118, output 35,868 tokens → $0.0149; per posting 677 input + 4,401 cached + 358 output tokens.

- Per 1,000 postings: **$0.15** (one cache write per run: $0.0003)
- 500 new postings a month (daily runs): **$0.08** a month
- 5,000 new postings a month (daily runs): **$0.74** a month
- 50,000 new postings a month (daily runs): **$7.29** a month

