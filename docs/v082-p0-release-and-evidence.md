# AlphaResearch v0.8.2 — P0 real-LLM engineering repair

This release **does not** authorize BRAIN Live, Alpha Submit, or unconstrained
model spending. No fixed candidate formula seeds, fallback factor families,
unreviewed operator aliases or result fabrication were added.

## Input provenance / evidence limitation

The v0.8.1 official release ZIP is the executable development baseline.
The distributed ZIP contains no `.git` directory. For reproducible patch
history, the source was imported into an independent local Git repository,
then this v0.8.2 branch was created. This local HEAD is **not verified to be
the remote GitHub HEAD**.

The two read-only real-experiment SQLite databases / redacted JSON replies
described in the request were **not attached** to the current sandbox. The
claims (Run1 first Critic deferred, Run1/Run2 Critic missing tools, 2048 tokens,
no trials/knowledge, no Live or Submit) are user-provided preliminary
observations. This release recreates these response shapes in
**network-blocked deterministic tests**. It must not claim the original Run
database, original provider response, or account billing was independently
verified.

## 1. Reviewed model output capabilities

A real v0.8.2 LLM is **not allowed** without a fully reviewed
`[llm.v08_capabilities.models.<model>]` profile. The entire profile is
part of the frozen research plan through `snapshot(config)`. It contains:

- `model`, `provider`, `reviewed_by`, `review_ref`, `reviewed_at`,
  `capability_source`
- `max_context_tokens`, `max_output_tokens`
- `output_token_parameter`: exact provider-verified `max_tokens` OR
  `max_completion_tokens` (never both)
- `role_output_tokens` for explorer, strategist, worker, critic, playbook
- `reasoning_parameter`: `none`, `reasoning_effort`, or `thinking`,
  with the exact approved `reasoning_value`
- `tool_choice_required_supported = true`.

Role caps are **not fixed in production source**. The capability profile is
valid only within a 90-day evidence-review window and is subject to finite
numeric ceilings, the original per-batch plan, cumulative campaign budgets,
prompt-input estimate, approved provider context and reviewed soft USD budget.
No model can be made unrestricted by setting a huge integer in config.

The token source distinctions are recorded:
- `estimated_prompt_token_upper_proxy`: a **UTF-8 byte-based heuristic**, not
  actual provider token count or a guaranteed upper bound.
- `usage.prompt_tokens`, `usage.completion_tokens`: provider-reported tokens.
- `usage.completion_tokens_details.reasoning_tokens`: provider-reported
  hidden reasoning consumption if supplied; otherwise `null`/unknown.
- provider invoice: **not guaranteed** by a locally bounded `max_tokens`.
- failed/no-response requests: token/US dollar usage remains **unknown**,
  never silently converted to zero.

Provider output limits of 4096 and 8192 are tested in offline parameter/
budget tests. For actual model compatibility and success rates, the isolated
`verification/v082_model_probe.py` requires a real approved provider,
reviewed pricing, available API credentials, and an explicit `--execute`
switch. Merely running it without permissions makes **zero requests**.
Select limits from the command line; those numbers are not production defaults.

### Moonshot Kimi K3: provider-specific verified facts

Official direct API documentation:
- https://platform.kimi.ai/docs/guide/kimi-k3-quickstart
- https://www.kimi.ai/help/kimi-api/api-troubleshooting
- https://github.com/MoonshotAI/Kimi-K3

The official Moonshot direct API says `kimi-k3` keeps thinking enabled and
uses top-level `reasoning_effort` values `low`, `high`, or `max` (default max).
The API troubleshooting guide recommends `max_completion_tokens` to avoid
`finish_reason=length`. The official model README also has `max_tokens`
examples, so actual field support may vary with the provider/gateway.
**Do not infer that disabling thinking is the only fix.** The reviewed model
profile must reflect the specific endpoint. For `provider =
"moonshot_direct_kimi_api"` with model `kimi-k3`, setting K2-style `thinking`
or an unverified reasoning-off value is explicitly rejected. Third-party
gateways require their own profile and authenticated compatibility tests.

## 2. Tool response parser and fault diagnostics

`v082_protocol.py` checks `choices`, `message`, `finish_reason`, tool count,
authorized tool name and exact JSON object. A `finish_reason=length`
response raises `ModelOutputTruncated`. Missing calls or malformed responses
raise `ModelToolCallMissing` / `ModelToolCallInvalid`, never bare KeyError.

For each failure the append-only event log records: model, role, request ID,
provider response ID if available, output parameter/cap, reason, provider
token counts (including hidden reasoning if reported), error type and
SHA256 of the complete response. It does **not** store prompt secrets,
headers, raw response content or raw reasoning.

Network errors are not retried automatically. Once a response has been
recorded as `failed`, a human operator can explicitly resume a
`paused_fault` project only with a reason of form:

```text
approve-model-retry:<failed-request-id>:<approval-reference>
```

The project can approve at most the frozen `max_model_output_retries`.
Unknown prior model charges must be reconciled before resumption.
A new batch is not a replay of any BRAIN Simulation request. Existing
ambiguous Simulation writes keep their old fail-closed handling.

## 3. Pre-Critic deterministic formula validation and bounded revision

New sequence:

`Worker -> AST, authorized fields, operators, PIT/delay and units ->
Worker-authored correction -> independent Critic -> queued candidate`.

Every attempted expression and Worker revision is recorded with request ID,
expression SHA256, parent attempt SHA256, deterministic error type, operator,
syntax location and change rationale. No deterministic formula replacement
is present. For example, an `multiply(...)` or `negative(...)` function
not approved in the BRAIN catalog remains invalid. The corrective
expression **must** be authored in a separate Worker model tool response and
revalidated by the same host Validator.

A Critic `defer` creates a source-linked persistent `v08_revisions` item
with review reason and LLM source IDs. A later Explorer/Worker receives it
as untrusted feedback and may propose a new expression with
`parent_revision_id` and change rationale. That new expression is rechecked
before another Critic review. The original result is never converted to
an approved candidate by default. The source event chain and revision
state replay detect unaudited modifications.

A batch with no approved candidate and no legally executed trial creates
**no Playbook experience**. If a deferral is still repairable, the project
can continue until its frozen consecutive-empty-batch ceiling; otherwise it
pauses in a fault state with a recorded reason. A valid sibling candidate
may still simulate even if another proposal failed deterministic checks.

## 4. Cross-Run resource guarantees

`make_plan()` now separately freezes:
- per-batch simulation/model call/token/USD and time budgets;
- repair attempts per formula, Critic deferral depth and empty-batch count;
- explicit human model-response retry ceiling;
- cumulative **campaign** model call/token/known USD ceilings.

Every new model request checks **both** batch and lifetime costs. An
unknown previous provider charge blocks further calls. A different Run ID
cannot silently reset cumulative campaign budget.

The same code snapshot, model capabilities and approved source catalog
must match the frozen project plan; changes require a **new version plan**,
not editing previous plan JSON/hashes.

## 5. Real-provider acceptance / BRAIN admission

To conduct the 4096/8192 provider tool-capability comparison from an
authorized local environment, the operator must create a reviewed
model-specific configuration and execute:

```bash
python verification/v082_model_probe.py \
  --config config/approved-local-model.toml --model your-reviewed-model \
  --limits 4096,8192 --max-known-usd 2 \
  --execute --report state/v082-real-provider-output-probe.json
```

For an approved remote provider, add `--allow-remote-llm`, and make sure
the separate remote endpoint/data-export authorization applies.
The script cannot access BRAIN or Submit. The output reports actual
token counts, finish reasons, valid tools and model request count,
or a precise refusal reason. The operator may need to generate a new
frozen plan to change output profiles.

Next, use the v0.8 real-LLM three-batch acceptance script in a *fresh*
database with an approved model and budget. It must pass all real-provider
provenance, parent revision, synthetic Demo feedback and Playbook
use checks. A synthetic success cannot be called a market Alpha.
The four-arm memory A/B and independent BRAIN validation are separate,
future, human-authorized research steps.

**Current environment has no LLM_API_KEY and no authorized provider**, so
the genuine experiment has not run and the preceding success conditions
are *not met*. Live BRAIN and Submit remain disabled.
