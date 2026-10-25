# AlphaResearch v0.8 — 自主研究架构与证据型 Playbook

## 产品数据流（无候选模板）

```
User objective + approved PIT catalog + supported operators + cited external materials
                               |
                 LLM Explorer: new hypotheses / arbitrary mechanism labels
                               |
          LLM Strategist: evidence selection, memory IDs, priorities, experiments
                               |
             LLM Worker: author original FASTEXPR expressions
                               |
          Deterministic AST / PIT / operator / unit checks + LLM Critic
                               |
           SQLite durable queued candidate + immutable role provenance
                               |
           Existing bounded Engine / Demo Simulation (this delivery)
                               |
               Audited evidence -> LLM Playbook distillation
                               |
              Next bounded run; old queued work survives restart
```

The new official `alpha-research-v08` command does not accept fixture generators, a candidate JSON file, a seed/recipe family or tool-side formula mutation. It requires a genuine tools-capable LLM: either explicitly configured local loopback, or reviewed remote HTTPS with a frozen host/model/data-export approval plus an invocation-time `--allow-remote-llm`. Both require a model ID and API key; otherwise it fails closed. Live BRAIN execution is disabled pending separate user authorization. Arbitrary LLM responses are untrusted and are checked by schemas, original/current model identity, source receipts and deterministic AST, field, operator, PIT, context and unit validation.

## Separate immutable facts and revisable knowledge

- `v08_projects`: frozen project identity, objective, catalog/snapshot hash, bounded per-batch resources; **continuous** lifecycle with `max_total_simulations=null`, no fake huge number.
- `v08_batches`: run/batch linkage and explicit started/completed/failed state; stale-start detection blocks automatic replay.
- `v08_requests`: model role identity, prompt hash, completion/failure state, response ID and tool call ID; never pretend unknown vendor fees/tokens are zero.
- `v08_role_payloads`: complete, schema-checked/screened **structured tool payload** with original raw UTF-8 SHA256 and canonical JSON SHA256, model and request/response IDs. `UPDATE`/`DELETE` blocked.
- `v08_hypotheses`: full structured mechanism, evidence rationale, prediction, fields, falsification, risk, references, free-text mechanism label and optional parent hypothesis ID. Immutable body SHA256.
- `v08_queue`: source-verified candidate, AST fingerprint, immutable mechanism mapping, Worker/Critic request IDs, parent and child trial linkage, queued/testing/evaluated/deferred state. Priority: queued old proposals tested first.
- Existing `trials`, `tasks`, `results`, `costs`, append-only `events`: objective Simulation facts, resource claims, failure, and account-bound evidence from the original Engine. No Submit tools appear in this new module.
- `v08_knowledge`: immutable **revisions** with claim, applicability, uncertainty, support and counterexample evidence IDs, evidence grade, `supersedes_id`, status provisional/contradicted/stale, open question.

Only latest **provisional** revisions are retrieved by `Playbook.memory_for`; superseded, contradicted or stale facts remain in the DB but are excluded from model context. Every memory selection emits `memory_selected` with a SHA256 context hash; Strategist returns `memory_ids_used` and `memory_effect`, linked by `memory_influence`. Claims must cite actual experiment evidence IDs; unauthorized IDs and `unverified` experiment results are rejected. Synthetic Demo experiments are always tagged `synthetic_process`, never `verified_market` or real Alpha success.

SQLite append-only events, protective triggers and audit replay detect ordinary accidental/unauthorized row edits. A privileged operator with OS-level DB access can still replace SQLite and recompute an unanchored hash chain. Production anti-tamper requires independent signed/WORM chain anchoring, separate from this offline release.

## Resource and safety boundaries

Each batch creates a new bounded Engine Run, preserving proposal count and simulation budget gates, LLM call/tokens/cost protection, explicit wall-clock bounds and stale write interruption. Unknown token usage or an unconfirmed remote write pauses the project without non-idempotent retries. Operator pause/resume/terminate retains evidence; old frozen v0.6 Runs must continue under their original source snapshots, never a rewritten plan hash. Independent final-holdout gates, correlation limits and formal Submit permissions remain unchanged.

## External project inspirations, not copied code

Architectural ideas only: Morgan Stanley AlphaLab (MSML Apache-2.0); Microsoft RD-Agent (MIT); FactorMiner (MIT); QuantaAlpha multi-trajectory conceptual reference (license not confirmed). No external project code, execution engine, trading connectivity or deployment dependencies were copied.
