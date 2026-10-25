# AlphaResearch: LLM source identity and field-unit evidence (offline engineering contract)

This document describes the **code-level** validation boundary. A provider's returned
`model` string is an assertion made by a remote gateway, not cryptographic proof of
which weights were executed. Trusted identity attestation, BRAIN account permissions,
and market performance require separate evidence and are **not** established here.

## 1. Campaign source barrier

For every campaign with `generator = "llm"` (including Demo LLM dry runs), and
for any Live execution path, `_register_batch()` **must** receive the same
`last_batch_provenance` object produced by the `DiscoveryAgent` tool call.
It verifies a unique `llm_batch_started` and `llm_batch_proposals` event,
absence of `llm_batch_failed`, exact candidate order/hash, frozen model identity,
response/tool call IDs, and generation cycle. A missing or forged proof causes an
exception **before** any new queue write or Simulation.

Accepted and rejected proposal attempts are associated with an append-only,
indexed `campaign_proposal_attempt` event. Newly inserted candidates separately
receive one `campaign_proposal_source` event. A candidate restored across Runs
must validate its original source/batch, including duplicate attempts, before
it can be dispatched or reconciled. A direct queue insertion without this proof
cannot be tested. The public Campaign Live runner is **still disabled**.

## 2. Frozen model identity and reviewed aliases

By default, `requested_model == returned_model` is required. Missing model IDs
and unknown replacements are rejected even if the returned tool-call content
looks valid. If a gateway is documented to report a different canonical model
name, add a **reviewed, exact** alias to the `[llm]` settings **before creating a
new frozen plan**. Example (illustrative values only):

```toml
[llm]
model = "GATEWAY_REQUEST_MODEL_A"
# base_url and timeout_seconds remain as already configured.

[[llm.response_model_aliases.GATEWAY_REQUEST_MODEL_A]]
returned_model = "GATEWAY_RESPONSE_MODEL_B"
reviewed_by = "security-review"
approval_ref = "CHANGE-2026-123"
reason = "Verified gateway documentation describing this exact canonical model alias"
```

The request model must be one of the frozen `model`, `fast_model`, or
`reasoning_model` identities. Each alias must contain the four reviewed fields
shown above; extra keys, duplicates and wildcard/prefix acceptance are refused.
The mapping and approval references are included in the frozen LLM snapshot.
The `llm_batch_started` and `llm_batch_proposals` events retain the policy
SHA256, actual returned label, verification status (`exact_match`,
`reviewed_alias`, or a rejection reason), and approval reference. An untrusted
request cannot supply its own alias allowlist to `verify_source()`.

**Important:** Do not add a mapping based solely on a model response claiming
another name. An authorized reviewer must examine trusted supplier documentation
and preserve the approval evidence outside the research database. Editing this
mapping for an existing frozen plan is not a supported migration path.

## 3. Three-state unit evidence

`validate_units()` first relies on the original AST, operator, type and PIT
validations. Unit checks only reject contradictions **proven** by reviewed field
metadata, for example `PRICE + SHARES` or `log(PRICE)`. Unreviewed fields remain
unknown, never inferred from names such as `high`, `low`, `close` or `vwap`.

- `verified`: no missing or conflicting dimensional evidence for inspected AST.
- `insufficient_metadata`: the expression remains eligible but emits structured
  warning codes, including `field_unit_unreviewed`,
  `additive_units_unverified`, or `dimensionless_requirement_unverified`.
- `literal_unit_unspecified`: a nonzero numeric offset (such as `volume+1`)
  has no DSL-level unit annotation. It is ambiguous even with reviewed field
  units, so it is not a proved conflict.
- `UnitError`: contradictory **reviewed** units or an unsupported semantic
  operation; it is not reclassified as merely uncertain.

A `rank()` output is dimensionless but its input can still have unreviewed
units, and this distinction is preserved. Warnings are included in the
candidate event (`unit_evidence_status`, `unit_evidence_warning_codes`). The
warning is a review task, not evidence of market performance or permission to
skip independent validation.

For production catalogs, reviewers should explicitly document every field's
unit scale, conversion, corporate-action basis and timeliness, then record:

```json
{
  "kind": "MATRIX",
  "point_in_time": true,
  "min_delay": 1,
  "unit": "PRICE",
  "unit_reviewed": true,
  "unit_review_ref": "DATA-CATALOG-APPROVAL-123"
}
```

These are **illustrative keys only**: the project deliberately leaves the
existing demo catalog without fabricated `unit_reviewed = true` claims.
Unit names are exact symbolic labels; two different strings must not be treated
as equivalent without an explicitly reviewed normalization policy.

## 4. Security and acceptance boundary

No historical SQLite record, frozen plan hash, Simulation limit, or Submit
safety gate is modified. Changes to source files produce a new source snapshot;
old frozen Runs remain bound to the previous code. Automated tests must run
with sockets blocked, temporary databases, and no provider credentials.
The full suite's success proves these **tested code paths**, not provider
identity authenticity, genuine BRAIN entitlement, or empirical Alpha quality.
