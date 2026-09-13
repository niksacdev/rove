# ADR-030: Foundry-inspired visual language

- Status: Accepted, implemented
- Date: 2026-09-13
- Extends: [ADR-029](ADR-029-workspace-interaction-consistency.md)

## Context

ROVE's robotics developers compare strategies, inspect evidence and repeat those
comparisons. Inconsistent navigation, controls and page styling distract from
that work. The requested visual reference is Azure AI Foundry's AI workspace,
not Azure Portal's resource-management interface.

## Decision

Use `frontend/fluent.css` as the shared token and component layer, loaded after
page styles on all four workspaces. Centralize neutral light/dark colors,
restrained purple actions, Segoe UI/system typography, spacing, focus states and
control geometry. Remove conflicting legacy journey rules from `datasets.css`.

Keep ROVE's Evaluate and Campaigns navigation. Use an aligned campaign list with
name, status, trials, created date and View results / Export / Improve actions.
Collapse the columns into rows on narrow screens. Configuration steps use flat
navigation with a clear active underline; focused editors use native dialogs.
Preserve semantic links, buttons and disclosures and the user's saved theme.

```mermaid
flowchart TB
  F[Foundry workspace patterns and Fluent guidance] --> T[Shared tokens]
  T --> C[Navigation, controls, fields and dialogs]
  C --> Q[Trial composer]
  C --> L[Campaign list]
  C --> W[Configure, run and review]
  Q --> API[Existing ROVE APIs]
  L --> API
  W --> API
  API --> E[Execution and durable evidence]
  E --> R[Standalone HTML report with matching palette]
```

## Alternatives and consequences

A React/Fluent component-library migration would replace working frontend
infrastructure without adding evaluation capability. This change adapts the
visual language in existing HTML/CSS; it does not claim to integrate Microsoft's
component library or reproduce Foundry exactly. Azure Portal navigation would
introduce unrelated resource-management concepts and is not adopted.

Exported reports embed matching colors and typography because they must remain
standalone. This small duplication must be updated with future theme changes.
Page-specific content layouts remain in their existing stylesheets. No changes
are made to pipeline execution, CLI operations, SQLite records or scoring.

## Validation

`tests/test_fluent_ui.cjs` checks text/action contrast in both themes and verifies
the shared layer loads last on every workspace. Campaign-list regression tests
cover dates, unavailable timestamps, progress and the existing actions. Browser
inspection covers the campaign list, trial composer, sample cases, results and
the reviewed strategy dialog. Existing UI and Python suites cover execution and
report behavior; provider inference is outside this presentation change.

## References

- [Foundry evaluation results](https://learn.microsoft.com/en-us/azure/foundry/how-to/evaluate-results)
- [Foundry navigation](https://learn.microsoft.com/en-us/azure/foundry/how-to/navigate-from-classic)
- [Fluent typography](https://fluent2.microsoft.design/typography)
- [Fluent color](https://fluent2.microsoft.design/color)
- [Fluent layout](https://fluent2.microsoft.design/layout)

## Browsing and actionable improvement refinement

Trials and Campaigns both start with full-width lists. Opening a trial reveals
its inspector; Back to trials restores the source filter, page and focus.
Deep links still open the requested trial directly. Settings is a gear link in
the top-right shared navigation on every workspace, superseding ADR-029's sidebar
placement.

Improvement recommendations identify the strategy, affected-trial counts and
named case evidence. Execution failures and missing assessments produce separate
recommendations even within one strategy. Model changes open the existing
reviewed revision editor; unscored trials link to their evidence and success
metrics. A strategy with no recorded trials is never described as passing.
Missing error messages stay absent rather than being invented. These are
recorded findings; optional model-authored interpretation remains labelled.

Regression coverage includes `test_history_routes_ui.cjs`,
`test_evaluation_journey_ui.cjs` and `test_campaign_seeds.py` for browsing,
evidence actions, mixed failure/unknown results and empty history.
