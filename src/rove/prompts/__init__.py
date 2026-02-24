"""Prompt loading utilities for ROVE pipeline stages."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent


class PromptLoader:
    """Load prompt templates from the prompts/ directory.

    Templates use Python str.format() placeholders (e.g. ``{task}``).
    Loaded templates are cached for the lifetime of the process.
    """

    def __init__(self, prompts_dir: Path | None = None):
        self._dir = prompts_dir or _PROMPTS_DIR
        self._cache: dict[str, str] = {}

    def load(self, name: str) -> str:
        """Load a prompt template by name (without extension).

        Looks for ``<name>.txt`` in the prompts directory.
        """
        if name in self._cache:
            return self._cache[name]
        path = self._dir / f"{name}.txt"
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        text = path.read_text().strip()
        self._cache[name] = text
        return text

    def render(self, name: str, **kwargs: str) -> str:
        """Load and render a prompt template with the given variables.

        Missing placeholders are left as-is (uses format_map with a
        defaultdict-like fallback).
        """
        template = self.load(name)
        return template.format_map(_SafeDict(kwargs))


class _SafeDict(dict):
    """Dict subclass that returns the key wrapped in braces for missing keys."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


# Module-level singleton for convenience
_default_loader: PromptLoader | None = None


def get_loader(prompts_dir: Path | None = None) -> PromptLoader:
    """Get the default PromptLoader (creates on first call)."""
    global _default_loader
    if _default_loader is None or prompts_dir is not None:
        _default_loader = PromptLoader(prompts_dir)
    return _default_loader
