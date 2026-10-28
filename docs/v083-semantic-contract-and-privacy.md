# v0.8.3.post2 — Research role contract & model-output privacy boundary

## Evidence and version caveats

- Code baseline: **uploaded** v0.8.3.post1 source ZIP. This is not a verified GitHub HEAD.
- Handoff: `HANDOFF.md` and `stage4-evidence-v4.json`, both read-only; the two historical experimental SQLite files named in the handoff were **not supplied** in this turn.
- The handoff reports two real Kimi K3 Demo batches completed, a third failing at Critic, six evaluated synthetic trials, two lineage links, eight synthetic Playbook items, and USD 0.4447464 of **known** model cost. These are evidence assertions from a redacted report, not re-executed experiments.
- Because no raw rejected Critic body exists in the supplied evidence, the claim that an academic link or email triggered the historical rejection is **plausible but not independently proven**. No source database was modified.

## What changed, and why it is not Alpha hardcoding

`alpharesearch/v083_semantic_contracts.py` defines machine-readable role rules. The **same** rules are rendered as LLM instructions and executed as deterministic gates. Structural hints are generated from `SCHEMAS` dynamically. These include:

- Explorer `required_fields`: the exact field identifiers from the currently approved catalog, with required research sources. Never a draft formula.
- Strategist: memory references pair one-to-one with actual application rationales; `memory_effect` is nonempty even if no memory is cited; indexes must refer to supplied hypotheses.
- Worker: hypothesis indexes must have been selected; any pending revision needs a concrete delta.
- Critic: exactly one economic review per validated Worker proposal, covering every proposal index once.

No Alpha formula, economic family, candidate pool, rank order, or research mechanism is supplied by this contract. New experimental directions remain model-proposed, while field/operator approval remains deterministic.

The tool JSON schema is still validated independently. We did not allow a missing tool call, partial JSON, truncated response, identity substitution, fabricated provenance, invalid formula, or unsupported field to pass.

## Separating a privacy scan from ordinary citations

The legacy broad `_safe_text()` redaction was wrongly used as an all-or-nothing acceptance test. The new `v083_output_privacy.py` makes two distinct decisions:

1. **Admission**: reject high-confidence credential patterns, including Bearer, `sk-`, explicit secret assignments, credentials embedded in URL userinfo, and plausible exact matches with configured local environment secrets. When rejected, append only the request/response hashes and indicator classes to the hash-chained events; **do not archive raw model body**.
2. **Storage**: benign scholarly HTTP(S) references may proceed; redact incidental mailbox addresses, and strip URL query/fragment before archiving and passing the response to downstream consumers. The received **raw tool argument SHA-256** and **sanitized canonical body SHA-256** are separate. Source links may lose tracking parameters, but the model's validated structural output, request ID, response ID and tool call ID remain bound.

The screen is deliberately conservative, **not a guarantee that all possible personal/private text is detectable**. Future provider tests must review whether the privacy redaction preserves important citation paths and whether additional credential formats need policy review. Existing `_safe_text()` remains available for legacy log/report redaction.

## Audit and recovery

New `v08_structured_output_security_review` events record category counts and disposition (never matched values), including rejected outputs. `v08_structured_contract_rejected` events bind malformed semantic/shape replies to their source SHA and, if applicable, a rule ID. Old `v08_role_payloads` remains source-bound; no database migration or mutation of prior plans.

Unknown or failed model requests still fail closed; no automatic retry of a potentially paid call, and an LLM retry never replays a BRAIN Simulation. The frozen research protocol changed to `llm-autoresearch-v083-semantic-v2`: old paused/frozen runs **cannot** be silently resumed under this code identity. A new approved plan and new database are required for a new genuine model experiment.

## Tests and acceptance limits

The dedicated offline tests exercise both pure contracts and full `execute_batch` with scripted tool-call responses. They verify that a citable DOI and author email cannot block the Critic, storage redaction and source hashes, secret-like replies fail closed, and semantic violations produce an auditable reason without creating a trial. All pre-existing budget, Playbook, AST and Submit gates remain in the project test suite.

**No genuine LLM request** was made in this repair. A real three-batch run is still necessary to establish genuine provider compatibility and real model autonomy; synthetic Demo metrics have no real-market-alpha significance. Do not turn on Live BRAIN or Submit on the basis of these offline tests alone.

## Reproduce offline

```bash
python scripts/release.py --check
python verification/v083_contract_offline_suite.py --repo . --report-dir ./offline-qa --timeout-seconds 90
python scripts/release.py --output ./AlphaResearch-v0.8.3.post2-source.zip
```

The test runner removes configured model/BRAIN secret environment variables from workers and denies network socket connections. The source ZIP and wheel are built and re-tested independently in the accompanying release evidence.
