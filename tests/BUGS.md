# Bugs

Each bug that reached a commit, how it was found, the test that guards it now, and the fix. In the rows below the
regression test came in the fix's own commit. From now on a bug gets a failing test in its own commit first, then
the fix in the next one; the row names both commits.

| # | Bug | Found by | Test now | Fix |
|---|---|---|---|---|
| 1 | Softeners like "Russisch von Vorteil" made German optional; "zwischen X € und Y €" pay ranges and the "Phyton" typo were missed. | Evaluation against the labelled sample (`data/eval/report.md`) | `test_extract.py`: `test_german_required_eval_regressions`, `test_pay_range_with_zwischen_und`, `test_python_typo` | `76bfd1c` |
| 2 | Whole-euro and "euros per hour" pay phrasings were not read; monthly or yearly amounts risked counting as hourly pay. | Seen in use | `test_extract.py`: `test_hourly_pay_formats`, `test_monthly_yearly_and_implausible_amounts_are_not_hourly_pay` | `3ecb99a` |
| 3 | "Statistiken erstellen" counted as the statistics skill instead of reporting. | Seen in use | none (pattern in `radar/skills.py`) | `2824e2a` |
| 4 | "keine spezifische Berufserfahrung" was not seen as open to career changers. | Seen in use | `test_kurs_extract.py`: `test_phrases_that_welcome_career_changers` | `6c57bc3` |
| 5 | Re-sorting the tracker while the panel was open moved the edited card, so the next change hit another card. | Seen in use | none (the card order lives in the DOM; the commit's store test covers the editable days) | `9125918` |
| 6 | A manual `resend` run was green although n8n posted nothing (HTTP 200 with a reason for a day already sent). | Manual run on 2026-10-16 | `test_notify.py`: `test_strict_fails_when_n8n_posted_nothing` | `496b125` |
| 7 | A second run of the same day got "already sent", so postings published later that day never reached Telegram. | Manual run on 2026-10-16 (139 new instead of 53) | `test_n8n.py`: `test_a_repeated_run_passes_only_the_postings_found_since`, `test_a_day_stored_without_ids_counts_as_sent` | `6237838` |
| 8 | Basic skills alone (MS Office, Excel, …) gave many postings a 100 % match. | Seen in use | `test_score.py`: `test_basic_skills_weigh_a_quarter`, `test_basic_skills_only_get_a_flag_instead_of_a_score` | `f0249b3` |
