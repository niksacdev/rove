# Repeated pipeline evaluations

ROVE campaigns repeat a saved task suite against selected pipeline configurations and produce local reports. Open **Benchmarks** from the dashboard, select tasks and configurations, set a repeat count, and choose **Run campaign**. The page shows the attempt budget before starting. Configured cloud endpoints may incur charges.

Use **Save configuration** to download the exact task inputs and seed list as JSON. **Load a saved configuration** restores them. CLI users can supply JSON or YAML. Reports are available as self-contained interactive HTML, JSON with attempt evidence, and CSV with one row per attempt.

## Try a synthetic campaign

From a source checkout with its dependencies installed:

```sh
rove benchmark run examples/benchmarks/mock.json --config examples/benchmarks/rove.yaml
rove benchmark list
rove benchmark report CAMPAIGN_ID
rove benchmark resume CAMPAIGN_ID
```

The example compares two synthetic verifier quality settings over two tasks and five repeats. It makes no external model calls. The deliberately different quality settings demonstrate metric behavior; they do not compare real models. For actual comparisons, use the same evaluation criteria and verifier across configurations.

The default store is `.rove/benchmarks/campaigns.sqlite3`; CLI exports go to `.rove/reports/`. Both are ignored by Git and outside the dashboard's public `/data` directory. Endpoint settings are saved privately for replay and omitted from report exports. Reports include task instructions and model outputs: inspect that content before sharing it.

## Metrics and missing evidence

For each task and fixed configuration, let **n** be its planned repeat count and **c** its observed successes:

| Measure | Calculation | Meaning |
| --- | --- | --- |
| pass@k | `1 - C(n-c,k) / C(n,k)` | At least one success in k attempts |
| pass^k | `C(c,k) / C(n,k)` | All k attempts succeed |
| Cross-model coverage | Tasks with at least one observed success across selected configurations / tasks | Hindsight coverage at the disclosed total attempt budget |

Campaign metrics average the per-task estimates with equal task weights. A point estimate requires all planned attempts for that task to have valid verdicts. Errors, timeouts, interrupted attempts, invalid verdicts and pending work remain **unknown**. Dotted curves show the lower/upper bounds obtained by treating unknowns as failures/successes; these bounds are not confidence intervals. Values with `k > n` are unavailable, never extrapolated.

Task-level pass@1 intervals use the 95% Wilson method under independent Bernoulli attempts. Correlation between episodes, changing providers and imperfect judges limit that assumption. Neither a perfect sample nor high model-reported confidence establishes reliability outside the tested configuration. The higher-k estimates describe exchangeable repeated trials, not a guarantee about a continuous deployment streak.

The current grading source is the existing **verify stage**. Campaigns containing any mock endpoint are labeled `synthetic_or_mixed`; otherwise they are labeled `model_judgment`. The report does not promote either label into observed physical task correctness. Verifier parsing failures are invalid verdicts rather than task failures. Changing verification adapters remains a separate stage-configuration feature.

## Isolation, resets and reproducibility

Each attempt runs the existing configured pipeline in a fresh child process, with fresh adapters and simulator state. Existing simulator reset behavior is retained. One attempt runs at a time per campaign, interleaving configurations by seed. This intentionally trades throughput and model-loading cost for isolation; real local models reload each attempt. Avoid running multiple heavy campaigns concurrently on limited hardware.

Seeds are applied to supported mock adapters. Other adapters are explicitly labeled `unsupported` in attempt evidence; assigning a seed alone cannot make a remote provider or unseeded policy reproducible. Endpoint/model identities, declared revisions, pipeline configuration hashes, task inputs, runtime versions, source/prompt and bundled URDF hashes accompany results. Model revisions are declared rather than independently resolved; mutable hosted aliases and externally changed weights remain a reproducibility limitation. Existing provider-internal retries are not separately counted as trials.

An attempt is written before invoking its worker. Cancellation terminates the worker process group. A process/file lock prevents simultaneous execution of the same campaign. After a crash, in-flight attempts become unknown and are never silently rerun; resume runs only untouched attempt slots. Completed verdicts are retained. Start a new campaign to retry failed or cancelled attempts. Resume rejects runtime changes.

Campaign workers and locks currently support **macOS and Linux**. They provide process isolation, not a sandbox for untrusted adapters, and do not add physical robot control.

## Trends and evidence

Historical curves require matching task inputs, suite version, seeds, timeout, grading source, evaluator settings, simulator and runtime. The model/configuration revision is the comparison axis. Incompatible historical series are counted and excluded; a single campaign naturally produces only one historical point. Changing a shared endpoint that also supplies verification changes the evaluator and therefore breaks comparability.

Each report contains task outcomes, verification coverage, individual attempt evidence and model/configuration identity. Task labels and model output are escaped before rendering. Confidence from the model is retained only inside supporting evidence and never used to calculate pass metrics. Cost is not currently measured by the campaign runner; missing cost data is not represented as zero.

## API and Python

The same engine serves the CLI and local API:

| Route | Purpose |
| --- | --- |
| `POST /api/campaigns` | Validate a `CampaignSpec` and start a campaign |
| `GET /api/campaigns` | List saved campaigns |
| `GET /api/campaigns/{id}` | Status, provenance and metric summary |
| `POST /api/campaigns/{id}/cancel` | Stop the local worker or request stop at the next boundary |
| `POST /api/campaigns/{id}/resume` | Continue untouched slots |
| `GET /api/campaigns/{id}/report?format=html` | Interactive report; also accepts `json` or `csv` |

The API inherits the dashboard's localhost and same-origin restrictions. For Python integrations, use `CampaignSpec`, `prepare`, `CampaignStore`, `run_campaign`, and `report_data` from `rove.benchmarks`. The fixed pipeline and existing strategy fields are preserved.

## Statistical references

- [HumanEval pass@k estimator](https://github.com/openai/human-eval/blob/master/human_eval/evaluation.py)
- [tau-bench pass^k estimator](https://github.com/sierra-research/tau-bench/blob/main/tau_bench/run.py)
- [Plotly standalone HTML export](https://plotly.com/python/interactive-html-export/)
