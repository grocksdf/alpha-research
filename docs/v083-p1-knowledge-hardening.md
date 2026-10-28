# AlphaResearch v0.8.3 P1: Playbook semantic trust boundary

## Baseline / compatibility

Source distribution `AlphaResearch-v0.8.3-complete-source.zip` is the input.
This patch is released as `0.8.3.post1`; its new frozen plan protocol
`llm-autoresearch-v083-p1` requires a new project and fresh database.
Existing immutable v0.8.3 events and legacy plans must be read only using the
corresponding historical version; there is no in-place migration or hash edit.

## Threat and mitigation

Previously `learn()` persisted `item["claim"]` and `item["applicability"]` as
trusted knowledge verbatim while only `evidence_grade` was host-assigned.
A real-market earnings guarantee written by an LLM could contaminate next
rounds as if supported by synthetic metrics.

Now the model's raw `topic`, `claim`, `applicability`, `uncertainty` and
`open_question` remain **exactly** in the source-bound immutable
`v08_role_payloads` record, auditable with the review receipt's SHA256.
They are interpretations **not supported facts**. `v08_knowledge.body`
is *projected* by deterministic host code from finished trial facts:

- `claim` is a host statement of completed trials, explicitly not alpha
  performance in a real market;
- `applicability` is `demo_synthetic_process_only` or
  `live_in_sample_research_only`, depending on evidence;
- `uncertainty` is host-authored and explicitly denies independent evidence;
- `observations` contain trial IDs and observed metrics with synthetic flags;
- `evidence_scope` is typed and host-authored, always marks
  `market_alpha_validated=false` and `independent_validation=false`;
- `model_interpretation` is a source pointer and digest only, never a factual
  quotation or unqualified summary.

`memory_for()` exports typed fact/scope/uncertainty and an
`unverified_archived_only` pointer; it does not export model language as
validated knowledge. This is not a keyword filter: even a subtle, novel
profitability assertion has no route into trusted free-text knowledge.
The unverified model reasoning is preserved for separate human/independent
review, retaining autonomous research proposals without evidence promotion.

## Safe partial structure handling

`parse_tool_response` still rejects invalid envelopes, incomplete JSON,
truncation (`finish_reason=length`), absent/wrong/multiple tools and oversized
arguments. `LLMRoles.ask()` checks top-level knowledge collection shape and
full vendor provenance before committing the exact raw JSON hash and content.
Only at `review_knowledge()` are individual JSON items validated, producing
independent accept/reject hash-chain receipts. No text reconstruction,
response repair or fabricated missing keys is used.

`learn()` also rechecks the complete source-bound Playbook request and item
hash, so it cannot directly persist an unsourced item by inventing a receipt.
Knowledge insertion and review receipt remain atomic. An invalid item does
not roll back existing trial records or siblings. Genuine DB/storage failures
retain the conservative pause-and-manual-reconcile path, never repeating an
unconfirmed Simulation.

## Test classification

The `tests/test_v083_p1_semantics.py` suite is an adversarial scripted
**offline unit/integration test**, not a genuine provider model run.
It covers misleading free text, source hashes, scoped retrieval, mixed
missing fields, invalid nested fields, all-invalid knowledge, invalid outer
schema, truncation, malformed JSON, text fallback and idempotent review.
A source build/re-extract and full regression are separately evidenced in
`reports/` of the delivery package. The genuine LLM, real-market learning
and v0.9 BRAIN admission remain separate open verification gates.
