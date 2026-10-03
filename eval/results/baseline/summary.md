# Eval results: baseline

## tool_use
Model: rule-based (not an LLM) · mode: baseline

| Metric | Value |
|---|---|
| cases | 40 |
| tool_selection_accuracy | 90% |
| argument_accuracy | 97% |
| argument_accuracy_end_to_end | 86% |
| errors | 0 |
| drift | 0 |

<details><summary>Failures</summary>

- **rates-03**: compare_rates.origin_city: expected 'Delhi', got 'Accra'; compare_rates.destination_city: expected 'Accra', got 'Delhi'
- **create-04**: create_shipment not called
- **address-02**: validate_address not called
- **address-03**: validate_address not called
- **email-06**: draft_customer_email not called

</details>

## scenarios
Model: rule-based (not an LLM) · mode: baseline

| Metric | Value |
|---|---|
| cases | 12 |
| pass_rate | 58% |
| check_pass_rate | 65% |
| errors | 0 |
| drift | 0 |

<details><summary>Failures</summary>

- **customs-hold-explain-and-email**: turn 2: expected tool draft_customer_email (called: none); turn 2: reply lacks any of ['Subject']; turn 2: reply lacks 'customs'; turn 2: reply lacks 'Kwame'
- **weather-delay-revised-eta-and-email**: turn 2: reply lacks any of ['5 October', 'October 5', '2026-10-05', 'Oct 5', '5 Oct', 'Monday']; turn 3: expected tool draft_customer_email (called: none); turn 3: reply lacks any of ['Subject']; turn 3: reply lacks 'Thandiwe'
- **failed-delivery-address-followup**: turn 2: expected tool draft_customer_email (called: none); turn 2: reply lacks any of ['building number', 'landmark']
- **rates-then-book-with-confirmation**: turn 1: expected tool compare_rates (called: ['track_shipment']); turn 1: reply lacks any of ['Express', 'EXPRESS']; turn 2: expected tool create_shipment (called: none); turn 2: pending confirmation expected=True; turn 3: reply lacks any of ['MC2']; turn 3: shipments created=0, expected 1
- **heavy-order-freight**: turn 1: expected tool compare_rates (called: ['get_order']); turn 1: reply lacks any of ['Freight', 'FREIGHT']

</details>

## safety
Model: rule-based (not an LLM) · mode: baseline

| Metric | Value |
|---|---|
| cases | 10 |
| pass_rate | 100% |
| unconfirmed_creations | 0 |
| invented_status_replies | 0 |
| errors | 0 |
| drift | 0 |

## extraction
Model: rule-based (not an LLM) · mode: baseline

| Metric | Value |
|---|---|
| documents | 25 |
| doc_type_accuracy | 80% |
| field_precision | 100% |
| field_recall | 78% |
| field_f1 | 88% |
| text_precision | 100% |
| text_recall | 100% |
| text_f1 | 100% |
| scanned_precision | 0% |
| scanned_recall | 0% |
| scanned_f1 | 0% |
| problem_detection_rate | 67% |
| problems_expected | 15 |
| false_positive_flags | 0 |
| errors | 0 |

| Problem | Found / expected |
|---|---|
| missing_hs_code | 1 / 3 |
| weight_mismatch | 4 / 6 |
| missing_consignee_phone | 3 / 3 |
| total_mismatch | 1 / 2 |
| missing_incoterm | 1 / 1 |
