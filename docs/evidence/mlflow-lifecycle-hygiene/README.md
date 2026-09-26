# MLflow lifecycle hygiene: trace root I/O + versioned promotion gate

Two coupled changes that make the agent's traces render and make evaluation a
versioned, comparable, governed process. Scope is limited to
`agent/src/agent.py` (+ its test) and `eval/`. `agent/src/authorities.py` has an
empty diff vs `main` (money math untouched).

## Part 1 — Root-span trace I/O

`ClaimsAdjudicationAgent.adjudicate()` opened a root `adjudicate` AGENT span but
never set root-level inputs/outputs, so traces rendered null request/response in
the MLflow UI. The fix sets, on that root span:

- **inputs** = the incoming claim (`root.set_inputs({"claim": claim})`), and
- **outputs** = the final, invariant-corrected recommendation
  (`root.set_outputs({...})`),

plus a compact `mlflow.update_current_trace(request_preview=..., response_preview=...)`
for the trace-list UI (the payloads are not chat-shaped). MLflow 3 derives the
trace's request/response from the root span's inputs/outputs, so future traces now
carry non-null request/response.

`agent/tests/test_agent.py` runs an adjudication with the DB, deterministic core,
and LLM step stubbed (persistence disabled), then fetches the trace via
`mlflow.get_trace(...)` and asserts the root span carries non-null inputs AND
outputs (claim in, recommendation out), and that `trace.data.request`/`response`
are non-null.

**This does NOT retroactively fix existing traces** — request/response are written
at trace-creation time, so only adjudications run after this change render with
populated root I/O.

## Part 2 — Versioned candidate → compare-vs-@prod → alias promotion

- **Version linkage.** `eval/src/evaluate.py` now tags every eval run with
  `candidate_version` (the UC registered-model version under test), passed via
  `--candidate-version` (default: `AGENT_MODEL_VERSION` env, else `MODEL_VERSION`)
  and also exported as `AGENT_MODEL_VERSION` so the agent stamps the same version
  on its decision records and trace attributes. The run — and its traces —
  therefore reference the exact UC version being scored.
- **Local JSON demoted.** The MLflow run and its logged metrics are the
  authoritative record. `eval/evidence/` is now clearly labeled as regenerated
  convenience snapshots (`eval/evidence/README.md`, plus `record_of_truth: "mlflow"`
  and `convenience_artifact: true` in `run-manifest.json`). `eval/README.md` gained
  a "System of record" section.
- **Gated promotion (`eval/src/promote.py`).** `promote_if_beats_prod` resolves the
  current `@prod` version (`get_model_version_by_alias`), pulls both versions'
  release-gate metrics from their eval runs via `mlflow.search_runs`, and moves the
  `@prod` alias to the candidate ONLY if it wins the gate:
  1. every money-safety invariant (`no_payable_duplicate`, `amount_matches_authority`,
     `verdict_matches_eligibility`, `citations_in_resolved_policy` — threshold 1.0)
     passes, AND
  2. `verdict_exact_match` / `disposition_exact_match` / `amount_matches_gold` are
     not worse than current `@prod`.

  If it does not win, the alias is unchanged and the reason is reported. Promotion
  is **explicit, gated, and logged** — never a side effect of an eval run. The CLI
  defaults to a dry-run report; `--promote` (fe-bar profile only) is required to
  move the live alias.

`eval/tests/test_promote.py` covers the pure gate logic and the promote
orchestration with `search_runs` and the registry client mocked. **The tests never
move the live `@prod` alias** and make no workspace calls.

## What was NOT done (by design / guardrails)

- No live evaluation was run; the live `@prod` alias was not moved; the registry was
  not mutated.
- No serving endpoint created. No changes to `lakebase/src/*` or `pipelines/*`.
- `mlflow.set_active_model` was grounded (signature confirmed) but not wired in: the
  `candidate_version` run tag names the UC registry version directly and needs no
  logged-model id. It is noted in `eval/README.md` as the complementary option.

See `gates.txt` for the exact commands and results.
