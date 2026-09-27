# Eval lifecycle refactor evidence

- `evaluate.py` no longer reads convenience JSON; skipped pilot/judge tiers are absent
  from the invocation result and manifest.
- The prediction adapter loads the pinned registered-model URI and sends
  `custom_inputs.persist=false`. It fails closed unless the packaged response reports
  `custom_outputs.write_result.persisted=false`.
- No live evaluation or registration was executed for this refactor, so this work wrote
  no `adjudications` or `adjudication_decision_records`. Unit tests exercise the packaged
  request/output persistence contract without a Lakebase connection.
- Promotion mutations were exercised only with a mocked registry client. The live
  `@prod` alias was not changed.
