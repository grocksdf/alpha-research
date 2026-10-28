# AlphaResearch v0.8.3 — Playbook evidence-fix audit and compatibility

## Evidence basis

This release was independently rebuilt from the user-provided `alpha-research-main.zip` (v0.8.2 source) and `v082-round3-handoff.md`. The handoff reports that one queued candidate (`10fc2baf`, `trial_id=None`) was incorrectly supplied to Playbook, causing `learn()` to reject evidence and stop Batch 1. The handoff **does not contain the original SQLite database**. This release does not claim to have replayed the historical provider's model output or mutated that historical database.

## Changes in this release

- A host-generated `completed_evidence()` whitelist checks `v08_queue.state=evaluated`, the bound trial's `tasks.state=completed`, persisted result payload, expression/fingerprint identity, and result scope. Queued, deferred and unknown writes are excluded.
- Only this whitelist, not the broader exploration queue, is passed to Playbook as `new_experiment_evidence` and `allowed_evidence_ids`.
- `review_knowledge()` binds the entire proposal list to its immutable archived Playbook tool response (role, model identity via original request record, request/response/tool receipts, canonical SHA). It validates and adjudicates **each proposal separately**.
- `v08_knowledge_reviews` records immutable item-level accept/reject decisions. Accepted knowledge, its review receipt, and the append-only event are inserted in **one SQLite transaction**. Rejected proposals also receive a transactionally audited receipt.
- `learn()` still rejects unknown or uncompleted evidence; it is **not bypassed**, and unexpected database/write failures still fault the batch.
- After operator `resume`, `reconcile_previous_playbook()` processes a previously archived completed Playbook result from a failed batch **without requesting new LLM output**. It skips accepted/rejected receipts; a missing receipt means the knowledge transaction was rolled back. This cannot cause a BRAIN Simulation write or repeat an LLM call.
- Pending Critic revisions remain in the persistent `v08_revisions` queue; regression tests demonstrate they can be consumed by a later batch with parent lineage.
- Only completed synthetic Demo trials can support `synthetic_process` / `synthetic_process_observation`. They do not support `research_is`, `verified_market`, or real-alpha performance claims. Planned experiments and untested formulas are not market knowledge.
- The frozen protocol is bumped to `llm-autoresearch-v083`. Existing frozen v0.8.2 project databases **must not be in-place migrated**. Production `execute_batch` checks this read-only before constructing an Engine; lifecycle `control` also checks exact frozen project identity. Create a new project and database for v0.8.3.

## Safety and test coverage

No predetermined Alpha candidate generators are added to production modules. The scripted formula fixture lives only in `tests/test_v083_playbook.py` and is imported solely by `verification/v083_offline_three_batches.py` for offline QA. The v0.8.3 Live BRAIN route is disabled; Alpha Submit is independently gated and unchanged. No BRAIN Simulation or Submit calls were made.

The new regression module covers mixed legal/illegal Playbook references; invalid queued evidence; direct `learn()` fail-closed behavior; empty knowledge after all-invalid proposals; synthetic-only grading; exact archived source checks; immutable audit receipts; idempotent replay; interrupted write/reconciliation; unknown Simulation blocking; frozen project safety; pending Critic revision consumption; and memory retrieved/referenced/applied accounting.

The three-batch scripted Demo SQLite and report are **test fixtures only**. They demonstrate local protocol behavior and synthetic evidence, not genuine LLM hypothesis quality, memory benefit, or real market performance. Real LLM three-batch verification requires **separate authorized model-cost approval** and a new frozen plan; no such requests were made in this release.

## Reproduce

```bash
python scripts/release.py --check
python -m unittest discover -s tests -p 'test_v083_playbook.py' -v
python verification/v083_offline_three_batches.py --output /tmp/alpha-v083-scripted-only
python scripts/release.py --output /tmp/alpha-v083-sources.zip
python scripts/release.py --verify-archive /tmp/alpha-v083-sources.zip
```

The full suite may be run with `python -m unittest discover -s tests -p 'test_*.py'` where test runtime permits; the delivery includes per-module isolated logs and a consolidated reproducible summary. The historical v0.8.2 source ZIP contains no original `.git` directory, so the Git diff is against an independently imported, verbatim ZIP baseline, **not** a verified upstream GitHub SHA.
