"""Tests for FastAPI endpoints."""

from __future__ import annotations

import io
import json

import pytest
from httpx import ASGITransport, AsyncClient

from rove.api.app import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestStrategiesEndpoint:
    @pytest.mark.asyncio
    async def test_list_strategies(self, client):
        resp = await client.get("/api/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategies" in data
        ids = [s["id"] for s in data["strategies"]]
        assert "mock" in ids

    @pytest.mark.asyncio
    async def test_strategy_has_model_assignments(self, client):
        resp = await client.get("/api/strategies")
        data = resp.json()
        mock = next(s for s in data["strategies"] if s["id"] == "mock")
        assert mock["perceive"] == "mock-vlm"
        assert mock["act"] == "mock-vla"
        assert mock["sim"] == "mock-sim"


class TestModelsEndpoint:
    @pytest.mark.asyncio
    async def test_list_models(self, client):
        resp = await client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "stages" in data
        assert "perceive" in data["stages"]
        assert "act" in data["stages"]


class TestEvaluateEndpoint:
    @pytest.mark.asyncio
    async def test_evaluate_returns_202(self, client):
        # Create a small fake image
        img = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        resp = await client.post(
            "/api/evaluate",
            data={"task": "pick bracket", "strategy_ids": "mock"},
            files={"image": ("test.png", img, "image/png")},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "eval_id" in data
        assert data["status"] == "running"
        assert data["strategies"] == ["mock"]

    @pytest.mark.asyncio
    async def test_evaluate_sse_stream(self, client):
        img = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        resp = await client.post(
            "/api/evaluate",
            data={"task": "pick bracket", "strategy_ids": "mock"},
            files={"image": ("test.png", img, "image/png")},
        )
        eval_id = resp.json()["eval_id"]

        # Read SSE stream
        events = []
        async with client.stream("GET", f"/api/evaluate/{eval_id}/stream") as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
                elif line.startswith("event: complete"):
                    # Next data line will be the complete event, read it and stop
                    pass

        # Should have stage events
        assert len(events) > 0
