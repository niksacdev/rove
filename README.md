# ROVE

**An open-source workbench for inspecting robotics policy evaluations.**

ROVE helps you compare policy configurations on your tasks, trace failure
hypotheses to supporting evidence, and spot missing checks before trusting an
assessment. It connects model adapters, optional MuJoCo diagnostics, and a local
browser dashboard. This is an experimental OSS project for demonstrating and
improving evaluation engineering.

![ROVE dashboard](docs/images/rove-welcome.png)

## Try it locally

Python 3.12 and [uv](https://docs.astral.sh/uv/) are recommended. Run from the
repository root; the dashboard currently needs the source checkout's assets.
No cloud account or model download is needed for the mock demo.

```bash
git clone https://github.com/niksacdev/rove.git
cd rove
uv sync --frozen --python 3.12 --extra dev --extra data --extra kinematics
uv run --frozen rove serve
# Open http://127.0.0.1:5001
```

Choose a gallery image, select **Mock (Test)**, and click **Evaluate**. Inspect
stage outputs, evidence confidence, missing checks, and comparison results.
Mock outputs demonstrate the workflow; they do not measure a robot's ability.

For a shareable, seeded example without opening the dashboard:

```bash
uv run --frozen python scripts/demo.py --output data/output/demo.json
```

The demo uses fresh seeded mock adapters and produces a JSON evidence report.
Latency and timestamps vary; mock actions and judgments repeat for the same seed.

For repeated trials, open **Benchmarks** in the dashboard or run:

```bash
uv run --frozen rove benchmark run examples/benchmarks/mock.json --config examples/benchmarks/rove.yaml
```

This produces offline HTML, JSON and CSV reports with pass@k, pass^k, task
outcomes and comparable history. See [benchmark configuration and scoring](docs/BENCHMARKS.md)
for seed support, unknown outcomes, cancellation and resume behavior.

## What you can inspect

- VLA action generation separately from later perception, planning and judging.
- Side-by-side strategy outputs, stage timing, and failure hypotheses.
- Input/config fingerprints, selected models, and dependency versions.
- Joint limits, modeled contacts, endpoint trajectories and singularity diagnostics
  when an appropriate robot model and state are available.
- Explicit `hard`, `estimated`, and `unknown` evidence with missing-check reasons.
- Action-space and robot-embodiment metadata, including approximate Jacobian IK.

`hard` means computed for the supplied model, state and action interpretation.
It is not proof of real-world execution. Default initial states and approximate
IK produce estimated evidence. Missing collision geometry produces **unknown**,
not “no collision.” Torque and payload feasibility remain **not computed** until
ROVE has a validated timing, inertia and actuator-mapping contract.

A VLM's success judgment is not measured task success. Stage attribution is a
heuristic debugging hypothesis, not a demonstrated causal root cause. Perception
and planning diagnostics do not control VLA actions in the parallel pipeline.
There is no real closed-loop simulator integration yet; `mock-sim` is a stub.

## Connect your models

`rove.yaml` declares endpoints and strategies. The mock strategy works out of the
box. Other configurations are examples and require your own running endpoints,
credentials and compatible checkpoints.

- OpenAI-compatible vision endpoints: configure provider URL and model ID.
- Azure: install `--extra azure`; set credentials through the environment and
  `AZURE_AI_FOUNDRY_ENDPOINT` for your own Foundry project.
- LeRobot policies: experimental `--extra smolvla` has unresolved upstream dependency
  advisories (see SECURITY.md); it is outside the supported setup. Consult adapter support and
  checkpoint requirements before downloading weights. These are not exercised
  by the lightweight CI suite.

Use `/docs` for the implemented HTTP API. `rove serve` and `rove benchmark` are supported CLI commands;
there is no published-package guarantee, batch `rove evaluate` command, or
`rove.evaluate()` convenience function yet.

## What a useful comparison needs

Use the same episode inputs, robot embodiment, action convention, initial state,
model revisions and runtime conditions. Save the report alongside these inputs.
A recorded seed alone does not make remote models deterministic; the dashboard's
seed is metadata unless the adapter explicitly applies it. Alias-only endpoint
names do not pin hosted model revisions.

The bundled frames are small demonstration samples, not a representative benchmark.
Before making performance claims, add held-out episodes, independent outcome
labels, repeated trials and uncertainty estimates. Current tests establish
software behavior and evidence handling, not robotics model quality.

## Development

```bash
uv run --frozen pytest -q
uv run --frozen python -m compileall -q src
node --check frontend/app.js
```

CI runs the mock/API and MuJoCo regression suite, static checks, a dependency
audit, and a secret scan. Contributions should include a failing case and explain
which evidence supports the resulting claim. See [CONTRIBUTING.md](CONTRIBUTING.md)
and [SECURITY.md](SECURITY.md).

The server binds to loopback and rejects foreign Host/Origin headers. It has no
authentication and is intended for one local user. Evaluation inputs and reports
remain on disk; review them before sharing. See SECURITY.md for data handling.

## License

ROVE's original software is [MIT](LICENSE). Bundled research assets retain their
upstream terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Design notes
under `docs/` include historical proposals and may describe unimplemented work;
this README and executable tests describe the supported release behavior.

### Configured task verification

Use [configured verification](docs/VERIFICATION.md) to provide a task evaluator,
required constraints and optional FK diagnostics through the existing verify stage.
Results carry measurements, evidence quality and evaluator versions into campaign reports.
