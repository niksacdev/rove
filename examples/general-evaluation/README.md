# Structured evaluations without robotics

This deterministic text example proves reuse of the evaluation infrastructure.
It is not a language-model benchmark. No image, URDF, model download, cloud account
or physics package is needed. Run from the repository root after installing ROVE.

Compare the original input with a whitespace/capitalization normalization strategy:

```bash
uv run rove trial run --executor text-example \
  --input examples/general-evaluation/case.json \
  --config examples/general-evaluation/config.json \
  --strategy original --strategy normalized

uv run rove benchmark run examples/general-evaluation/campaign.json \
  --config examples/general-evaluation/config.json
```

The first command records two standalone trials in ordinary history. The second
creates a six-trial campaign and writes HTML, JSON and CSV reports. `original`
fails the exact-match criterion; `normalized` passes. Repeating this deterministic
fixture demonstrates accounting, not statistical independence or model reliability.

Use `rove trial list`, `rove trial show ID`, `rove trial trace ID`,
`rove benchmark list` and `rove benchmark report ID` to inspect saved records.
Use `--store PATH` consistently to keep a study separate. Saved trials are also
readable in the existing browser inspector when it uses that same store.

The candidate receives only `id`, `task` and `inputs`. The grader receives
`reference` after candidate execution. The expected answer remains a private
managed asset; it is excluded from the candidate request and campaign report inputs.
Local host integrations are trusted Python code, not an adversarial sandbox.

To add a real model or agent, install an implementation of `EvaluationAdapter`
under the `rove.evaluations` Python entry-point group. Its `predict` method runs
the candidate and its `grade` method returns the typed assessment. See the
[core specification](../../docs/product/reusable-evaluation-core.md) for source
identity, evidence requirements and the current UI boundary.
