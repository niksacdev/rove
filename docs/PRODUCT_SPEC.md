# ROVE Product Specification

**Version:** 1.0
**Updated:** 2026-09-14
**Status:** Current product vision and requirements. Capability status is explicit below; proposed features are not shipped functionality.

## Current direction: reusable evaluations

ROVE preserves its robotics workbench while separating reusable Trials, Campaigns,
evidence, grading, baselines and history from domain-specific execution. A case
supplies concrete inputs; a strategy defines the candidate configuration; a trial
records one attempt; a campaign repeats selected comparisons under frozen conditions.

The [reusable evaluation core](product/reusable-evaluation-core.md) is implemented
for structured-input CLI trials/campaigns and saved-result inspection. Robotics
remains the default integration and current case-authoring UI. This is an OSS
foundation for future evaluation uses, not a claim of a complete general-purpose
product or a deployed cloud service. The separate
[ABC bimanual integration](product/abc-pi05-comparison.md) implements simulator
episodes; GPU inference and model performance remain unverified. The original robotics vision and
personas below remain the specification for the preserved robotics integration.

## 1. Core vision

**ROVE: Robot Observation & Vision Evaluation.**

**ROVE evaluates robotics agent pipelines on your task.**

Compare the same task, image and robot description across a chosen set of strategies to get useful direction on model, agent and pipeline choices. Start with an immediate side-by-side evaluation, then use durable campaigns to repeat trials, establish a baseline and measure improvement under consistent conditions. Inspect failed or uncertain outputs before deciding what to change.

The unit being assessed is a configured robotics agent pipeline: its models, prompts, stage settings, tools and applicable action components. A strategy can combine vision-language reasoning, tool-using agents and action policies such as VLAs through supported adapters. An agent is a running system, while VLM and VLA describe model capabilities; these are not mutually exclusive choices. Perception or planning assessments do not require every action or simulation stage. The evaluation stays anchored to the customer's robotics task and business outcome.

ROVE is an inference and evaluation workbench. Explicit standalone preparation examples can fine-tune models before evaluation; trials and campaigns never start training implicitly. ROVE does not choose a deployment on the customer's behalf. It records outputs, grades and evidence so a person can make that decision. Human review can assess scene understanding and plan quality; claims about physical task completion require associated episode outcome evidence.

## 2. Target users

The three founding personas remain the product foundation, with the platform role broadened beyond one cloud provider.

### Persona 1: Robotics Researcher

Works at a robotics company, academic lab or AI lab on manipulation tasks, often using Python, simulators and VLA frameworks.

**Need:** Compare complete configurations on a task suite, understand variability, and attribute a difference to a controlled change rather than changed test conditions.

**ROVE journey:** Import cases, run repeated trials, inspect traces and measurements, preserve a baseline, and compare an ablation with a complete configuration diff. Existing CLI/API campaigns provide the repeated-trial foundation; explicit baseline references and component comparisons are implemented.

**Value:** Less evaluation scripting; clear case-level evidence, uncertainty, missing observations and reproducibility limits.

### Persona 2: ML Engineer on a Manipulation Team

Builds a product such as bin picking, assembly or kitting, with requirements for completion, cycle time, human intervention and operating constraints.

**Need:** Assess the team's models or agents on a customer's data, including customers who have images and tasks but no labeled evaluation dataset.

**ROVE journey:** Onboard the data, define success, run an initial baseline, ask a subject-matter expert (SME) to review cases and outputs, save a versioned case collection, and compare a candidate against the same requirements. The local review and immutable dataset services are implemented; the revised campaign workspace is implemented locally with regression coverage.

**Value:** A reusable customer evaluation set and an evidence-backed explanation of which tasks improve, fail or still need assessment.

### Persona 3: Platform / AI Infrastructure Team

Provides model endpoints, agent runtimes and evaluation infrastructure to development teams across local and hosted environments. Azure is one integration example, not the definition of this persona.

**Need:** Connect supported endpoints, preserve model/configuration identity and make results usable across tools without coupling the evaluation to one provider.

**ROVE journey:** Configure adapters and strategies, check compatibility, inspect runtime/model/tool versions and traces, share versioned evidence, and integrate external analytics when required.

**Value:** Reusable integration contracts and traceable results. PostgreSQL, Fabric/Databricks connectors, MCP services and hosted collaboration are not implied by this persona; see the capability table and current implementation.

An SME is a collaborator in these workflows, not a fourth replacement persona. A local reviewer identity records attribution without claiming enterprise authentication.

## 3. Concepts and terminology

Use [Anthropic's evaluation terminology](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) for tasks/test cases, trials, graders, traces and outcomes. Campaign is ROVE's organizational concept, not a term attributed to that guidance.

| Concept | Meaning |
| --- | --- |
| Task | The robotics objective or instruction, retained as a field of a case rather than another persisted container. |
| Case / test case | A concrete observation, task, relevant environment/robot conditions and expected outcome. Several cases can test the same task under different conditions. |
| Trial | One attempt at one case using one strategy. For one case, running three strategies once creates three trials. Pipeline stages belong to the same trial; regrading an output does not create a new attempt. |
| Grader | Logic or human review that assesses a declared aspect of the result. ROVE's configured verify stage hosts task evaluators, required constraints and diagnostics. |
| Trace | The recorded outputs, calls, observations and intermediate results available for an attempt. A predicted action trajectory is not proof that actions executed. |
| Outcome | The resulting state or artifact being assessed. Plan quality, an agent decision and observed robot completion have different evidence requirements. |
| Suite / dataset revision | A selected collection of case versions. A frozen reviewed dataset also pins annotations, rubrics and reference-output links. |
| Strategy | A reusable named configuration of stages and endpoints. Its mutable name is not sufficient identity for old results. |
| Campaign | A named evaluation of cases, strategies and repetitions. **Run campaign** executes its trials; **Review results** reads their saved evidence. |
| Baseline | A saved reference to one completed campaign strategy and its frozen assessment snapshot, used for a later comparison. This is a role for an existing result, not a separate execution. |
| Provenance | Configuration, input, version and evidence identity attached to records; not another navigation level. |

Keep Cases, Trials and Campaigns as the working vocabulary. Saved history belongs under Results. Dataset revisions appear as versioned case collections managed with cases. Do not introduce overlapping Experiment or Session entities. Existing evaluation IDs remain compatibility/grouping identifiers where needed.

```mermaid
flowchart TD
    C[Case versions] --> D[Dataset or suite]
    S[Strategy configuration] --> T[Trial: one attempt]
    D --> T
    T --> E[Trace and outcome evidence]
    E --> G[Grading results]
    T --> P[Campaign]
    G --> R[Report]
    P --> R
    P --> V[Baseline and candidate lineage]
```

This is the target conceptual model. Independent quick and campaign trials now share durable identities, frozen configuration and history. Frozen dataset revisions, selected reviews and baseline references are now implemented locally.

## 4. Primary customer workflow

```mermaid
flowchart TD
    I[Customer images and tasks] --> M[Map inputs and check readiness]
    M --> O[Define success and expected measures]
    O --> S[Connect supported strategy]
    S --> B[Small baseline campaign]
    B --> H[SME review when needed]
    H --> D[Save reviewed case collection]
    D --> A[Change a component and compare]
```

This local workflow is designed to deliver a useful first report before requiring a large benchmark or external analytics platform. Input readiness distinguishes whether a case can run from whether its requested outcome can be graded. An image-to-plan agent should not be asked for joint state or a URDF unless its assessment needs them.

The first report shows accepted/completed, failed and unknown cases; representative failures link to evidence. It shows what is unreviewed or unmeasured. A configurable pilot checks data and grading before the full campaign, with attempt count and available budget information shown before launch.

Home offers **New trial** for an immediate comparison and **New campaign** for a
prepared case collection. Both create durable trials through the same underlying
services. Saved work is browsed through peer **Trials** and **Campaigns** destinations;
Settings contains reusable configuration and assistant setup.

### Trial-first entry and guided improvement

The latest journey starts in chat with a task, image, optional URDF and a selected
set of strategies. Each strategy produces its own saved trial, giving the robotics
team an immediate comparison before repeated campaign evaluation. **Add to campaign** carries its case,
robot input and selected strategy into the campaign workflow. Customers can add cases
and strategies, review three to five editable suggested criteria, then **Review and
run**. Completion opens Results automatically. Saved exploratory trials retain lineage
but are not inserted into fresh campaign reliability denominators.

From Results, **Set as baseline** preserves a selected campaign strategy's assessment
snapshot. From the campaign browser, **Improve** opens the populated workflow with
recommendations grounded in recorded low or unresolved outcomes. A component ablation
keeps cases and scoring fixed; expanding cases changes assessment conditions and must
not be presented as a controlled improvement. The entry/handoff/refinement is implemented with local regression coverage for
source inputs, managed robot assets, snapshot strategies and exact baseline comparison.
Browser verification completed the chat-to-improvement loop with a retained Panda
URDF, three edited/template criteria, two three-trial mock campaigns and an exact
baseline comparison. Conditions matched with zero component changes and outcomes
still pending review; this was a lineage check, not evidence of improvement. Cases
and criterion editing were usable at 390px. Live providers and hardware were excluded. See
[ADR-027](architecture/ADR-027-trial-to-campaign-improvement.md).

### Scripted comparison and iteration history

The same meaningful evaluation operations are available through supported commands:
immediate multi-strategy trials; case, criteria and review management; campaigns,
baselines, ablations, reports and timeline; strategy/configuration discovery and
optional confirmed assistance. These commands share the existing engine and assessed
records. See the [capability table and tested boundaries](product/cli-ui-parity.md).

An **evaluation timeline** connects campaigns through validated source snapshots or
exact baseline references. Each iteration shows its cases, strategy versions, success
metrics, outcome coverage and latency. Case or scoring changes remain useful history
but do not establish a controlled improvement. Timeline scores reflect current
assessments as of viewing; exact baseline comparisons retain the frozen assessment.
Mock evidence is labelled. Separate studies are not grouped by similar names.

```mermaid
flowchart LR
    A[Compare task + image + robot across strategies] --> B[Save campaign recipe]
    B --> C[Repeated trials and evidence]
    C --> D[Freeze chosen baseline assessment]
    D --> E[Revise strategies, cases or criteria]
    E --> F[Run next linked campaign]
    F --> G[Timeline: changes, outcomes and comparability]
    G --> E
```

The [CI example](../examples/ci/README.md) restores approved study state, records a
commit label, emits HTML/JSON/CSV and enforces declared targets. State and reports are
separate artifacts; history never depends on an assumed persistent runner. Local mock
CLI tests and a two-iteration desktop/390px browser timeline verify these paths. They
do not establish live-provider, GitHub-hosted workflow or hardware performance.

### Navigation and workspace design

The current journey is [specified in one place](product/user-journey.md):
**Trials** and **Campaigns** are record browsers; **New trial** and **New campaign**
create drafts. Settings is a separate right-hand gear. A quick comparison follows
**Case → Configure → Review & run → Results**, retaining task conversation, image,
optional URDF, shared strategy selection and explicit execution. Results preserves
All strategies and exact trial/trace inspection.

Campaigns extend the same setup through **Cases → Configure → Success metrics →
Run → Review & improve**. A persistent header shows name, attempts per case and
case × strategy × attempt total. Draft setup is editable; saved setup comes from
its recorded specification, including exact seed identities. Improve creates a
linked editable copy. The shared strategy table presents configured stages and
endpoints; readiness feedback is inline and does not imply live model quality.

Cases combines Add new, Select existing and Import cases with a bounded image/task
library. Datasets are saved case collections inside this workflow, not an additional
execution concept. Source labels remain separate from candidate input; populated
expectations and assistant suggestions never become SME judgments automatically.

Success metrics distinguishes each trial's **Pass condition** and **Checked by**
from automatically calculated report statistics. Manual checks need a recorded review;
configured checks need a verifier and suitable evidence. A textual description is not
executable scoring logic. Technical settings retains contracts, targets and annotation
bindings. Generated criteria and model-authored summaries have explicit provenance.

Run shows each strategy's execution and stage progress. Results begins with outcomes,
strategy charts and optional evidence-grounded interpretation, followed by inspection
and scoring/baseline details. A fresh draft shows No results yet and Go to Run rather
than another campaign picker. Completion, acceptance, failure and missing assessment
remain distinct. Setting a baseline, opening history or preparing an improvement never
starts another trial. Unsaved-work navigation and locked execution inputs preserve the
reviewed observation; [ADR-032](architecture/ADR-032-trial-review-and-navigation-state.md)
records the execution boundary.

Saved collections preserve membership and selected reviews in new revisions. A new
candidate strategy preserves its source and shows an exact change preview before
use. Case or rubric changes remain visible in the timeline and can invalidate a
controlled comparison. See [ADR-031](architecture/ADR-031-shared-evaluation-workspace.md)
and [ADR-033](architecture/ADR-033-isolated-vla-runtime-and-trial-inspection.md) for
workspace and trace-inspection validation limits.

## 5. Create datasets and validate cases

### Build on open robotics datasets

The [ABC import](product/abc-dataset-support.md) builds on the ABC team's data and
conversion tools. A selected observation, task and exported robot state become an
ordinary ROVE case, retaining source episode, split, domain, frame and artifact
identity. CLI and the existing Cases import dialog share the same boundary. Full
recordings and demonstrated actions remain reference material; they never establish
that a newly evaluated strategy completed the physical task. This provides an
onboarding path into existing strategy comparisons, SME review and campaign history.
See [ADR-028](architecture/ADR-028-abc-episode-import.md) for scope and validation;
the separate [ABC bimanual integration](product/abc-pi05-comparison.md) now runs
simulator episodes with fresh candidate actions and retained measurements. Physical
robot evaluation remains outside that implementation.

### Review customer cases and outputs

A customer without labels can run the initial campaign and have an SME review three distinct targets:

1. **Case validity:** usable input, clear instruction and sufficient evidence for the stated assessment.
2. **Reusable annotation:** expected properties, constraints, acceptable alternatives or corrected reference labels.
3. **Output rating:** assessment of one trial output under a versioned rubric, with rationale and evidence.

Save a collection as an immutable dataset revision, preserving exact case membership, annotations and rubric revisions with links to reference outputs/reviews. Failed outputs remain useful examples; dataset membership is not limited to successes. Exclusions retain reasons and history. A reference answer is not automatically ground truth or the only acceptable solution.

Candidate outputs receive their own grades. Baseline evidence can be regraded under the same rubric without increasing trial counts; original grades remain available. New task inputs require new case versions. Missing evidence remains unknown. Private grading material and baseline answers are excluded from candidate inputs unless that use is explicitly part of the evaluation.

The local implementation uses revision-checked drafts and immutable final reviews.
Dataset freezing checks its exact preview and preserves exclusions and disagreement.
The fully reviewed badge requires accepted validity and annotation reviews with
no unresolved disagreement; incomplete datasets expose their coverage. An explicit
contract binding can supply selected accepted frozen annotations to a participating
local verifier, retaining exact review IDs. Candidate stages and unrelated graders
receive none. Output ratings never become reusable labels automatically. Reviewer
names remain local attribution rather than authenticated organizational identities.

## 6. Success definitions and meaningful measures

The Cases workflow uses one validated, frozen success/measurement contract in its API and UI. Before launch, show the criteria, required evidence, grading source, repetition budget and measures expected in the report. Case success criteria and campaign performance targets are separate: changing a dashboard target must not rewrite task grades.

Prioritize measures tied to robotics and agent work:

- **Task correctness and completion:** scene/decision correctness, rubric-based plan acceptance or observed robot completion, with assessment scope explicit.
- **Reliability:** pass^k across repeated attempts; pass@k as a secondary at-least-one-success view, not proof of deployed retry or recovery behavior.
- **Constraints:** violations of declared force, contact, workspace or task constraints when their evidence is available.
- **Autonomy and recovery:** human assistance, interventions and recovery under defined conditions, only when recorded.
- **Time and resource use:** robot task duration separately from pipeline latency, plus cost or usage where measured.
- **Evidence coverage:** completed, ungraded, unknown, interrupted and excluded observations, with units and source quality.

The final report should lead with task outcomes and comparable differences. Collapsible details contain definitions, formulas, denominators, uncertainty assumptions, units, grader versions and links to underlying trials. Precision, recall and F1 are out of this iteration's scope.

Reports calculate pass@k/pass^k, coverage, pipeline latency and configured verification measurements. Versioned episode evidence supports completion time, autonomous completion, recovery and constraint aggregates with explicit denominators; missing evidence or zero eligible opportunities stays unavailable. HTML and JSON reports include aggregate measures and campaign targets; CSV remains one row per trial without repeated campaign aggregates. See [success and performance measures](product/metrics-and-success.md) and [robotics evidence](product/robotics-evidence.md).

### Inspect traces and measurements

Every report should support **outcome → trial → measurement or grade → stage/call → supporting evidence**. A developer must be able to inspect what the system observed, returned, requested and actually executed, including errors and incomplete recording. Measurements carry values, units, source quality and the exact evidence used; timing distinguishes stage work, pipeline wall time and robot task time.

The trial inspector combines saved configuration, verdicts, measurements, preserved stage outputs, producer-clock trace lanes and on-demand managed assets. Byte ranges and indexed JSON sample/frame/time ranges resolve through trial evidence identities; arbitrary video decoding and remote fetching are unsupported. Paired-trial views place baseline and candidate traces side by side while retaining separate unsynchronized clocks. Comparisons expose matching cases, assessment scope and changed components without presenting correlation as proven causation.

Quick and campaign paths retain events exposed by their orchestrator and optional Copilot runtime, alongside results and structured checks. Internal customer-agent calls still require adapter support; missing telemetry is not reconstructed. Native SDK spans, ROVE stage/tool ancestry and supplied clock identities remain inspectable locally. Cross-clock synchronization is never inferred from arrival order. See [Trial history](TRIAL_HISTORY.md), [traces and measurements](product/traces-and-measurements.md), [evidence and exchange](product/evidence-and-exchange.md) and [ADR-022](architecture/ADR-022-trial-telemetry.md).

## 7. Baselines, ablations and evidence

A completed initial campaign can be named and pinned as a baseline even while SME review is pending. Each immutable baseline revision captures its campaign/strategy, cases, contract, assessment revision set and outcomes, including unknowns. Renaming, unpinning or replacing a baseline creates a revision; later ratings do not rewrite a captured baseline. Candidate comparisons retain the selected revision. See [named baselines and confirmed assistance](product/named-baselines-and-assistant.md).

An ablation copies cases, repetitions and grading conditions and exposes both the intended component change and incidental differences. Renaming a strategy should not sever its relationship to a baseline. A changed evaluator, dataset or environment may require regrading or new matching trials; do not silently call it model improvement.

Robotics episode evidence must identify the producing configuration and attempt. Reusing policy A's recorded outcome cannot demonstrate policy B's performance. Matching seed labels alone do not establish matching physical conditions or statistical independence. Curated development data remains distinct from an independent held-out assessment.

### Action comparison and complete episodes

The current local VLA path collects action output from one prepared observation/state;
it does not execute, observe the changed environment and replan until completion.
This supports integration and action-level diagnostics. The separate
[ABC bimanual integration](product/abc-pi05-comparison.md) owns simulator reset, fresh
observations, action execution and terminal evidence,
with multiple chunks retained inside one trial. Match initial conditions for strategy
comparisons; subsequent observations naturally diverge. A URDF does not adapt a
checkpoint to arbitrary cameras, state, normalization or control conventions. See
[action and episode evaluation](product/action-and-episode-evaluation.md) for the
adapter-specific boundary and requirements for other episode integrations.

## 8. Storage and integration direction

Use SQLite for the local product. The shared journal stores trials, snapshots, events, cases, reviews, frozen datasets, named baseline revisions, evidence references and operation identities with schema migrations and integrity checks. Observations, stage outputs and bounded auxiliary recordings are separate content-addressed assets. A complete backup preserves the journal, assets and separate campaign database together. The versioned exchange exports a validated relational manifest and assets and restores only into a fresh destination. It is a portable local format; PostgreSQL and cloud analytical connectors remain demand-driven.

The exchange manifest preserves schemas, identities, lineage and asset hashes for downstream transforms. Its JSONL tables are an interchange representation; SQLite remains the live store. Parquet and Delta/Fabric/Databricks connectors require justified workloads and target-specific validation. Configuration snapshots identify accessible artifacts and declared model versions, but do not archive remote weights or recreate a physical environment.

### Evaluation harness and agent runtime

ROVE already has the core evaluation services: configured trials, execution orchestration, grading, evidence and reporting. Copilot SDK is the accepted shared runtime for agent behavior ROVE hosts: the evaluation assistant, configured strategy agents and agent graders where they add value. ROVE still validates and freezes cases, strategies, repetitions and success contracts; records trial lifecycle and evidence; and calculates reports from authoritative records.

ROVE owns these evaluation contracts and execution services while reusing Copilot's agent loop, tool and telemetry capabilities. A customer's agent remains the system under test: its runtime, tools, prompts and action interface become versioned components of a strategy. Direct customer agents, VLMs and VLAs remain evaluable through supported adapters. Adding Copilot planning or recovery around one creates a different combined strategy and must be compared explicitly. See the [target architecture](architecture/target-architecture.md), [ADR-023](architecture/ADR-023-evaluation-and-execution-harnesses.md), [ADR-024](architecture/ADR-024-copilot-runtime-and-observability.md) and the [dated model assessment](product/model-landscape-2026-09.md).

SDK session events and captured native spans feed the durable trial record. The
configured Copilot adapter hosts perceive, plan and verify in fresh role-scoped
sessions; action stages use explicit integrations. Endpoint runtime contracts
describe memory, reset and telemetry coverage without attesting a customer reset.
The assistant prepares case import/revision, contracts, dataset freezing, named
baselines and campaign/ablation operations through validated services. Each write
requires its exact host confirmation; the assistant cannot manufacture SME reviews
or labels. Local telemetry copies can use a bounded loopback OTLP exporter, disabled
by default. Controlled real-SDK/CLI tests establish local infrastructure behavior,
not live-provider quality or a deployed cloud monitoring profile. Local Qwen and native SmolVLA inference were exercised in the later
[ADR-033 validation](architecture/ADR-033-isolated-vla-runtime-and-trial-inspection.md).
That does not establish physical success, cloud-provider coverage or deployed
Azure/Grafana analytics.
See [runtime lifecycle](architecture/runtime-lifecycle.md),
[local observability](architecture/runtime-observability.md) and the
[live-provider validation specification](product/live-provider-validation.md).

### Recommended next industrial validation milestone

Beyond the implemented ABC simulator episode path, an industrial task needs its own
validated environment and outcome measurement. Image/task/URDF comparisons and FK diagnostics support preliminary screening;
they cannot establish the most reliable industrial task solution. The next recommended
slice is one resettable pick-and-place environment, one declared robot, measurable
object-level success and repeated complete episodes of two executable strategies.
Fresh observations and execution acknowledgements must connect actions to outcomes.
Agent-with-robot-tools and VLA/controller systems are compared as full strategies.
This is a proposed milestone requiring scope approval, not an implemented feature or
a commitment to add generic closed-loop control in the current update.

## 9. Capability and delivery status

| Capability | Current implementation and validation boundary, 2026-09-14 |
| --- | --- |
| Configured optional stages, supported models/agents and local dashboard | Implemented; adapter availability depends on installed dependencies and endpoints |
| Repeated campaigns, frozen selected configuration, SQLite trial records and HTML/JSON/CSV reports | Implemented in PR #13 |
| Local task evaluators, required constraints, optional FK diagnostics and versioned evidence | Implemented in PR #14 |
| Durable shared quick/campaign trial recording and initial observation assets | Implemented; frozen snapshots, interrupted-work recovery and idempotent legacy quick-history import |
| Durable activity, evidence ranges and paired trace inspector | Implemented for supplied events and managed assets; separate source clocks, indexed JSON ranges and explicit missing coverage; no arbitrary remote fetch or video decoding |
| Copilot runtime for hosted perception, planning and verification | Optional pinned runtime with fresh role scopes, lifecycle contracts and local provider validation; cloud/provider-specific acceptance remains separate |
| ROVE/native spans and optional monitoring | Local stage/tool correlation, native capture and bounded loopback OTLP export implemented; exporter disabled by default; no validated Azure/Grafana deployment |
| Evaluation assistant | Typed reads and host-confirmed operations; saved selection and explicit connection tests; no SME judgment or label-writing tools |
| Robot action integrations | Direct customer action adapters and the synthetic test world are supported; no Copilot hardware-action tools or hardware drivers |
| Customer intake, case revisions and expected-metric preview | Managed images and bounded auxiliary evidence, typed episode validation, deterministic action-dependent synthetic rollout and aggregate targets implemented |
| SME review, reusable annotations and frozen datasets | Optimistic drafts, immutable final reviews, atomic freezing and explicit frozen annotation-to-local-verifier bindings implemented |
| Baseline/ablation references, component diffs and quick promotion | Named/pinned immutable baseline revisions, captured assessments, paired traces and exploratory quick promotion implemented |
| Shared trial/campaign workspace and versioned iteration history | Implemented with retained strategy comparison, exact saved configuration, explicit launch, trace inspection and linked Improve copies |
| ABC dataset support | Converted local real/sim observation import through CLI/API/UI; source assets private, no automatic candidate-success labels |
| Isolated native VLA inference | Local SmolVLA path exercised; probes are execution-ineligible; Pi0.5 and physical deployment are not validated by that probe |
| Relational exchange; PostgreSQL or Delta Lake integration | Validated local exchange/restore implemented; PostgreSQL and cloud connectors remain demand-driven |
| Real closed-loop robot/simulator integration, universal adapter compatibility or safety certification | Not provided by the current release |

The local product connects durable trials to case contracts, approved labels, synthetic/recorded evidence, frozen datasets, named baselines and inspectable comparisons. The import → baseline → review → save collection → candidate → comparison journey preserves versions, restart recovery and missing evidence. The [sequenced delivery plan](product/implementation-plan.md) records current validation and separates local delivery from live-provider/cloud specifications, hardware drivers and demand-driven analytical integrations.

## 10. Related specifications and decisions

- [Concepts](product/concepts.md): vocabulary and current/proposed mappings.
- [Customer workflows](product/evaluation-workflows.md): persona stories, onboarding, SME review and acceptance criteria.
- [User journey and navigation](product/user-journey.md): primary destinations, staged evaluation, route ownership and accessibility acceptance.
- [Metrics and success](product/metrics-and-success.md): configuration/UI contract and report explanations.
- [Traces and measurements](product/traces-and-measurements.md): trial timelines, evidence inspection and baseline diagnosis.
- [Robotics evidence](product/robotics-evidence.md): action-dependent synthetic trials, typed recordings, frozen annotation bindings and aggregate targets.
- [Evidence and exchange](product/evidence-and-exchange.md): preserved ranges, clock-aware trace lanes and validated relational transfer.
- [Named baselines and assistance](product/named-baselines-and-assistant.md): immutable baseline assessments and confirmed workflow operations.
- [Runtime lifecycle](architecture/runtime-lifecycle.md) and [observability](architecture/runtime-observability.md): implemented role/reset boundaries and local telemetry.
- [Model and harness assessment](product/model-landscape-2026-09.md): dated research informing architecture, not a ROVE benchmark.
- [Current implementation](architecture/current-implementation.md): actual code paths, APIs, storage and tests.
- [Trial history and optional Copilot stages](TRIAL_HISTORY.md): local setup, inspector usage, privacy and complete backups.
- [Target architecture](architecture/target-architecture.md) and [system diagram](architecture/system-diagram.md): logical responsibilities, processes, data and optional integrations.
- [Implementation plan](product/implementation-plan.md): sequenced tasks, dependencies and milestone acceptance.
- [Architecture decisions](architecture/README.md): evaluation, navigation and strategy-revision decisions with diagrams and implementation status.
- [Campaign usage](BENCHMARKS.md) and [configured verification](VERIFICATION.md): supported configuration today.

Earlier versions of this file remain in Git history. The core vision and personas are retained; historical claims about unimplemented commands, full simulation, universal reproducibility or future integrations are not release guarantees.
