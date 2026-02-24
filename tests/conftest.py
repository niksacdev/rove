"""Shared fixtures for ROVE tests."""

from __future__ import annotations

import pytest

from rove.adapters.registry import AdapterRegistry
from rove.models import Strategy
from rove.models.config import reset_config_cache

# Minimal 1x1 white PNG base64
MOCK_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def registry():
    return AdapterRegistry()


@pytest.fixture
def mock_image_base64():
    return MOCK_IMAGE_B64


@pytest.fixture
def mock_strategy():
    return Strategy(
        id="mock",
        display_name="Mock (Simulation)",
        description="All mock adapters for testing",
        perceive="mock-vlm",
        plan="mock-vlm",
        act="mock-vla",
        verify="mock-vlm",
        sim="mock-sim",
        tags=["test", "mock"],
    )


@pytest.fixture
def agent_strategy():
    return Strategy(
        id="agent_orchestrated",
        display_name="Agent Orchestrated",
        description="Agent plans and acts via tool calls — VLM perceives and verifies",
        perceive="mock-vlm",
        plan="mock-agent",
        act="mock-agent",
        verify="mock-vlm",
        sim="mock-sim",
        tags=["agent"],
    )


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config cache between tests."""
    reset_config_cache()
    yield
    reset_config_cache()
