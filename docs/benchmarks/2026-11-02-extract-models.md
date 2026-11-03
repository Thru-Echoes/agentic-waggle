# Extract-stage model benchmark — 2026-11-02

Ran 6 candidate models against 10 ground-truth cases across 3 stages.
Started at 2026-11-02T21:02:45.585750+00:00.
Costs are derived from the recorded token counts and the price table in `scripts/benchmark_models.py` at the time the report was written.


## Stage: intent

| model | accuracy | evidence-grounded | avg latency (ms) | total cost | total tokens |
| --- | --- | --- | --- | --- | --- |
| `gpt-5.4-mini` | 100% | 0% | 1145 | $0.0011 | 4621 |
| `gpt-5.4-nano` | 100% | 30% | 1088 | $0.0005 | 4607 |
| `gpt-5.6-luna` | 100% | 80% | 1532 | $0.0008 | 4606 |
| `gpt-6-luna` | 100% | 70% | 2105 | $0.0010 | 4989 |
| `gpt-6.1-terra` | 100% | 100% | 2284 | $0.0086 | 4730 |
| `gpt-6.1-sol` | 100% | 100% | 2402 | $0.0158 | 4593 |

## Stage: customer

| model | accuracy | evidence-grounded | avg latency (ms) | total cost | total tokens |
| --- | --- | --- | --- | --- | --- |
| `gpt-5.4-mini` | 100% | 20% | 979 | $0.0010 | 4757 |
| `gpt-5.4-nano` | 80% | 30% | 1015 | $0.0005 | 4715 |
| `gpt-5.6-luna` | 100% | 89% | 2053 | $0.0009 | 5083 |
| `gpt-6-luna` | 100% | 100% | 2000 | $0.0009 | 5161 |
| `gpt-6.1-terra` | 100% | 100% | 2006 | $0.0080 | 4913 |
| `gpt-6.1-sol` | 100% | 100% | 2175 | $0.0133 | 4634 |

## Stage: line_items

| model | accuracy | evidence-grounded | avg latency (ms) | total cost | total tokens |
| --- | --- | --- | --- | --- | --- |
| `gpt-5.4-mini` | 100% | 10% | 1161 | $0.0017 | 8213 |
| `gpt-5.4-nano` | 100% | 90% | 1274 | $0.0009 | 8208 |
| `gpt-5.6-luna` | 100% | 71% | 2128 | $0.0013 | 8333 |
| `gpt-6-luna` | 100% | 71% | 2323 | $0.0015 | 8741 |
| `gpt-6.1-terra` | 100% | 100% | 2586 | $0.0128 | 8259 |
| `gpt-6.1-sol` | 100% | 100% | 2201 | $0.0237 | 8078 |

## Per-model totals

| model | correct/scored | overall accuracy | avg latency (ms) | total cost |
| --- | --- | --- | --- | --- |
| `gpt-5.4-mini` | 25/25 | 100% | 1095 | $0.0038 |
| `gpt-5.4-nano` | 24/25 | 96% | 1126 | $0.0019 |
| `gpt-5.6-luna` | 25/25 | 100% | 1904 | $0.0030 |
| `gpt-6-luna` | 25/25 | 100% | 2143 | $0.0034 |
| `gpt-6.1-terra` | 25/25 | 100% | 2292 | $0.0294 |
| `gpt-6.1-sol` | 25/25 | 100% | 2260 | $0.0528 |

## Recommended per-stage assignment

Ranked by accuracy, then evidence grounding (the share of quoted evidence that appears verbatim in the email), then total cost, then latency. Grounding ranks above cost because the UI shows the quotes to the reviewer: an accurate answer with fabricated quotes fails review. Picks are per stage and mechanical; the shipped defaults in `app/config.py` keep one model across the three extraction stages unless the full-corpus eval, not this sample, confirms a split.

- **intent** → `gpt-6.1-terra` (accuracy 100%, evidence-grounded 100%, avg 2284ms, $0.0086 for 10 calls)
- **customer** → `gpt-6-luna` (accuracy 100%, evidence-grounded 100%, avg 2000ms, $0.0009 for 10 calls)
- **line_items** → `gpt-6.1-terra` (accuracy 100%, evidence-grounded 100%, avg 2586ms, $0.0128 for 10 calls)
