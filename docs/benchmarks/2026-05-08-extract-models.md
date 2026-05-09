# Extract-stage model benchmark — 2026-05-08

Ran 4 candidate models against 10 ground-truth cases across 3 stages.
Started at 2026-05-08T15:50:10.573118+00:00.


## Stage: intent

| model | accuracy | evidence-grounded | avg latency (ms) | total cost | total tokens |
| --- | --- | --- | --- | --- | --- |
| `gpt-4o-mini` | 100% | 20% | 1818 | $0.0010 | 4499 |
| `gpt-5.4-mini` | 100% | 0% | 1362 | $0.0011 | 4628 |
| `gpt-5.4-nano` | 100% | 0% | 2557 | $0.0005 | 4586 |
| `gpt-5.5` | 100% | 100% | 2391 | $0.0177 | 4597 |

## Stage: customer

| model | accuracy | evidence-grounded | avg latency (ms) | total cost | total tokens |
| --- | --- | --- | --- | --- | --- |
| `gpt-4o-mini` | 100% | 40% | 1067 | $0.0009 | 4628 |
| `gpt-5.4-mini` | 100% | 20% | 1060 | $0.0010 | 4797 |
| `gpt-5.4-nano` | 100% | 10% | 1279 | $0.0005 | 4752 |
| `gpt-5.5` | 100% | 80% | 4637 | $0.0261 | 5704 |

## Stage: line_items

| model | accuracy | evidence-grounded | avg latency (ms) | total cost | total tokens |
| --- | --- | --- | --- | --- | --- |
| `gpt-4o-mini` | 80% | 100% | 2048 | $0.0011 | 6199 |
| `gpt-5.4-mini` | 100% | 0% | 1837 | $0.0016 | 8090 |
| `gpt-5.4-nano` | 100% | 90% | 2531 | $0.0009 | 8186 |
| `gpt-5.5` | 100% | 100% | 2965 | $0.0268 | 8026 |

## Per-model totals

| model | correct/scored | overall accuracy | avg latency (ms) | total cost |
| --- | --- | --- | --- | --- |
| `gpt-4o-mini` | 23/25 | 92% | 1644 | $0.0030 |
| `gpt-5.4-mini` | 25/25 | 100% | 1420 | $0.0037 |
| `gpt-5.4-nano` | 25/25 | 100% | 2122 | $0.0019 |
| `gpt-5.5` | 25/25 | 100% | 3331 | $0.0705 |

## Recommended per-stage assignment

- **intent** → `gpt-5.4-mini` (accuracy 100%, avg 1362ms, $0.0011 for 10 calls)
- **customer** → `gpt-5.4-mini` (accuracy 100%, avg 1060ms, $0.0010 for 10 calls)
- **line_items** → `gpt-5.4-mini` (accuracy 100%, avg 1837ms, $0.0016 for 10 calls)
