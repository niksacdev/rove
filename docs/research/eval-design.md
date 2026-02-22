# ROVE Evaluation Design
## What Does "Eval" Mean for Robotics?

**Author**: Principal ML Scientist
**Date**: 2026-02-21
**Status**: Authoritative design document — implement from this

---

## The Core Tension

Before defining anything, state the fundamental problem plainly: robotics evaluation is harder than LLM evaluation because the ground truth is *physical*, not *linguistic*.

When you evaluate GPT-4o on a question-answering task, you compare text output to a known-correct text string. The ground truth is unambiguous and free. When you evaluate a VLA on a pick-and-place task, ground truth means "did the object end up in the right place?" — which requires a simulator or a real robot, human observation, and careful criteria for what "right" means. Ground truth is expensive to produce, noisy, and stage-dependent.

This shapes every design decision below.

---

## 1. What Is an Eval Dataset for Robotics?

### 1.1 What ROVE Is NOT Doing

ROVE is not a benchmark tool and not a training tool.

Benchmarks (LIBERO, Open X-Embodiment, RLBench) evaluate individual models against fixed curated task sets to measure population-level average performance. ROVE evaluates complete agent pipelines — specific VLM+VLA+LLM combinations — against a specific user-defined task. The distinction is:

- **Benchmark**: "How does SmolVLA perform across 130 LIBERO tasks on average?"
- **ROVE**: "For MY bin-picking task with MY scene images, which agent pipeline configuration works best?"

ROVE is inference-only. It does not train, fine-tune, or modify models. It runs them in realistic robotics pipelines and measures the outcomes.

This means ROVE does not ship a dataset. ROVE accepts the user's task definition and generates evaluation records by running inference trials.

### 1.2 An Evaluation Dataset Is Generated, Not Curated

When a user runs `rove evaluate --evaluation pick_bracket`, ROVE generates a dataset by running N trials for each model combination. Each trial is one complete pipeline execution: perceive → ground → plan → execute → verify. The resulting dataset is the set of trial records.

This is fundamentally different from supervised ML evaluation, where you evaluate a model against a pre-labeled holdout set. In ROVE, the "dataset" does not exist before the evaluation runs. It is created by the evaluation itself.

### 1.3 What Goes INTO a Trial (Inputs)

Every trial starts with the same fixed inputs so results are comparable across model combinations:

```
Task specification (string)
  "Pick the red bracket and place it in bin A"

Initial scene image (bytes, JPEG, <750KB)
  The scene before any manipulation

Initial simulator state (deterministic seed)
  MuJoCo XML + object positions reproducible from the seed
  The same seed produces the same initial configuration every time

Proprioception (7-DOF joint state + gripper state)
  [j0, j1, j2, j3, j4, j5, j6, gripper]
  Read from the simulator at reset time
```

The same inputs are presented to every model combination in every trial. This is what makes the comparison fair.

### 1.4 Existing Dataset Formats (What ROVE Learns From)

**RLDS (used by Open X-Embodiment)**
Episodes in TFRecord files. Each timestep: `{observation: {image, wrist_image, state}, action: [7-DOF], reward, is_terminal, language_instruction}`. Strong format for training data. Too heavyweight for ROVE's use case — we do not need wrist cameras or reward signals at design time.

**LIBERO**
HDF5 files per task suite. Demonstrations as trajectory sequences. Grouped into suites by variation type (LIBERO-Spatial, LIBERO-Object, LIBERO-Goal, LIBERO-100). Binary success evaluated by PDDL goal predicates in simulation. This is directly usable: ROVE uses LIBERO tasks as the sim backend.

**JSONL (Azure AI Foundry compatible)**
ROVE's export format. Simple, text-based, one record per line. Compatible with `azure-ai-evaluation` SDK. This is what ROVE writes after running trials.

**ROVE's choice**: Generate JSONL internally, export Foundry-compatible JSONL. Do not use HDF5 or TFRecord — those are training data formats. Eval data should be human-readable and toolchain-agnostic.

---

## 2. What Does Ground Truth Mean?

This is the hardest conceptual question. There are four distinct types of ground truth in the ROVE pipeline, and they are not interchangeable.

### 2.1 The Four Ground Truth Types

#### Type 1: Sim Physics Ground Truth (Authoritative for full-pipeline runs)

The MuJoCo simulator knows the precise position and orientation of every object. At the end of an episode, LIBERO evaluates success using PDDL goal predicates — deterministic geometric checks:

```
LIBERO success check for "place object in bin":
  object.position ∈ bin.bounding_box
  object.velocity < ε   (object at rest, not still moving)
  gripper.state = open  (robot released the object)
```

This is **objective**, **free** (no human needed), and **reliable** (physics is deterministic given the same seed). It is also **conservative**: a task can look like a success visually but fail the geometric check, and vice versa (e.g., object placed correctly but still sliding at termination).

**Use for**: Any trial that runs the full pipeline with MuJoCo. This is the primary success signal.

#### Type 2: VLM Judge Ground Truth (Available but calibration-dependent)

The VLM verify step compares the initial scene image to the post-execution scene image and outputs `success: bool, confidence: float, reasoning: str`. This is a probabilistic judgment, not a geometric check.

VLM judge ground truth is valuable because:
- It works when there is no simulator (VLM-only mode, or future real-robot evaluation)
- It captures semantic success ("object is in the right area") not just metric success
- It provides human-interpretable reasoning

VLM judge ground truth is unreliable because:
- It is calibrated against the sim physics check only if we measure that calibration
- VLMs disagree with themselves across temperatures
- Small visual differences (lighting, angle) can flip the judgment
- LIBERO-PRO showed that even strong VLMs collapse under slight scene perturbations

**Use for**: All trials as a secondary signal. Track disagreement with sim physics as a first-class metric (judge calibration). Never use as a substitute for sim ground truth in full-pipeline mode.

#### Type 3: Per-Stage Process Ground Truth (Requires annotation or heuristics)

Does the scene description correctly identify all objects? Is the grasp plan physically reasonable? These are harder questions because there is no automatic checker.

For perceive:
- We can partially automate this: compare VLM-reported object list to LIBERO's known object list for the scene
- We cannot fully automate spatial relationship accuracy (VLM says "bracket is left of bin A" — is that correct? Only if we annotate ground truth spatial relationships)

For plan:
- Grasp plan quality (approach direction, strategy) has no automatic ground truth
- We can heuristically check: does the planned approach direction avoid obvious collisions? Is confidence reported accurately?

For ground (GroundingDINO bbox):
- Sim gives us exact object positions projected into image coordinates
- We can compute IoU between predicted bbox and true object bbox
- This IS automatable with the simulator

**Use for**: Research/diagnostic mode. Not the primary success signal. Used to diagnose WHERE a combination is failing.

#### Type 4: Human Annotation Ground Truth (Gold standard, expensive)

A human annotator reviews trial recordings and labels: was the task completed? Were intermediate steps reasonable? This is the gold standard but impractical for automated evaluation at scale.

**Use for**: Calibration dataset. Run 50 trials, have a human label outcomes, measure how well sim ground truth and VLM judge agree. Do this once per task type, not per run.

### 2.2 The Hierarchy

```
Sim physics GT (most reliable, automatic)
    ↓ disagreement measured as "judge_calibration"
VLM judge GT (secondary, automatic)
    ↓ disagreement measured as "annotation_drift"
Human annotation GT (gold, expensive — calibration only)
```

When sim is available: use sim physics as primary success, VLM judge as secondary.
When sim is not available (VLM-only mode): use VLM judge as primary, note the reliability limitation.
For calibration: run human annotation monthly on a 5% sample.

### 2.3 The Sim-to-Real Gap

LIBERO-PRO (2025) showed a brutal result: models that achieve 90%+ on standard LIBERO collapse to 0% when object positions shift by 0.2 sim units. This is the sim-to-real gap in miniature — even within simulation, small distribution shifts destroy performance.

Implications for ROVE:
- A high success rate on fixed-seed trials is optimistic
- ROVE must evaluate over multiple initial condition seeds, not just one
- The variation study workflow (`bracket_variations` in `rove.yaml`) is not optional — it is what separates deployable models from demo models

---

## 3. Scoring Framework

### 3.1 Primary Outcome Metric: Task Success Rate

**Formula**:
`success_rate = successful_trials / total_trials`

Where `successful_trial` = sim physics ground truth returned `True` for the complete pipeline execution.

**Example**: 3 VLMs × 3 VLAs = 9 combinations, 5 trials each = 45 runs.
Combination (gpt-4o + pi0): 4 of 5 trials succeed → `success_rate = 0.80`.

**Statistical validity**: 5 trials gives very wide confidence intervals (Wilson interval for 4/5 ≈ [0.29, 0.99]). This is by design for Phase 1 — the ranking is indicative, not statistically precise. See Section 3.7 for how many trials you actually need.

### 3.2 Per-Stage Metrics

These are diagnostic, not primary ranking signals. They tell you WHERE a combination is failing.

#### Perceive Stage

| Metric | Formula | Ground Truth Required |
|--------|---------|----------------------|
| `perceive_latency_ms` | Wall clock at adapter boundary | None (measured) |
| `object_recall` | Objects reported by VLM ∩ known objects / known objects | LIBERO object list (automatic) |
| `grounding_iou` | IoU(predicted bbox, true bbox from sim projection) | Sim object position (automatic) |
| `perceive_cost_usd` | Token count × cost config from rove.yaml | None (computed) |

`object_recall` requires knowing what objects are in the scene. LIBERO provides this metadata per task. For user-defined scenes without LIBERO backing, this metric is unavailable — document this explicitly.

`grounding_iou` uses GroundingDINO's predicted bounding box versus the true object centroid projected into image space from the simulator. Standard threshold: IoU ≥ 0.5 = "hit", else "miss".

#### Plan Stage

| Metric | Formula | Ground Truth Required |
|--------|---------|----------------------|
| `plan_latency_ms` | Wall clock at adapter boundary | None |
| `plan_confidence` | VLM self-reported confidence from GraspPlan output | None (self-reported) |
| `plan_cost_usd` | Token count × cost config | None |

No automatic quality ground truth for the plan itself. We collect `plan_confidence` as a calibration signal — if high plan confidence correlates with high trial success, the VLM confidence is meaningful. If not, it is noise.

#### Execute Stage

| Metric | Formula | Ground Truth Required |
|--------|---------|----------------------|
| `execute_latency_ms` | Total VLA inference + sim execution time | None |
| `vla_inference_ms` | VLA model inference time only | None |
| `sim_steps_to_completion` | Number of simulator steps until terminal | Sim (automatic) |
| `action_smoothness` | `1 - normalized_jerk` (see formula) | None (computed from actions) |
| `workspace_violations` | Count of action steps with any joint exceeding limits | Sim joint limits (automatic) |
| `execute_cost_usd` | VLA cost config (per_call or per_second) | None |

**Action Smoothness Formula**:
Jerk is the third derivative of position — rate of change of acceleration. High jerk = jerky, unsafe motion.

```python
def action_smoothness(actions: list[list[float]]) -> float:
    """
    Compute normalized action smoothness from predicted action chunk.

    actions: list of N steps, each a 7-DOF delta [dx, dy, dz, droll, dpitch, dyaw, gripper]
    Returns: float in [0, 1] where 1.0 = perfectly smooth

    Method: Log Dimensionless Jerk (SPARC metric from motor control literature)
    """
    if len(actions) < 4:
        return 1.0  # insufficient data, assume smooth

    import numpy as np
    a = np.array(actions)[:, :6]  # exclude gripper from smoothness calc

    # Finite difference derivatives (velocity, acceleration, jerk)
    vel = np.diff(a, axis=0)        # N-1 x 6
    acc = np.diff(vel, axis=0)      # N-2 x 6
    jerk = np.diff(acc, axis=0)     # N-3 x 6

    # RMS jerk per dimension, then mean across dimensions
    rms_jerk = np.sqrt(np.mean(jerk**2, axis=0)).mean()

    # Normalize by velocity magnitude to get dimensionless metric
    rms_vel = np.sqrt(np.mean(vel**2, axis=0)).mean() + 1e-8
    normalized_jerk = rms_jerk / rms_vel

    # Map to [0,1]: lower jerk → higher smoothness
    # Empirical calibration: normalized_jerk > 2.0 = very jerky, < 0.1 = very smooth
    smoothness = 1.0 / (1.0 + normalized_jerk)
    return float(np.clip(smoothness, 0.0, 1.0))
```

Note: For monolithic VLAs (pi0) that return action chunks directly, this works natively. For autoregressive VLAs (OpenVLA-OFT) that return one step at a time, collect the full execution trajectory then compute post-hoc.

#### Verify Stage

| Metric | Formula | Ground Truth Required |
|--------|---------|----------------------|
| `verify_latency_ms` | Wall clock at adapter boundary | None |
| `verify_confidence` | VLM self-reported confidence from VerificationResult | None |
| `judge_agreement` | `1 if vlm_success == sim_success else 0` per trial | Sim ground truth |
| `verify_cost_usd` | Token count × cost config | None |

`judge_agreement` is the most important verify metric. Track it per VLM across all trials. A VLM with 95% success rate but 60% judge agreement is systematically miscalibrated — it cannot tell you if the task actually succeeded.

**Judge calibration score** (aggregate across N trials):
`judge_calibration = trials where vlm_success == sim_success / total_trials`

Target: > 0.85. Below 0.70, the VLM verify output is unreliable as a success signal.

### 3.3 Efficiency Metrics

These are first-class ranking signals after success rate.

| Metric | Formula | Unit |
|--------|---------|------|
| `total_latency_ms` | Sum of all stage latencies per trial | ms |
| `p50_latency_ms` | Median across trials | ms |
| `p95_latency_ms` | 95th percentile across trials | ms |
| `total_cost_usd` | Sum of all stage costs per trial | USD |
| `cost_per_success_usd` | `total_cost_usd / success_rate` | USD |

`cost_per_success_usd` is the most practically useful cost metric. A combination with 40% success at $0.01/run costs $0.025/success. A combination with 90% success at $0.025/run costs $0.028/success. The more expensive model may be cheaper per outcome.

**Example from README leaderboard, annotated**:

```
gpt-4o + cogact-7b:
  success_rate: 0.92    # 46/50 trials
  p50_latency_ms: 1842
  cost_per_run: $0.021
  cost_per_success: $0.023   # = 0.021 / 0.92

qwen3-vl-8b + smolvla-450m:
  success_rate: 0.78    # 39/50 trials
  p50_latency_ms: 1203
  cost_per_run: $0.000   # both local, no API cost
  cost_per_success: $0.000   # free but ~15% fewer successes
```

### 3.4 Robustness Metrics (Variation Studies)

Only available when the evaluation defines multiple tasks or seeds (e.g., `bracket_variations` in rove.yaml).

| Metric | Formula |
|--------|---------|
| `robustness_mean` | Mean success rate across task variations |
| `robustness_std` | Std dev of success rates across variations |
| `robustness_min` | Worst-case success rate across variations |
| `degradation_ratio` | `1 - (min_success / max_success)` |

`degradation_ratio` = 0 means uniform performance across variations.
`degradation_ratio` = 0.8 means the model's worst case is 80% worse than its best case. This is the LIBERO-PRO finding in numeric form.

### 3.5 The Ranking Formula

ROVE ranks combinations by:

1. `success_rate` descending (primary — does it work?)
2. `p50_latency_ms` ascending (secondary — how fast?)
3. `cost_per_run` ascending (tertiary — how much?)

This ordering is opinionated and correct for production robotics. A model that works reliably at 3x the cost is better than a model that is cheap but unreliable. Latency beats cost because cycle time directly affects throughput.

The ranking should be re-sortable in the dashboard UI (user may care more about cost than latency for their use case), but the default ordering is fixed as above.

### 3.6 Monolithic VLA Handling

A monolithic VLA like pi0 or pi0.5 does perceive + plan + act in a single forward pass. It does not produce intermediate outputs for perceive and plan stages. This is a first-class design consideration, not an edge case.

**Design rule**: Treat NULL intermediate outputs as a valid state, not an error.

For a monolithic VLA trial:
- `perceive_output`: NULL (not produced)
- `plan_output`: NULL (not produced)
- `execute_output`: ActionPrediction (produced)
- `verify_output`: VerificationResult (produced)
- `sim_success`: bool (produced by sim)

The following metrics are NOT computed for monolithic VLAs:
- `object_recall` (no separate perceive stage)
- `grounding_iou` (no separate ground stage)
- `plan_confidence` (no separate plan stage)
- Stage-specific latencies for perceive/plan (latency is monolithic)

The following metrics ARE computed:
- `total_latency_ms` (the full execution time, attributed to execute stage)
- `action_smoothness` (from the action chunk)
- `judge_agreement` (verify still runs separately)
- `success_rate` (sim physics, same as any other combination)

**Fair comparison**: When comparing a full chain (VLM + grounding + VLA) vs. a monolithic VLA, the primary comparison must be `success_rate` and `total_latency_ms`. Per-stage metrics are diagnostic within each architecture type and cannot be cross-compared.

The dashboard must clearly mark which combinations are monolithic vs. full-chain, and filter/group accordingly.

### 3.7 Statistical Validity: How Many Trials Do You Actually Need?

This is the question ROVE founders will be asked by serious robotics engineers. The answer is not "run 5 trials."

**The math**: For binary success/fail with success probability p, the Wilson confidence interval width at 95% confidence is approximately `2 * sqrt(p(1-p)/n)` for large n. For p=0.80:

| Trials | CI Width | CI Range |
|--------|----------|----------|
| 5      | ±35%     | [0.45, 1.00] |
| 10     | ±25%     | [0.55, 1.00] |
| 25     | ±16%     | [0.64, 0.96] |
| 50     | ±11%     | [0.69, 0.91] |
| 100    | ±8%      | [0.72, 0.88] |

**ROVE's recommendation by use case**:

- **Screening** (which combinations are worth pursuing): 5 trials. Wide CI is acceptable — you're filtering out obvious failures.
- **Selection** (choosing a model for deployment): 25-50 trials per combination. CI width of ±11-16% is practical.
- **Certification** (statistically defensible claim): 100+ trials. Required for CI width < 10%.

Default of `trials: 5` in rove.yaml is correct for Phase 1 (mock adapters, fast). Real model evaluations should default to `trials: 25` or more.

**Report confidence intervals, not just point estimates.** The leaderboard should show:

```
gpt-4o + pi0:  88% success [95% CI: 80%-94%]  (50 trials)
gpt-4o + smolvla:  78% success [95% CI: 64%-88%]  (25 trials)
```

Use Wilson score interval for binary success rate. Python implementation:

```python
from scipy.stats import norm

def wilson_ci(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score confidence interval for binary success rate."""
    if trials == 0:
        return (0.0, 0.0)

    z = norm.ppf(1 - (1 - confidence) / 2)  # 1.96 for 95%
    p_hat = successes / trials

    denominator = 1 + z**2 / trials
    center = (p_hat + z**2 / (2 * trials)) / denominator
    margin = z * ((p_hat * (1 - p_hat) / trials + z**2 / (4 * trials**2)) ** 0.5) / denominator

    return (max(0.0, center - margin), min(1.0, center + margin))
```

**Significance testing for pairwise comparison**: Use paired bootstrap test (resample trial results with replacement, measure how often combination A beats combination B). Never use unpaired t-test on success rates — the binary outcome and small N make the normality assumption invalid.

```python
import numpy as np

def bootstrap_comparison(
    results_a: list[bool],  # trial success/fail for combination A
    results_b: list[bool],  # trial success/fail for combination B
    n_bootstrap: int = 10_000,
) -> dict:
    """
    Paired bootstrap test: is combination A significantly better than B?
    Returns p-value for H0: A success_rate <= B success_rate.
    """
    # This works even when len(results_a) != len(results_b)
    # (unpaired bootstrap)
    a = np.array(results_a, dtype=float)
    b = np.array(results_b, dtype=float)

    observed_diff = a.mean() - b.mean()

    count_a_beats_b = 0
    for _ in range(n_bootstrap):
        sample_a = np.random.choice(a, size=len(a), replace=True)
        sample_b = np.random.choice(b, size=len(b), replace=True)
        if sample_a.mean() > sample_b.mean():
            count_a_beats_b += 1

    p_value = 1.0 - count_a_beats_b / n_bootstrap

    return {
        "observed_diff": observed_diff,
        "p_value": p_value,
        "significant": p_value < 0.05,
        "n_bootstrap": n_bootstrap,
    }
```

With 5 trials per combination, almost no pairwise differences will be statistically significant. This is honest — show the CI, let the user decide.

---

## 4. The Eval Record Data Structure

This is the canonical format for a single trial record. This is what gets stored in SQLite and exported to JSONL.

### 4.1 Single Trial Record (Internal)

```python
from dataclasses import dataclass, field
from typing import Optional
import time

@dataclass
class StageResult:
    """Outcome of one pipeline stage."""
    stage: str                           # perceive | ground | plan | execute | verify
    started_at: float                    # unix timestamp
    latency_ms: float                    # wall clock, adapter boundary to boundary
    cost_usd: float                      # computed from token counts and cost config
    success: bool                        # did the stage complete without error?
    error: Optional[str] = None          # error message if success=False
    output: Optional[dict] = None        # serialized stage output (SceneAnalysis, etc.)
    metrics: dict = field(default_factory=dict)  # stage-specific computed metrics

@dataclass
class TrialRecord:
    """
    Single pipeline execution record.
    One trial = one complete perceive→ground→plan→execute→verify run
    for one VLM+VLA combination on one task.
    """
    # Identity
    trial_id: str                        # uuid4
    evaluation_id: str                   # parent evaluation session
    combination_id: str                  # f"{vlm_id}+{vla_id}"
    trial_index: int                     # 0-based within this combination

    # Configuration
    task: str                            # task description string
    vlm_id: str | None                   # None for monolithic VLA
    vla_id: str | None                   # None for vlm_only mode
    grounding_id: str
    sim_id: str
    is_monolithic: bool                  # True if VLA handles perceive+plan internally
    initial_seed: int                    # MuJoCo random seed for this trial

    # Timestamps
    started_at: float                    # unix timestamp
    completed_at: float                  # unix timestamp

    # Stage results (None if stage not executed)
    perceive: Optional[StageResult] = None
    ground: Optional[StageResult] = None
    plan: Optional[StageResult] = None
    execute: Optional[StageResult] = None
    verify: Optional[StageResult] = None

    # Ground truth signals
    sim_success: Optional[bool] = None   # physics engine verdict (authoritative)
    vlm_success: Optional[bool] = None   # VLM verify verdict (secondary)
    judge_agreement: Optional[bool] = None  # sim_success == vlm_success

    # Aggregate metrics (computed after all stages)
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    action_smoothness: Optional[float] = None  # None for monolithic or vlm_only
    workspace_violations: int = 0

    @property
    def primary_success(self) -> Optional[bool]:
        """The authoritative success signal. Sim when available, VLM judge otherwise."""
        if self.sim_success is not None:
            return self.sim_success
        return self.vlm_success
```

### 4.2 Combination Summary (Aggregate Across Trials)

```python
from scipy.stats import norm as _norm

@dataclass
class CombinationResult:
    """
    Aggregated result for one VLM+VLA combination across all trials.
    This is what appears in the leaderboard.
    """
    combination_id: str
    vlm_id: str | None
    vla_id: str | None
    is_monolithic: bool

    # Trial inventory
    total_trials: int
    completed_trials: int                # excludes trials that errored before completion
    error_trials: int

    # Primary outcome
    successes: int
    success_rate: float                  # successes / completed_trials
    ci_lower: float                      # Wilson 95% CI lower bound
    ci_upper: float                      # Wilson 95% CI upper bound

    # Latency (milliseconds)
    p50_latency_ms: float
    p95_latency_ms: float
    mean_latency_ms: float

    # Per-stage latency breakdown (None for monolithic)
    perceive_p50_ms: Optional[float]
    ground_p50_ms: Optional[float]
    plan_p50_ms: Optional[float]
    execute_p50_ms: Optional[float]
    verify_p50_ms: Optional[float]

    # Cost
    mean_cost_per_run_usd: float
    cost_per_success_usd: float

    # Quality diagnostics
    mean_action_smoothness: Optional[float]  # None for monolithic or vlm_only
    judge_calibration: Optional[float]       # judge_agreement rate
    mean_plan_confidence: Optional[float]    # None for monolithic
    workspace_violation_rate: float          # violations / execute_steps

    # Robustness (only if multi-task/multi-seed evaluation)
    robustness_std: Optional[float] = None
    robustness_min: Optional[float] = None
    degradation_ratio: Optional[float] = None
```

### 4.3 Foundry-Compatible JSONL Export

Each line in the export file is one trial, formatted for Azure AI Foundry `azure-ai-evaluation` SDK compatibility:

```jsonl
{
  "query": "Pick the red bracket and place it in bin A",
  "response": {
    "vlm_success": true,
    "confidence": 0.91,
    "reasoning": "The bracket is now clearly inside bin A boundaries. Gripper is open."
  },
  "context": {
    "combination_id": "gpt-4o+pi0",
    "vlm_id": "gpt-4o",
    "vla_id": "pi0",
    "trial_index": 2,
    "initial_seed": 42,
    "scene_analysis": {
      "objects": ["red_bracket", "bin_A", "bin_B"],
      "spatial_relationships": "red bracket is on the left shelf, bin A is center-right"
    },
    "grasp_plan": {
      "approach_direction": "top_down",
      "strategy": "precision_grasp",
      "confidence": 0.88
    },
    "execute": {
      "action_steps": 47,
      "action_smoothness": 0.83,
      "workspace_violations": 0
    },
    "latency_breakdown_ms": {
      "perceive": 1243,
      "ground": 312,
      "plan": 891,
      "execute": 2108,
      "verify": 987,
      "total": 5541
    },
    "cost_usd": {
      "perceive": 0.0082,
      "plan": 0.0071,
      "verify": 0.0063,
      "total": 0.0216
    }
  },
  "ground_truth": {
    "sim_success": true,
    "judge_agreement": true
  },
  "metadata": {
    "trial_id": "tr_8f2a1c9e",
    "evaluation_id": "eval_20260221_001",
    "task": "Pick the red bracket and place it in bin A",
    "rove_version": "0.1.0",
    "sim_backend": "mujoco-libero",
    "created_at": "2026-02-21T14:23:11Z"
  }
}
```

The `query`, `response`, `context`, `ground_truth` field structure is the Foundry schema. Everything inside those fields is ROVE-specific. Custom Foundry evaluators can be registered to compute domain-specific metrics from this format.

---

## 5. Comparison Methodology

### 5.1 Full Chain vs. Monolithic VLA

The architectural comparison between a full chain (VLM + grounding + VLA) and a monolithic VLA (pi0 doing everything) is the most important comparison ROVE enables. Do this correctly.

**What you can compare directly** (same unit, same measurement):
- `success_rate` — binary outcome, comparable
- `total_latency_ms` — wall clock, comparable
- `cost_per_run_usd` — USD, comparable
- `cost_per_success_usd` — USD, comparable
- `action_smoothness` — dimensionless, comparable
- `judge_calibration` — dimensionless, comparable

**What you cannot compare directly**:
- `perceive_latency_ms` for a monolithic VLA vs. a chained VLM (monolithic does not have this)
- `plan_confidence` for monolithic vs. chained (monolithic does not expose this)
- `object_recall` for monolithic vs. chained (same reason)

**Dashboard guidance**: Show "N/A" for metrics that monolithic VLAs cannot produce. Do not exclude monolithic VLAs from the leaderboard — exclude them from per-stage metric breakdowns only.

**Concrete example** from a real evaluation:

```
Task: "Pick the red bracket, place in bin A" — 50 trials each

Full chain (gpt-4o + pi0):
  success_rate: 88% [CI: 76%-95%]
  total_latency_ms: 5,541 (perceive:1243 ground:312 plan:891 execute:2108 verify:987)
  cost_per_run: $0.022
  action_smoothness: 0.83
  judge_calibration: 0.92

Monolithic (pi0 alone):
  success_rate: 84% [CI: 71%-93%]   ← statistically equivalent at 50 trials
  total_latency_ms: 2,180 (execute:1193 verify:987)  ← 2.5x faster
  cost_per_run: $0.010              ← 2.2x cheaper (only verify costs)
  action_smoothness: 0.87           ← slightly smoother (VLM chain adds noise)
  judge_calibration: 0.90

Interpretation: pi0 alone is within CI of gpt-4o+pi0 on success rate,
but 2.5x faster and 2.2x cheaper. The VLM chain does not add enough
value on this task to justify its overhead. Verdict: deploy pi0 alone.
```

This is the kind of concrete, defensible insight ROVE needs to produce.

### 5.2 Cloud vs. Edge Comparison

Cloud models (gpt-4o, high API cost, ~1200ms VLM latency) vs. edge models (phi-4-multimodal, $0 cost, ~400ms on Apple Silicon) are a key comparison for deployment decisions.

**Key insight**: Cost-per-success is almost always more informative than cost-per-run. A cloud model with $0.022/run but 92% success costs $0.024/success. An edge model with $0/run but 72% success costs $0/success but delivers 28% fewer completions — which may represent $X in throughput loss per hour.

Present the Pareto frontier: plot all combinations on (success_rate, total_latency_ms). The Pareto frontier shows which combinations are not dominated. Combinations on the frontier are the rational choices; combinations below the frontier are strictly worse than something else on at least one dimension.

### 5.3 Statistical Testing for Pairwise Comparison

When a user asks "is gpt-4o significantly better than qwen3-vl-8b on this task?", the answer requires statistics, not just point estimates.

**Protocol for pairwise comparison**:

1. Collect N trials for each combination (same N, same initial seeds for fairness)
2. Compute observed success rate difference: `Δ = success_A - success_B`
3. Run bootstrap test (function defined in Section 3.7)
4. Report: "A is better than B by X% [p=0.03, n=50 trials per combination]" or "No significant difference [p=0.31, n=25 trials]"

**With 5 trials**: Almost never significant. This is fine — report the point estimate with a wide CI and note "insufficient trials for significance."

**With 50 trials**: Can detect differences of ~15% absolute at p < 0.05.

**With 100 trials**: Can detect differences of ~10% absolute at p < 0.05.

---

## 6. Implementation Priorities

This section translates the above into what to build in what order.

### Phase 1 (Mock adapters — build now)

The mock adapters must produce realistic-shaped data to validate the evaluation pipeline. Required:

1. `TrialRecord` dataclass — implement exactly as specified in Section 4.1
2. `CombinationResult` dataclass — implement with `wilson_ci` built in
3. SQLite schema — one table per: evaluations, trials, stage_results
4. JSONL export — Foundry-compatible as specified in Section 4.3
5. Mock ground truth: mock sim returns `success: bool` drawn from `mock_success_rate` config
6. Wilson CI computation in leaderboard aggregation

Do NOT implement: `action_smoothness` (needs real actions), `object_recall` (needs LIBERO metadata), `grounding_iou` (needs sim projection), `judge_calibration` (needs real VLM judge).

### Phase 2 (Real sim — MuJoCo + LIBERO)

1. Sim physics ground truth: PDDL goal check via LIBERO task predicates
2. `judge_calibration`: compare LIBERO success bool to VLM verify bool
3. `action_smoothness`: compute from real action chunks
4. `workspace_violations`: check joint limits from MuJoCo model
5. `grounding_iou`: project object bbox from sim world → image coords
6. Multiple initial seeds: vary seed per trial to prevent memorization

### Phase 3 (Cloud models + statistics)

1. Cost tracking from real API token counts (not estimates)
2. Pairwise bootstrap comparison in CLI and API
3. Pareto frontier computation in leaderboard
4. Robustness metrics when multi-task evaluations run

### Phase 4 (Foundry integration)

1. Register ROVE's JSONL format with Foundry custom evaluators
2. `judge_calibration` as a custom Foundry evaluator metric
3. `action_smoothness` as a custom Foundry evaluator metric

---

## 7. What the Mock Adapters Must Simulate

The mock adapters are the primary testing tool for Phase 1. They must produce data shaped like real adapters would.

**Mock VLM** must return:
```python
SceneAnalysis(
    objects=["red_bracket", "bin_A", "bin_B", "workbench"],
    spatial_relationships="red bracket is on the left side of the workbench, bin A is center",
    task_relevant_observations="target object clearly visible and unoccluded",
    confidence=0.85,  # from mock_quality config
)

GraspPlan(
    approach_direction="top_down",
    strategy="precision_grasp",
    pre_grasp_offset=[0.0, 0.0, 0.15],
    confidence=0.82,
)

VerificationResult(
    success=True,  # drawn from P(success) = 0.85
    confidence=0.88,
    reasoning="Object is now clearly inside bin A boundary.",
)
```

**Mock VLA** must return action chunks shaped like real VLAs:
```python
ActionPrediction(
    actions=[
        [0.01, 0.02, -0.03, 0.001, -0.002, 0.001, 0.0],  # step 0: 7-DOF delta
        [0.02, 0.03, -0.04, 0.002, -0.001, 0.002, 0.0],  # step 1
        # ... 10 steps total (SmolVLA chunk size)
    ],
    confidence=None,  # flow matching has no natural confidence
    inference_time_ms=87.3,
)
```

**Mock Sim** must return:
```python
SimResult(
    success=True,                         # drawn from mock_success_rate AND VLA quality
    post_execution_image=<bytes>,         # 640x480 JPEG (can be placeholder image)
    terminal_step=47,
    info={"goal_satisfied": True, "object_in_bin": True},
)
```

The mock success should be correlated with mock_quality, not purely random. A mock_quality=0.85 should produce ~85% success rate across many trials. This validates the aggregation logic correctly.

---

## 8. The One Thing to Get Right

Every design decision above can be adjusted. But one thing must be right from the start: **the primary success signal is always sourced from the sim physics engine, never assumed from the VLM judge.**

The VLM verify output goes into `vlm_success` and is used to compute `judge_calibration`. It is never treated as the authoritative outcome signal when the simulator is running. The LIBERO-PRO findings make clear that even state-of-the-art VLMs miscalibrate under distribution shift. ROVE's credibility depends on the physics engine verdict being primary.

This means:
- `primary_success = sim_success` when `sim_success is not None`
- `primary_success = vlm_success` only in `vlm_only` mode
- The leaderboard ranks by `primary_success`, not `vlm_success`
- The export JSONL puts `sim_success` in `ground_truth`, not `response`

If you get this wrong, ROVE reports inflated success rates (VLMs tend to overreport success) and the entire evaluation is dishonest.

---

## Sources

Research that informed this design:

- [LIBERO: Benchmarking Knowledge Transfer for Lifelong Robot Learning](https://arxiv.org/abs/2306.03310) — task structure, HDF5 format, PDDL success criteria
- [LIBERO-PRO: Towards Robust and Fair Evaluation of VLA Models Beyond Memorization](https://arxiv.org/abs/2510.03827) — robustness collapse findings (0% under 0.2 unit perturbation), perturbation methodology
- [LIBERO-Plus: In-depth Robustness Analysis of Vision-Language-Action Models](https://arxiv.org/abs/2510.13626) — multi-dimensional perturbation framework
- [Open X-Embodiment: Robotic Learning Datasets and RT-X Models](https://arxiv.org/html/2310.08864v4) — RLDS format, 7-DOF action normalization
- [VLABench: A Large-Scale Benchmark for Language-Conditioned Robotics](https://openaccess.thecvf.com/content/ICCV2025/papers/Zhang_VLABench_A_Large-Scale_Benchmark_for_Language-Conditioned_Robotics_Manipulation_with_Long-Horizon_ICCV_2025_paper.pdf) — multi-dimensional evaluation, long-horizon task structure
- [Evaluating Uncertainty and Quality of Visual Language Action-enabled Robots](https://arxiv.org/abs/2507.17049) — uncertainty and quality metrics for VLA evaluation
- [Azure AI Foundry Evaluation SDK documentation](https://learn.microsoft.com/en-us/azure/ai-foundry/how-to/develop/evaluate-sdk) — Foundry JSONL schema
- [When +1% Is Not Enough: A Paired Bootstrap Protocol](https://arxiv.org/html/2511.19794v1) — statistical testing methodology
- [SmolVLA: Efficient Vision-Language-Action Model](https://huggingface.co/blog/smolvla) — flow matching architecture, performance vs. size tradeoffs
- [OpenVLA-OFT: Fine-Tuning Vision-Language-Action Models](https://openvla-oft.github.io/) — autoregressive VLA evaluation methodology, success rate comparison
