# ADR-009: Dashboard — Plain HTML+JS with SSE Streaming

**Date**: 2026-02-22
**Status**: Accepted

## Context

ROVE needs a dashboard for researchers to configure evaluations, observe pipeline progress in real-time, and compare results across strategies. The question is what technology to use for the frontend.

Industry default for new projects is React/Next.js with a build toolchain (npm, Vite, webpack). However, ROVE's dashboard has specific constraints:

1. **The API is the product** — the dashboard is one of four access paths (CLI, Python library, Jupyter, dashboard). If you delete `frontend/`, the evaluation system still works.
2. **No business logic in frontend** — the dashboard renders what the API tells it and sends user actions to the API. Zero state management complexity.
3. **Single developer** — no team to maintain a React codebase, dependency upgrades, or build pipeline.
4. **ML engineers are the audience** — they care about results, not UI polish. They run `uv run rove serve` and open a browser.

## Decision

### Plain HTML + vanilla JS, served as static files by FastAPI

- Single `index.html` file with inline `<style>` and `<script>` blocks
- Tailwind CSS via CDN (`cdn.tailwindcss.com`) — no build step
- Lucide icons via CDN
- No npm, no node_modules, no package.json, no build step
- FastAPI serves the `frontend/` directory as static files

### SSE (Server-Sent Events) for real-time pipeline streaming

- `EventSource` API — native browser support, automatic reconnection
- Unidirectional: server → client only (pipeline progress doesn't need client→server messages)
- Events: `stage` (per-stage progress), `strategy_complete`, `strategy_error`, `complete`
- One SSE connection per evaluation run

### Rejected: WebSocket

WebSocket was considered but rejected because:
- Bidirectional communication is unnecessary — the client never sends messages during an evaluation
- WebSocket requires explicit reconnection logic; SSE handles it natively
- WebSocket adds connection lifecycle complexity (open/close/error handlers, heartbeats)

### Rejected: React/Vite

- Adds 200+ MB of node_modules for zero business logic
- Requires a build step, complicating the developer experience
- Creates a second dependency ecosystem (npm) alongside Python
- The dashboard is ~1500 lines of JS — React would add abstraction overhead without reducing complexity

## Consequences

### Positive

- Zero frontend build step — `uv run rove serve` is all you need
- No node_modules, no npm audit warnings, no dependency conflicts
- Single file is easy to read, debug, and modify
- SSE is simpler than WebSocket for unidirectional streaming
- FastAPI serves static files with zero additional configuration

### Negative

- No component reuse — repeated DOM creation patterns in vanilla JS
- No TypeScript type safety — relying on runtime testing
- Tailwind CDN is ~300KB on first load (cached thereafter)
- SSE has a 6-connection-per-domain limit in HTTP/1.1 (not an issue for single-user tool)

### Risks

- If the dashboard grows significantly beyond current scope (multi-user, complex state), vanilla JS will become harder to maintain — but the architecture principle says "the API is the product," so the dashboard should stay thin
