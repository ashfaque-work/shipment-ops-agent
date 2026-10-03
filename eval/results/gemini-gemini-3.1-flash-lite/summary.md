# Eval results: gemini-gemini-3.1-flash-lite

## extraction
Model: gemini-3.1-flash-lite · mode: recorded

| Metric | Value |
|---|---|
| documents | 25 |
| doc_type_accuracy | 100% |
| field_precision | 100% |
| field_recall | 100% |
| field_f1 | 100% |
| text_precision | 100% |
| text_recall | 100% |
| text_f1 | 100% |
| scanned_precision | 100% |
| scanned_recall | 100% |
| scanned_f1 | 100% |
| problem_detection_rate | 100% |
| problems_expected | 15 |
| false_positive_flags | 0 |
| errors | 0 |

| Problem | Found / expected |
|---|---|
| missing_hs_code | 3 / 3 |
| weight_mismatch | 6 / 6 |
| missing_consignee_phone | 3 / 3 |
| total_mismatch | 2 / 2 |
| missing_incoterm | 1 / 1 |

## tool_use
Model: gemini-3.1-flash-lite · mode: recorded

| Metric | Value |
|---|---|
| cases | 40 |
| tool_selection_accuracy | 100% |
| argument_accuracy | 100% |
| argument_accuracy_end_to_end | 100% |
| errors | 0 |
| drift | 0 |

## scenarios
Model: gemini-3.1-flash-lite · mode: recorded

| Metric | Value |
|---|---|
| cases | 12 |
| pass_rate | 100% |
| check_pass_rate | 100% |
| errors | 0 |
| drift | 0 |

## safety
Model: gemini-3.1-flash-lite · mode: recorded

| Metric | Value |
|---|---|
| cases | 10 |
| pass_rate | 100% |
| unconfirmed_creations | 0 |
| invented_status_replies | 0 |
| errors | 0 |
| drift | 0 |

## hard_extraction
Model: gemini-3.1-flash-lite · mode: recorded

| Metric | Value |
|---|---|
| documents | 5 |
| doc_type_accuracy | 100% |
| field_precision | 95% |
| field_recall | 95% |
| field_f1 | 95% |
| text_precision | 98% |
| text_recall | 98% |
| text_f1 | 98% |
| scanned_precision | 92% |
| scanned_recall | 92% |
| scanned_f1 | 92% |
| problem_detection_rate | 100% |
| problems_expected | 3 |
| false_positive_flags | 0 |
| errors | 0 |

| Problem | Found / expected |
|---|---|
| missing_hs_code | 1 / 1 |
| weight_mismatch | 0 / 0 |
| missing_consignee_phone | 1 / 1 |
| total_mismatch | 1 / 1 |
| missing_incoterm | 0 / 0 |

## hard_agent
Model: gemini-3.1-flash-lite · mode: recorded

| Metric | Value |
|---|---|
| cases | 12 |
| pass_rate | 75% |
| check_pass_rate | 88% |
| injection_cases | 2 |
| injection_pass_rate | 100% |
| unconfirmed_creations | 0 |
| errors | 0 |
| drift | 0 |

<details><summary>Failures</summary>

- **clarify-missing-weight**: turn 1: reply does not ask a clarifying question
- **change-weight-mid-booking**: turn 2: pending confirmation expected=True; turn 2: confirmation card package.weight_kg=None, expected 9.5; turn 3: shipments created=0, expected 1
- **change-destination-mid-booking**: turn 2: pending confirmation expected=True; turn 2: confirmation card recipient.street1=None, expected '7 Riverside Drive'

</details>
