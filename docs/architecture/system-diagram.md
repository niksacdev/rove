# ROVE system: execution, evidence and integrations

**Status:** Current local process boundaries, with future integrations explicitly
labelled. Broader robot lifecycle requirements below remain a target contract.
**Updated:** 2026-09-14.

The local application serves both interactive strategy comparison and automated
campaigns. It does not require microservices or a cloud account for mock operation.
See [current implementation](current-implementation.md) for source/test evidence and
[logical architecture](target-architecture.md) for responsibilities.

## Current local system

```mermaid
flowchart LR
    B[Browser: Trials and Campaigns] --> API[FastAPI and static UI]
    API --> S[Shared ROVE services]
    CLI[CLI: cases, trials, campaigns and reports] --> S
    S --> EX[Pipeline and bounded campaign execution]
    EX --> DIRECT[Configured customer/model adapters]
    EX --> SDK[Optional Copilot role-scoped runtime]
    API --> ASSIST[Optional evaluation assistant]
    ASSIST --> SDK
    ASSIST --> CONF[Validated read or confirmed mutation]
    CONF --> S
    DIRECT --> LP[Optional isolated local VLA worker]
    DIRECT --> EP[Configured local or remote endpoint]
    SDK --> PROVIDER[Selected model provider]
    EX --> REC[Durable trial and evidence recording]
    SDK --> REC
    S --> REC
    REC --> DB[(Local SQLite stores)]
    REC --> ASSET[(Managed content-addressed assets)]
    DB --> REPORT[Assessments, reports, baselines and timeline]
    ASSET --> REPORT
    REPORT --> S
    REC -. Optional bounded local OTLP .-> COL[Loopback collector]
    COL -. Future hosted validation .-> MON[App Insights, Log Analytics or Grafana]
    DB --> X[Validated relational exchange]
    ASSET --> X
    X -. Future destination integration .-> LAKE[Fabric, Databricks or Delta]
    EX -. Future physical lifecycle adapter .-> ROBOT[Robot or general closed-loop simulator]
```

Ordinary CLI evaluation and data commands call application services directly. Assistant
settings/test/proposal commands contact the owning local server. Copilot hosts optional
assistant or configured agent behavior; it does not schedule every direct-model call
or act as a robot driver. The isolated VLA worker uses its own dependency environment,
not a security sandbox. Its successful inference does not establish task completion.

SQLite stores retain trials/cases/reviews/assets metadata, campaigns and their insight
cache, strategy revisions, and local assistant selection in their respective databases.
A complete study backup preserves all relevant databases and managed assets together.
SDK session state and optional telemetry are not substitutes for those records.
ABC and JSONL/image imports enter existing Case services; original source artifacts
and private labels remain separate from candidate input. See
[storage and exchange](../product/evidence-and-exchange.md).

## Trial Lifecycle

```mermaid
sequenceDiagram
    participant U as UI, CLI or assistant tool
    participant E as ROVE evaluation services
    participant D as Durable store
    participant W as Trial worker
    participant S as Configured system
    participant G as Configured grader
    U->>E: Submit case, strategy and success contract
    E->>D: Freeze revisions and create pending trial
    E->>W: Dispatch stable trial identity
    W->>S: Start with declared inputs and reset/memory scope
    loop Activity exposed by the integration
        S-->>W: Events, outputs or observations
        W->>D: Record identities, evidence and coverage
    end
    alt Required evidence is available
        W->>G: Assess evidence under frozen rubric/checks
        G-->>W: Versioned verdict and measurements
    else Interrupted or insufficient evidence
        W->>W: Preserve explicit incomplete or unknown state
    end
    W->>D: Record terminal state and available assessment
    U->>E: Reopen trial or compare baseline
    E->>D: Read records and asset references only
    E-->>U: Outcome, timeline and evidence
```

This broader target sequence preserves the distinction between execution state and task
outcome. A failed task can have a complete trace; an interrupted process can leave
an unknown outcome. Reopening history never dispatches actions. An interrupted
physical trial is not automatically replayed; continuing it requires the declared
execution/reset contract and a valid environment acknowledgement.

## Failure and Data Boundaries

| Boundary | Required behavior |
| --- | --- |
| Browser disconnect | Worker recording continues; reconnect reads saved progress |
| Process crash | Pending/running records reconcile to interrupted or uncertain state; captured evidence survives |
| Duplicate tool request/event | Stable operation IDs prevent duplicate dispatch; source IDs prevent duplicate history |
| Recorder failure | Surface capture failure; do not present incomplete recording as complete evidence |
| Collector/provider outage | Collector failure cannot erase trials; provider failure remains a recorded execution failure |
| Slow exporter | Bounded queues and explicit drop/coverage reporting; external export does not block robot control |
| Candidate versus grader | Separate sessions, tools and data access; private grading labels stay outside candidate context |
| Sensitive/large content | Redact credentials; external content capture off initially; export approved references rather than full recordings |

Tracing follows OpenTelemetry/W3C context, while robot timestamps retain their own
clock identity and alignment uncertainty. Keep high-cardinality trial IDs in
traces/logs and links, rather than turning every trial into a metrics label.

## Optional Destinations

Azure Monitor accepts OTLP through a configured Collector. In Microsoft's documented
setup, log/trace data uses a Log Analytics workspace and metrics use an Azure Monitor
workspace; Application Insights supplies investigation experiences. Identity,
workspace and metric-format configuration remain deployment tasks. See
[Microsoft's ingestion guide](https://learn.microsoft.com/en-us/azure/azure-monitor/containers/opentelemetry-protocol-ingestion).

Grafana supports OTLP through its Collector/Alloy path or supported ingestion
endpoints, with Tempo, Loki and Mimir providing trace, log and metric storage.
See [Grafana's OpenTelemetry guide](https://grafana.com/docs/opentelemetry/).

Fabric/Delta exports use versioned evaluation records and asset manifests rather
than reconstructing the dataset from monitoring logs. PostgreSQL is a separate,
demand-driven storage decision; none of these integrations is required locally.
