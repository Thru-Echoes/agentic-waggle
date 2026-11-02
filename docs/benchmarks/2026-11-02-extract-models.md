# Extract-stage model benchmark — 2026-11-02

Ran 6 candidate models against 10 ground-truth cases across 3 stages.
Started at 2026-11-02T21:02:45.585750+00:00.


## Stage: intent

| model | accuracy | evidence-grounded | avg latency (ms) | total cost | total tokens |
| --- | --- | --- | --- | --- | --- |
| `gpt-5.4-mini` | 100% | 0% | 1145 | $0.0011 | 4621 |
| `gpt-5.4-nano` | 100% | 30% | 1088 | $0.0005 | 4607 |
| `gpt-5.6-luna` | 100% | 80% | 1532 | $0.0000 | 4606 |
| `gpt-6-luna` | 100% | 70% | 2105 | $0.0000 | 4989 |
| `gpt-6.1-terra` | 100% | 100% | 2284 | $0.0000 | 4730 |
| `gpt-6.1-sol` | 100% | 100% | 2402 | $0.0000 | 4593 |

## Stage: customer

| model | accuracy | evidence-grounded | avg latency (ms) | total cost | total tokens |
| --- | --- | --- | --- | --- | --- |
| `gpt-5.4-mini` | 100% | 20% | 979 | $0.0010 | 4757 |
| `gpt-5.4-nano` | 80% | 30% | 1015 | $0.0005 | 4715 |
| `gpt-5.6-luna` | 100% | 89% | 2053 | $0.0000 | 5083 |
| `gpt-6-luna` | 100% | 100% | 2000 | $0.0000 | 5161 |
| `gpt-6.1-terra` | 100% | 100% | 2006 | $0.0000 | 4913 |
| `gpt-6.1-sol` | 100% | 100% | 2175 | $0.0000 | 4634 |

## Stage: line_items

| model | accuracy | evidence-grounded | avg latency (ms) | total cost | total tokens |
| --- | --- | --- | --- | --- | --- |
| `gpt-5.4-mini` | 100% | 10% | 1161 | $0.0017 | 8213 |
| `gpt-5.4-nano` | 100% | 90% | 1274 | $0.0009 | 8208 |
| `gpt-5.6-luna` | 100% | 71% | 2128 | $0.0000 | 8333 |
| `gpt-6-luna` | 100% | 71% | 2323 | $0.0000 | 8741 |
| `gpt-6.1-terra` | 100% | 100% | 2586 | $0.0000 | 8259 |
| `gpt-6.1-sol` | 100% | 100% | 2201 | $0.0000 | 8078 |

## Per-model totals

| model | correct/scored | overall accuracy | avg latency (ms) | total cost |
| --- | --- | --- | --- | --- |
| `gpt-5.4-mini` | 25/25 | 100% | 1095 | $0.0038 |
| `gpt-5.4-nano` | 24/25 | 96% | 1126 | $0.0019 |
| `gpt-5.6-luna` | 25/25 | 100% | 1904 | $0.0000 |
| `gpt-6-luna` | 25/25 | 100% | 2143 | $0.0000 |
| `gpt-6.1-terra` | 25/25 | 100% | 2292 | $0.0000 |
| `gpt-6.1-sol` | 25/25 | 100% | 2260 | $0.0000 |

## Recommended per-stage assignment

- **intent** → `gpt-5.4-nano` (accuracy 100%, avg 1088ms, $0.0005 for 10 calls)
- **customer** → `gpt-5.4-mini` (accuracy 100%, avg 979ms, $0.0010 for 10 calls)
- **line_items** → `gpt-5.4-mini` (accuracy 100%, avg 1161ms, $0.0017 for 10 calls)
