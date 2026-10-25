# v0.8.1 P1/P2 final engineering acceptance

This is a narrowly scoped security and verification release. It **does not**
activate Live BRAIN or Submit and never uses scripted research output as proof
of real LLM discovery.

## Research memory: three distinct levels

- `memory_retrieved`: valid, latest, provisional Playbook revisions loaded
  into the model context; rejected/stale/superseded revisions excluded.
- `memory_referenced`: exact revision IDs the **original archived Strategist
  tool response** explicitly cites in its individual decisions. All IDs must
  occur in the current retrieval and have actual permitted supporting trials.
- `memory_applied`: cited revisions with an explicit structured explanation
  of the decision change and the experiment evidence supporting that change.
  This is an **auditable model assertion about a decision**, **not proof of
  causal influence**.

Each LLM Strategist decision now includes a `memory_application` array binding
`memory_id`, `decision_change`, and `evidence_rationale`. The database stores
the role's canonical original payload and an append-only decision receipt with
hypothesis index, action, priority, experiment, explanation, revision digest,
evidence IDs and evidence grade.

The genuine-model test reads both the stored model payload and immutable event
receipts; merely retrieving knowledge cannot satisfy its memory-use check.
A Scripted-Demo negative test proves that two batches can retrieve memories
without applying any, and acceptance remains INCOMPLETE. A/B evaluation with
no-memory, original-memory and shuffled-memory controls remains mandatory
before claiming causal research lift. Synthetic evidence is not Alpha
performance evidence.

## Reviewed LLM rates and call budgets

The `[cost]` table contains numeric `input_per_million_usd` and
`output_per_million_usd`, but numeric zero alone is NOT an approval.
For genuine LLM research it must also contain exactly:

```toml
[cost.price_review]
source = "Provider official price record or contract ID"
applicable_models = ["explicitly-reviewed-model-id"]
reviewed_at = "2026-10-25T00:00:00+00:00"
reviewed_by = "Finance or security reviewer"
approval_ref = "A documented price approval reference"
zero_price_reviewed = false
```

The source and rates must be real, the date timezone-aware and within 90 days,
and the actual requested model among `applicable_models`. A zero input or output
rate requires `zero_price_reviewed=true`; explicitly approved 0/0 rates get a
distinct `reviewed_zero` status. Missing/invalid rates retain `unknown_price`
and **prevent real model calls**.

The preflight uses remaining known token and USD budgets plus a conservative
proxy for prompt/tool token overhead to set `max_tokens` per call. It logs the
reviewed model, rate source, approval reference/time and requested output
limit. Strict local controls: rejecting unauthorised prices, models and
insufficient estimated budget, and passing bounded `max_tokens` in requests.

**Not strict:** the provider's actual tokenisation, hidden reasoning tokens,
prompt overhead, pricing changes and invoice charges. A USD budget is an
estimated **soft** constraint; even with a margin it cannot promise that an
already accepted provider request will not exceed an invoice threshold.
The observed overage causes subsequent work to stop and remains auditable.
Provider errors without usage retain NULL token counts and NULL USD, never 0.

## Configuration type errors

`_model_set()` now validates `model`, `fast_model`, `reasoning_model` and
approved aliases **before** creating sets. Unhashable list/dict, empty or
malformed strings and invalid alias approvals raise `ResearchError` rather
than unchecked `TypeError`.

## Acceptance boundary

Run all tests from an offline/network-blocked process, then verify the
reproducible source manifest and freshly extracted source ZIP and wheel.
A successful scripted fixture suite proves engineering safety, not genuine
provider identity, memory improvement or market Alpha significance.
