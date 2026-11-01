# Bugs

Each bug that reached a commit, how it was found, the test that guards it now, and the fix. In rows 1–8 the
regression test came in the fix's own commit. From row 9 on, a bug gets a failing test in its own commit first
(named in "Test now"), then the fix in the next one; rows 11 and 12 were caught by the same test commit.

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
| 9 | Typing a tracker note and then picking a status: leaving the note redrew the list, which replaced the select being picked (an exception in Chrome's console). | Browser tests (`tests/e2e/`) | `test_tracker.py`: `test_typing_a_note_then_picking_a_status_keeps_both` (`b5443ae`) | `cfe59b7` |
| 10 | A change in another tab redrew the tracker and ended the note being typed: the next keystrokes were lost. | Browser tests | `test_tracker.py`: `test_typing_a_note_survives_a_change_in_another_tab` (`f331143`) | `df3f0be` |
| 11 | At 375 px the job count in the filter row was cut off. | Browser tests | `test_phone.py`: `test_the_page_fits_375_px` (`7b18d9c`) | `82ff40e` |
| 12 | On phones, once a posting was tracked, the count on the tracker button made the header 6 px too wide and the page scrolled sideways. | Browser tests | `test_phone.py`: `test_the_filter_sheet_and_the_tracker_fit_375_px` (`7b18d9c`) | `31ae9b4` |
| 13 | At 375 px the yearly pay histogram in Jobs mode ran past the screen (18 axis labels that could not shrink). | Browser tests | `test_phone.py`: `test_the_page_fits_375_px[?mode=jobs]` (`7c7bc19`) | `d785e4e` |
