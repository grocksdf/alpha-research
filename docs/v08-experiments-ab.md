# AlphaResearch v0.8 — 真实 LLM 实验及研究记忆 A/B 评测记录

## Local genuine LLM multi-round acceptance: **NOT RUN**

This environment contains neither an `LLM_API_KEY` nor a reachable, authorized local model endpoint (e.g. a loopback tools-capable service). The user authorized local LLM experiments, but **did not authorize real BRAIN Simulation or Submit**. It would be false to report scripted LLM calls as a genuine autonomous research success.

Engineering-only scripted offline evidence: `tests/test_v08_autoresearch.py` runs three consecutive bounded batches. A test fixture constructs a novel formula from whichever reviewed field name it finds; this expression is test data only and never a product seed. The agent roles, candidate provenance, AST validator, Demo simulator, parent-child hypothesis lineage, memory selection and contradictory memory exclusion are exercised. All fixture-generated evidence is classified `offline_scripted_test`/`synthetic_process`.

### Reproducible real local-LLM acceptance *when a genuine local provider is supplied*

1. Run a tools-capable LLM server bound to a private loopback host, validate model identity and configure **only** the `llm.base_url` and `llm.model` in a fresh offline Demo config. Do not connect BRAIN or enable Live/Submit flags.
2. Set `LLM_API_KEY` only in the local test runtime. Confirm `mode=demo`, `safety.live_research_enabled=false`, `safety.submission_enabled=false`.
3. Prepare a **new** v0.8 database and freeze a project plan using `alpha-research-v08 --config ... --database ... plan --project-id ... --objective ... --output ...`.
4. Run at least three bounded rounds with `alpha-research-v08 --config ... --database ... drive --plan ... --batches 3`.
5. Check that all 5 role responses per round carry model identity, complete structured payload, original+canonical SHA256 and response ID. Check at least one original LLM-authored legal Alpha actually enters a synthetic Demo simulation, and its feedback is used explicitly in next-round Strategist memory references. Capture any failures and true token/price usage or unknown values; no fallback.
6. Repeat with different research goals/catalogs, plus no-memory and shuffled-memory controls (four-arm A/B below), under separate frozen plans. Only after independently verified real-model outputs and multiple independent seeds/tasks can one claim autonomous-discovery **engineering** acceptance.

No authenticated remote request was issued here. Because genuine model behavior and actual non-deterministic convergence remain unobserved, **the user's strongest final criterion has not been empirically proven**.

## Independent A/B benchmark, never a product generator

Module: `benchmarks/evaluate_memory.py`. Four independent arms under identical task, field catalog hash and bounded budget hash:

1. `rule_random_baseline` (offline comparator only; no v0.8 candidate injection)
2. `llm_no_memory`
3. `llm_full_playbook`
4. `llm_shuffled_memory`

Each frozen JSONL record includes `arm`, `task_id`, `authorized_catalog_sha256`, `budget_sha256`, `source_run_id`, evidence grade, counts (`attempted`, `legal`, `duplicates`, `revisions`, `simulation_count`), `mechanism_labels`, `platform_checks_passed`, independent validation flag. Compare mechanism diversity, legal/duplicate rates, revision count, legal candidates per simulation, authentic platform checks and independent passes; do not call larger candidate volume a benefit. Equal field/task/resource hashes and all four arms are required. Output remains `ENGINEERING_ONLY` for synthetic records and NEVER sets `memory_improvement_proven=true` without actual preregistered statistical testing.

To execute on frozen records: `python benchmarks/evaluate_memory.py path/to/frozen-four-arm.jsonl`.

**Actual A/B evaluation results: `INCOMPLETE` (no genuine matched records provided).** Run with `[]` returns `INCOMPLETE` and never creates a candidate or makes a network request. Memory uplift, genuine BRAIN Alpha effectiveness and independent market validation are **not demonstrated**.
