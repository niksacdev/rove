# ADR-005: Python Src Layout and Test Suite

**Status**: Accepted
**Date**: 2026-02-22
**Deciders**: @niksac

## Context

The project used a flat layout (`rove/` at repo root) which is discouraged in modern Python because running `python` in the repo root can import the local `rove/` directory instead of the installed package. Additionally, the project had zero test files despite having a full mock adapter suite that enabled comprehensive testing.

## Decisions

### D1: Migrate to Python src layout

Move `rove/` → `src/rove/`. The namespace (`import rove`) is unchanged — only the filesystem location changes.

**pyproject.toml update**:
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/rove"]
```

**Path fixes**: Two files used `Path(__file__)` relative paths to find project root:
- `src/rove/config.py` — added one `.parent` level to reach repo root
- `src/rove/api/app.py` — added one `.parent` level to find `frontend/`

### D2: Use `uv` for all package operations

`uv` replaces `pip` for installs, dependency resolution, and virtual environment management. It is faster and produces deterministic lockfiles.

```bash
uv pip install -e ".[dev]"
```

### D3: Comprehensive test suite with pytest + pytest-asyncio

Created `tests/` directory with 7 test files covering all pipeline components:

| File | Tests | Coverage |
|------|-------|----------|
| `tests/conftest.py` | — | Shared fixtures: registry, mock_image, mock_strategy, agent_strategy |
| `tests/test_models.py` | 8 | PipelineContext.to_dict() serialization, Strategy construction |
| `tests/test_config.py` | 8 | load_config, find_model_config, get_strategies |
| `tests/test_mock_adapters.py` | 11 | MockSim statefulness, MockVLM context, MockPolicy plan steps, MockAgent context |
| `tests/test_pipeline.py` | 5 | End-to-end data flow, plan references perceived target, verify receives context |
| `tests/test_run_manager.py` | 5 | Concurrent execution, sim isolation, semaphore bounds, strategy_complete events |
| `tests/test_api.py` | 5 | GET /api/strategies, GET /api/models, POST /api/evaluate, SSE stream |

**pytest configuration** in pyproject.toml:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

### D4: Autouse fixture resets config cache

A `_reset_config` autouse fixture clears cached config state between tests, preventing config bleed across test runs.

## Consequences

- **Positive**: No accidental local imports. All tests run via `pytest tests/ -v`.
- **Positive**: 42+ tests validate all mock adapters, pipeline data flow, concurrent execution, and API endpoints.
- **Positive**: `uv` provides faster, reproducible installs.
- **Negative**: One-time path fix needed for `__file__`-relative paths. All future `__file__` paths must account for `src/` prefix.

## Files

| File | Change |
|------|--------|
| `rove/` → `src/rove/` | git mv (entire package) |
| `pyproject.toml` | Updated packages path, added pytest config |
| `src/rove/config.py` | Fixed `__file__` path (+1 parent) |
| `src/rove/api/app.py` | Fixed `__file__` path (+1 parent) |
| `tests/` | NEW — 7 test files, 42+ tests |
| `CLAUDE.md` | Updated project structure section |
