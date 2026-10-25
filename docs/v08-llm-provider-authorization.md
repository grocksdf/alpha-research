# v0.8 source release repair and reviewed remote LLM authorization

## Scope and limitations

The **existing v0.8 BRAIN Live/Submit prohibition remains unchanged**. This
release permits **real LLM-only, synthetic Demo research** against a local
loopback service, or an explicitly reviewed remote HTTPS Chat Completions
provider. The provider must support a single named function tool call for each
Explorer / Strategist / Worker / Critic / Playbook step.

The ChatGPT session model cannot be reused as a local Python API. An operator
must supply their own **genuine authorized service**, its actual supported
model ID and an API credential through `LLM_API_KEY`. The repository does not
contain, infer or retrieve provider credentials.

## Security policy

For loopback, `http://127.0.0.1:PORT/v1`, `http://[::1]:PORT/v1` or
`http://localhost:PORT/v1` is allowed without data-export authorization.

For remote services, **all** of these are required before any real request:

1. `mode = "demo"`, `live_research_enabled = false`,
   `submission_enabled = false`; use a fresh dedicated v0.8 SQLite database.
2. A literal `https://` DNS hostname on port 443 (no numeric public/private IP,
   user-info, query string, fragment, traversal, mixed case path tricks or
   alternate HTTP port). The entire approved base URL must match exactly.
3. A complete `[llm.remote_authorization]` record, frozen into the plan's
   LLM identity: exact URL, full requested model set, reviewer, non-placeholder
   ticket ID, review note, data export approval and start/expiry timestamps.
   Approval cannot last more than 90 days.
4. **Explicit `--allow-remote-llm`** on each CLI `run` / `drive` invocation.
   Direct Python execution needs the equivalent explicit operator flag and
   the same frozen reviewed authorization.
5. `LLM_API_KEY` is read from the **process environment** only; real API
   credentials should never be written to TOML/JSON, notes or logs.
6. LLM HTTP requests have no proxy environment lookup, no cookies, no
   automatic redirects, and CA-verified TLS. Network failures are not retried.
   Only a single validated structured tool call is accepted per role.

**An approval record in a user-editable TOML file is an accountable operator
claim, not cryptographic proof of provider identity.** Organizations should
keep the ticket evidence in their approval system, restrict filesystem access
and apply network egress controls. The server's `model` value remains
self-reported. The existing frozen model ID / reviewed alias mechanism still
applies independently of hostname approval.

### Example: prepare your own approved config

Copy `config/settings.toml` to an untracked local config at
`config/v08-provider.local.toml`, retain the original safety flags, and set
`[llm]` as follows (all identifiers are **placeholders**, not an approval):

```toml
[llm]
base_url = "https://api.openai.com/v1"
model = "REPLACE_WITH_A_SUPPORTED_MODEL_ID"
timeout_seconds = 90

[llm.remote_authorization]
approved_base_url = "https://api.openai.com/v1"
approved_models = ["REPLACE_WITH_A_SUPPORTED_MODEL_ID"]
approved_by = "REPLACE_WITH_ACTUAL_SECURITY_REVIEWER"
approval_ref = "REPLACE_WITH_REAL_APPROVAL_REFERENCE"
approved_at = "2026-10-25T00:00:00+00:00"
expires_at = "2026-11-01T00:00:00+00:00"
data_export_approved = true
approval_note = "REPLACE_WITH_APPROVED_RESEARCH_DATA_EXPORT_SCOPE"

[cost]
# Replace with the independently reviewed USD prices for your actual model;
# do not guess zero or assume ChatGPT subscription credentials work for API.
input_per_million_usd = 0.0
output_per_million_usd = 0.0
```

The sample intentionally fails authorization if placeholders are left. The
pricing zeros are **illustrative only**, not a pricing claim: for a non-free
remote provider, replace them with reviewed prices before genuine acceptance.
The full config needs the original scope, catalog, evaluation and safety
sections from `settings.toml`; do not replace the complete file with this snippet.

The syntax of the current OpenAI Chat Completions tool protocol is documented
at https://platform.openai.com/docs/api-reference/chat/create . Other vendors'
OpenAI-compatible endpoints may implement differing `tools`/`tool_choice`
semantics; verify before spending research budget.

### Preflight with zero network calls

```bash
python -m alpharesearch.v08_cli --config config/v08-provider.local.toml \
  --database state/v08-new-only.sqlite3 provider-check --allow-remote-llm
```

This command checks only configuration. Its output explicitly says
`provider_authenticated: false`, so do not treat it as a successful API login.

### Optional real LLM / Demo 3+ round acceptance

Only after the actual service and cost budget have been approved, set the
`LLM_API_KEY` variable **outside** this repository, and execute:

```bash
python verification/v08_genuine_llm_acceptance.py \
  --config config/v08-provider.local.toml \
  --database state/v08-real-model-fresh.sqlite3 \
  --project-id llm-only-acceptance \
  --objective 'Explore and falsify novel mechanisms from authorized fields' \
  --batches 3 --max-simulations-per-batch 2 \
  --max-tokens-per-batch 40000 --max-known-usd-per-batch 2.0 \
  --allow-remote-llm --report state/v08-real-model-acceptance.json
```

The acceptance refuses a pre-existing DB, missing credentials, unreviewed
prices, missing role responses, missing LLM provenance, no synthetic Demo
simulation, no actual evidence-based Playbook, no feedback-driven parent-child
revision or no cross-run memory usage. Failed attempts retain their original
fact trail and are **not** substituted with scripted candidates.

It reports `PASS_REAL_LLM_ENGINEERING_PROTOCOL` **only** for authenticated
HTTP tool responses meeting all criteria; the result remains **Demo-only** and
cannot prove real Alpha performance or Playbook causal lift.

## Repaired release inventory

- `scripts/release.py`: `benchmarks/` is now an enumerated source root.
- `benchmarks/__init__.py`, `benchmarks/evaluate_memory.py`: read-only,
  separately importable research A/B evaluator, never a product generator.
- `pyproject.toml`: include `benchmarks*` in Python wheel package discovery.
- `MANIFEST.sha256`: regenerated from real bytes; missing file now fails
  archive validation.
- `tests/test_release.py`: checks both source inventory and published ZIP.
- `tests/test_v08_benchmark.py`: the original four tests must be present and
  pass in a **freshly extracted release archive** as well as the checkout.

To verify locally:

```bash
python scripts/release.py --check
python scripts/release.py --output dist/AlphaResearch-v08-fixed.zip
python scripts/release.py --verify-archive dist/AlphaResearch-v08-fixed.zip
python -m unittest discover -s tests -p 'test_v08_benchmark.py' -v
python verification/v08_module_matrix.py --repo . --report-dir /tmp/v08-qa
```

No real BRAIN Simulation/Submit or trading gateway is connected by these
changes. Historic frozen runs must be kept on their matching original code
snapshot; never rewrite a historical plan hash to force a resume.
