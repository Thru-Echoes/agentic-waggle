# Stage B Disambig Model Comparison — v5.1 Cycle 3.5

**Date:** 2026-05-14  
**Corpus:** `tests/fixtures/matcher_corpus.yaml` (v5.1 — 281 cases across 24 categories)  
**Eval harness:** `tests/test_matcher_eval.py` (full mode, `MATCHER_EVAL_FULL=1`)  
**Decision:** **ship `gpt-5.4-mini`** for `copilot_matcher_disambig_model`.

## Why this comparison

v5 baseline (gpt-5.4-nano) had 7 customer categories under threshold,
attributed in the compact-context to LLM-jitter on the cheap nano tier.
Cycle 3 swapped to gpt-5.4-mini for the alias / pronoun / casual /
compound improvements predicted in the prior session — and saw a real
+1.0pp aggregate gain, but with **regressions on cross_customer_collision
and site_shortform** (mini over-commits on prefix collisions vs nano).

To validate the choice, we ran the full corpus against four candidate
disambig models on the same eval harness and same corpus SHA
(`31866c57aa52`).

## Results

> **Pre-rigor snapshot (2026-05-18).** These numbers were measured before
> the 2026-05-19 eval-rigor pass that started asserting
> `expected_alternatives_min`. Under the post-rigor contract, the
> shipped `gpt-5.4-mini` measures **95.2% customer / 95.8% SKU**
> (the prior "100% SKU" was inflated by 3 vacuous null passes —
> 2 `sku_ambiguous` cases + 1 `sku_negative` jitter; see
> `matcher_phase_i_summary.md` for the honest post-rigor breakdown).
> The model-vs-model comparison below still holds directionally;
> a like-for-like re-measurement of the other three models was not
> performed (would cost another ~$5 and the ship decision stood).

| Model | Customer | SKU | # FAIL customer cat | Deterministic? | Runtime | Cost/eval |
|---|---|---|---|---|---|---|
| `gpt-5.4-nano` | 95.2% (200/210) | 100% (71/71) | 6 | yes (`temp=0`) | 11 min | ~$0.05 |
| **`gpt-5.4-mini`** | **95.2% (200/210)** | **100% (71/71)** | **4** | **yes (`temp=0`)** | **11 min** | **~$0.05** |
| `gpt-4o-mini` | 89.5% (188/210) | 91.5% (65/71) | 9 + 3 SKU | yes (`temp=0`) | 11 min | ~$0.05 |
| `gpt-5.5` | 96.7% (203/210) | 100% (71/71) | 4 | **no** (`temp=1` only) | 20 min | ~$3-5 |

`gpt-5-mini` was also evaluated but **also requires `temp=1`**; one snapshot
was abandoned at 38 min after the patch path was extended. Same
non-determinism caveat as gpt-5.5; not pursued further.

### Provenance note on the gpt-5.5 numbers (audit-trail disclosure)

The `gpt-5.5` 96.7% / 100% figures above came from a single
**temperature=1, non-deterministic** snapshot, run against an
exploratory patch (`temperature=1 if model.startswith('gpt-5.5')`) in
`providers/semantic_matcher.py` that was reverted post-decision (the
shipped code is `temperature=0` unconditionally). The TRACE audit trail
for this evaluation is **incomplete**: session `trace_20260518_b35eea`
event `evt_006` records only the *first* gpt-5.5 attempt, which was
invalid (temp=0 rejected → all 281 disambig calls failed → null-coincidence
scores 24.8% / 35.2%); the subsequent valid temp=1 re-run that produced
the 96.7% figure was not logged as its own TRACE event in the
multi-day-staled session. The shipped decision (ship `gpt-5.4-mini`,
revert the patch) is unchanged — non-determinism + 37× cost still
disqualifies gpt-5.5 — but a reader trusting these numbers should know
they (a) are a single non-deterministic snapshot, not a stable metric,
and (b) lack a direct TRACE corroboration. To independently verify, one
would re-apply the temperature patch and re-run with
`MATCHER_EVAL_FULL=1 COPILOT_MATCHER_DISAMBIG_MODEL=gpt-5.5`.

## Per-category — `gpt-5.4-mini` vs `gpt-5.5`

The 14 unchanged categories are omitted. Both hit 100% on:
abbreviation, apostrophe_variant, canonical, casual_colloquial,
generic_words, long_form_sentence, negative_case, partial_match_noise,
possessive, pronoun_resolution, typo_resolvable. Both 90.0% on
compound_reference. Both 90.9% on ambiguous_reference. Both 86.7% on
site_reference.

| Category | `gpt-5.4-mini` | `gpt-5.5` | Δ |
|---|---|---|---|
| `alias` | **100%** | 92.3% | gpt-5.5 regresses -7.7pp |
| `cross_customer_collision` | 72.7% | **100%** | gpt-5.5 fixes +27.3pp |
| `site_shortform` | 62.5% | **75.0%** | gpt-5.5 improves +12.5pp |
| **Overall** | 95.2% | **96.7%** | gpt-5.5 +1.5pp |

## Why ship mini, not gpt-5.5

`gpt-5.5` wins on aggregate (+1.5pp) and resolves mini's two
prefix-collision regressions. But three structural concerns outweigh
that:

1. **Non-determinism.** The OpenAI API rejects `temperature=0` for
   `gpt-5.5` (and `gpt-5-mini`): *"Only the default (1) value is
   supported."* Stage B's job is reproducible structured-output
   disambiguation. A single snapshot at temp=1 is illustrative, not
   definitive — re-running could give different numbers. The
   `cross_customer_collision` 100% may be a lucky run.

2. **Cost.** ~37× per call. Production matcher hits Stage B on every
   customer / SKU resolution: 1 call per copilot turn, 5-10 calls per
   order-extraction email. Per-extraction cost rises 10× across the
   stack.

3. **Latency.** ~2× slower (20 min vs 11 min on the same eval set).
   Adds ~1-2s per chat turn / extraction call.

The mini regressions on `cross_customer_collision` and `site_shortform`
are real but mitigated by the **confidence-graduated UX**: when Stage B
confidence is below the auto-accept threshold,
`POST /copilot/turns/{id}/confirm-match` surfaces a `pick-match` partial
with alternatives, so a user typing `Riverbend` sees the two candidates
rather than being silently assigned the wrong one.

## What rolls forward to the v5.1 ship

- `app/config.py` — `copilot_matcher_disambig_model = "gpt-5.4-mini"`.
- No code change to `providers/semantic_matcher.py` (the exploratory
  `temperature=1`-for-gpt-5.5 patch was reverted post-decision).
- Eval reports at `.reports/matcher_eval_{customer,sku}.md` reflect the
  shipped mini state.

## What's deferred (cycle 4 candidates)

- **Stage A FTS tokenization fix** for the
  Cobblestone↔Drakestone Main Warehouse swap (`site_reference` 86.7%) —
  not a Stage B issue; no model swap helps.
- **Mini prompt iteration** for prefix-collision behavior:
  `cross_customer_collision` cases where two seeded customers share a
  prefix token (`Riverbend`, `Wenatchee`, `Seven Acres Farms`) — explicit
  FIRES/DOES-NOT-FIRE example for the prefix-collision pattern.
- **`Cascade` ambiguity rule** — `ambiguous_reference` 90.9%; the only
  failing case is `Cascade` over-resolving to Cascade Orchard Partners.
- **Combined customer + SKU corpus cases** — schema extension; the current
  corpus model asserts one-or-the-other.

## Reports preserved

Full per-model reports at:
- `/tmp/eval_customer_v5.1_nano.md` + `/tmp/eval_sku_v5.1_nano.md`
- `/tmp/eval_customer_v5.1_mini.md` + `/tmp/eval_sku_v5.1_mini.md`
- `/tmp/eval_customer_v5.1_4omini.md` + `/tmp/eval_sku_v5.1_4omini.md`
- `/tmp/eval_customer_v5.1_55.md` + `/tmp/eval_sku_v5.1_55.md`

The mini reports are also the canonical `.reports/matcher_eval_*.md`
in the v5.1 commit.
