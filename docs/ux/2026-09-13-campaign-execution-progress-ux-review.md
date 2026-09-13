# Campaign execution progress UX review — 2026-09-13

## User problem

The previous Run screen hid pipeline execution feedback inside each trial disclosure. A developer comparing strategies could not see which one was running or distinguish queued work from finished execution without opening details.

## Change

- Show a stable lane for every selected strategy before and during execution.
- Present status in text, with restrained visual emphasis for running and execution errors.
- Show the current case, configured repetition position, elapsed time and recorded stage states.
- Keep rich output and full traces one action away, loaded only on inspection.
- Distinguish execution completion from task acceptance; show cancelled remaining work as not run.
- Retain keyboard focus on Inspect output during refreshes. Use status semantics and descriptive button labels, wrap long names and stack lanes on narrow screens.

## Verification

Automated tests cover multiple strategies, queued/running/completed/error/cancelled states, exact repetition indexing for nonconsecutive seeds, actual elapsed time, safe text rendering, stable focused controls, incremental event polling and retry, and lazy full-output loading. A sanitized trace fixture verifies that internal adapter spans do not appear as pipeline stages.

The existing shared rich-viewer and strategy-selection regression suites also pass. These checks do not constitute a screen-reader or full browser accessibility audit. Desktop and narrow-screen visual review remains a release verification step.

## Scope and limits

Current execution follows the existing scheduler. Lanes expose its observed state and do not imply concurrent strategies when the scheduler runs them sequentially. There is no invented progress percentage, estimated completion time, or success verdict. Stage events become visible on the next two-second poll; unavailable event updates do not stop the campaign.
