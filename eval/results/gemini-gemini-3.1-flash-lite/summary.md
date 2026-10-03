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
