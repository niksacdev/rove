# ROVE documentation

**ROVE evaluates robotics agent pipelines on your task.** The founding vision and three personas remain the basis for the product. This guide explains how a robotics team can assess a pipeline, inspect the evidence and compare changes under stated conditions.

| Read | Purpose |
| --- | --- |
| [Product specification](PRODUCT_SPEC.md) | Core vision, founding personas, campaign model and delivery status |
| [Concepts](product/concepts.md) | Cases, trials, campaigns, strategies, datasets and provenance |
| [Product workflows](product/evaluation-workflows.md) | Baselines, SME review, frozen datasets, ablations and acceptance criteria |
| [User journey and navigation](product/user-journey.md) | Start, Evaluate, Results and Configure; staged workspace, routes and accessibility acceptance |
| [Navigation decision](architecture/ADR-025-workflow-navigation.md) | Why navigation follows the evaluation journey while retaining existing routes |
| [Success and performance measures](product/metrics-and-success.md) | Define success before a campaign; understand the report and missing evidence |
| [Traces and measurements](product/traces-and-measurements.md) | Inspect trial timelines, supporting evidence and baseline differences |
| [Robotics evidence and targets](product/robotics-evidence.md) | Action-dependent fixtures, approved annotation bindings and supplied episode measures |
| [Named baselines and assistant](product/named-baselines-and-assistant.md) | Frozen baseline assessments and confirmed guided workflows |
| [Preserved evidence and exchange](product/evidence-and-exchange.md) | Recording selections, clock-aware traces and portable relational tables |
| [Live provider validation spec](product/live-provider-validation.md) | Planned provider/Azure checks; execution intentionally skipped |
| [Model assessment](product/model-landscape-2026-09.md) | September 2026 evidence on frontier agents, VLAs and evaluation scope |
| [Current implementation](architecture/current-implementation.md) | Diagrams of the code that exists today, with source and test links |
| [Target architecture](architecture/target-architecture.md) | Logical roles for ROVE services, Copilot-hosted agents and direct customer systems |
| [System diagram](architecture/system-diagram.md) | Processes, storage, trial sequence and optional observability integrations |
| [Implementation plan](product/implementation-plan.md) | Sequenced delivery tasks, dependencies and acceptance gates |
| [Architecture decisions](architecture/README.md) | Decisions, alternatives, consequences and implementation status |
| [Run campaigns](BENCHMARKS.md) | Supported campaign configuration, CLI/API and metric behavior |
| [Configure verification](VERIFICATION.md) | Supported task evaluators, constraints, diagnostics and measurements |

The current implementation and delivery plan distinguish validated local features
from external deployment and provider validation. Product specifications include
proposed follow-up work and label it explicitly; an accepted direction alone does
not establish deployed integration support.

## Explain ROVE in three diagrams

1. Use the concepts diagram to explain the units being evaluated.
2. Use the current implementation diagram to explain how ROVE runs and records them today.
3. Use the target architecture and system diagrams to explain the accepted direction.
4. Use the implementation plan and ADR diagrams to explain delivery order and rationale.

## Historical design material

The older [high-level architecture](architecture/high-level-architecture.md) and ADRs 001–016 preserve design history. Some routes, integrations and guarantees in those documents were proposals. Earlier product-specification versions remain in Git history; [the current specification](PRODUCT_SPEC.md) retains their vision and personas while updating the evaluation model. Use the current implementation and executable tests when describing available behavior.

## Keeping these explanations accurate

For a feature change, update its product behavior, acceptance criteria, related ADR and diagram in the same PR. Mark the implementation status and link to relevant code/tests. Record superseding decisions rather than rewriting the rationale of an old decision. A diagram showing a planned component must say that it is planned.
