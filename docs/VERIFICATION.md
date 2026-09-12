# Configured verification

ROVE keeps its perceive, plan, act and verify stages. A strategy may use the existing
endpoint string or a structured stage with an endpoint and timeout:

```yaml
act:
  endpoint: robot-policy
  timeout_ms: 5000
verify:
  endpoint: object-in-target
  mode: precompute
  timeout_ms: 5000
  checks:
    - endpoint: contact-force
      role: constraint
      required: true
      timeout_ms: 1000
    - endpoint: robot-kinematics
      role: diagnostic
      required: false
```

Define `object-in-target` as an endpoint with `type: verifier`, `adapter: local_verifier`
and `capabilities: [verification]`. Its config identifies a Python function:

```yaml
config:
  entrypoint: project_checks:object_in_target
  revision: final-position-v1
  target_min_m: [0, 0, 0]
  target_max_m: [0.2, 0.2, 0.2]
```

The module must be importable in the same Python environment as ROVE (install your
project or include its directory in `PYTHONPATH`). The function receives a typed
`EvaluatorContext` and its endpoint config. It may be synchronous or asynchronous;
it returns `EvaluatorResult` or a dictionary with the same fields:

```python
from rove.models.verification import EvaluatorResult


def object_in_target(context, config):
    observation = context.episode.get("final_object_position_m")
    if observation is None:
        return EvaluatorResult(
            verdict="unknown",
            reasoning="No final observation supplied",
            evidence_quality="unknown",
        )
    # Validate evidence and apply your task's declared completion criteria here.
```

The complete example is `rove.evaluators.examples:object_in_target`. It checks a
supplied final object position against configured three-dimensional bounds. The
`force_limit` example compares supplied peak force with a declared limit. Neither
example controls a robot or generates new task observations from policy actions.
Replaying a fixed recorded observation measures the evaluator, not an improved policy.

## Evidence and authority

- `EvaluatorContext` contains the task, full structured pipeline outputs, available
  before/after images and episode evidence. Supply episode data through
  `ExampleData.extras.episode` (or `example.extras.episode` in a campaign task).
  Condition, intervention and recovery metadata remain in this evidence bag.
- A result contains `verdict` (`pass`, `fail`, `unknown`), `reasoning`,
  `evidence_quality`, `evidence_refs`, optional measurements and details. Known
  verdicts require evidence references and a declared quality. Numeric measurements
  have a name, unit, quality and optional value; unknown values remain unavailable.
- The configured task evaluator determines task completion. A failed **required
  constraint** vetoes success. Missing required constraints or unavailable required
  diagnostic evidence produce unknown. Optional checks never override the verdict.
- Required checks run before verification in both execution modes, even when the
  model never requests tools. Precompute runs optional checks too; agent-loop exposes
  them as `check_0`, `check_1`, etc. Each check result is cached within that trial.
- An LLM cannot supply authoritative configured-check results or masquerade as the
  local task evaluator. Its own confidence remains a model judgment, never reliability.
- A check endpoint uses `type: verifier` and `capabilities: [diagnostic_check]`.
  `adapter: forward_kinematics` exposes existing FK/dynamics as diagnostics only.
  It cannot be the task evaluator or a constraint. Detailed computed/estimated/unknown
  dynamics labels are preserved; its overall diagnostic label is conservative.

## Execution and compatibility

Local Python is trusted developer code. Each invocation runs in a child process
and is terminated on timeout/cancellation; it inherits campaign process grouping.
This is not an untrusted-code sandbox. Do not give evaluators credentials or device
access they should not use. A subprocess boundary does not make physical actions safe.

`timeout_ms` bounds a stage including its configured checks, model/tool calls and
semaphore wait. Check timeouts apply inside that overall budget. Other stage timeouts
bound adapter calls (simulator stepping remains inside the campaign attempt timeout).
Absent explicit settings, strategies use `defaults.timeout_per_step_ms`; legacy
per-strategy `latency_budget` values provide stage overrides. The global
`defaults.latency_budget` remains a UI target, not a hard deadline.

Existing string stages, `verify_mode` and `compute_dynamics` continue to work.
Conflicting mode declarations fail validation. Choose legacy `compute_dynamics` or
structured `verify.checks` when adding diagnostics, rather than combining both.
Local task evaluators use precompute; selecting agent-loop for one fails validation.
Legacy FK stage/event layout remains available for old configurations.

Reports retain raw task verdicts, final combined outcomes, required-check failures,
measurement units/quality and unresolved evidence. Supplied episode completion time
is separate from pipeline wall time. Unknown outcomes remain visible in the existing
pass@k/pass^k bounds. No universal weighted score is introduced.

## Versioning

Campaign snapshots include check configuration, aggregation-policy version and local
entry-module hashes plus declared revisions. Changing those prevents historical
comparison. Local source changes block campaign resume and mark affected attempts
unknown. Imported helper modules, remote model weights, external artifacts and the
complete operating environment are not automatically snapshotted. Pin/version those
in your project and increment the evaluator revision when dependencies or semantics
change. These records are provenance, not a hermetic environment image.

## Run the synthetic example

```bash
rove benchmark run examples/verification/campaign.json \
  --config examples/verification/rove.yaml
```

The nine attempts cover three fixtures: a pass, a required force-limit failure despite
successful placement, and an unknown due to missing final position. Optional FK
remains unknown when no supported robot trajectory/model is supplied. This is a
contract demonstration, not evidence of real robot performance.
