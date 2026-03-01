"""Tests for EvaluationProvenance, failure attribution, insights, and seed determinism."""

from __future__ import annotations

import pytest

from rove.models import EvaluationProvenance
from rove.orchestrator.failure_attribution import attribute_failure
from rove.orchestrator.insights import compute_run_insights


class TestEvaluationProvenance:
    def test_build_creates_provenance(self):
        prov = EvaluationProvenance.build(
            image_base64="fake_image_data",
            strategy_ids=["mock-full"],
        )
        assert prov.timestamp
        assert prov.hostname
        assert prov.python_version
        assert prov.image_sha256
        assert prov.strategy_ids == ["mock-full"]
        assert prov.seed is None

    def test_build_with_seed(self):
        prov = EvaluationProvenance.build(
            image_base64="img",
            strategy_ids=[],
            seed=42,
        )
        assert prov.seed == 42

    def test_image_hash_deterministic(self):
        h1 = EvaluationProvenance.compute_image_hash("same_image")
        h2 = EvaluationProvenance.compute_image_hash("same_image")
        assert h1 == h2
        assert len(h1) == 16

    def test_image_hash_differs_for_different_images(self):
        h1 = EvaluationProvenance.compute_image_hash("image_a")
        h2 = EvaluationProvenance.compute_image_hash("image_b")
        assert h1 != h2

    def test_package_versions_returns_dict(self):
        versions = EvaluationProvenance.collect_package_versions()
        assert isinstance(versions, dict)
        # pydantic should be installed in test env
        assert "pydantic" in versions

    def test_resolved_models_stored(self):
        prov = EvaluationProvenance.build(
            image_base64="img",
            strategy_ids=[],
            resolved_models={"perceive": "mock-vlm", "verify": "mock-vlm"},
        )
        assert prov.resolved_models["perceive"] == "mock-vlm"

    def test_serialization_roundtrip(self):
        prov = EvaluationProvenance.build(
            image_base64="img",
            strategy_ids=["s1"],
            seed=123,
        )
        d = prov.model_dump()
        restored = EvaluationProvenance.model_validate(d)
        assert restored.seed == 123
        assert restored.strategy_ids == ["s1"]


class TestFailureAttribution:
    def test_success_returns_none(self):
        stages = [
            {"stage": "perceive", "status": "completed", "output": {"task_relevant": ["obj"]}},
            {"stage": "verify", "status": "completed", "output": {"success": True}},
        ]
        fs, fc = attribute_failure(stages, success=True)
        assert fs is None
        assert fc is None

    def test_stage_error_attributed(self):
        stages = [
            {"stage": "perceive", "status": "error", "error": "timeout"},
        ]
        fs, fc = attribute_failure(stages, success=False)
        assert fs == "perceive"
        assert fc == "perceive_error"

    def test_perceive_miss(self):
        stages = [
            {"stage": "perceive", "status": "completed", "output": {"task_relevant": []}},
            {"stage": "verify", "status": "completed", "output": {"success": False}},
        ]
        fs, fc = attribute_failure(stages, success=False)
        assert fs == "perceive"
        assert fc == "perceive_miss"

    def test_plan_low_confidence(self):
        stages = [
            {"stage": "perceive", "status": "completed", "output": {"task_relevant": ["obj"]}},
            {"stage": "plan", "status": "completed", "output": {"confidence": 0.2}},
            {"stage": "verify", "status": "completed", "output": {"success": False}},
        ]
        fs, fc = attribute_failure(stages, success=False)
        assert fs == "plan"
        assert fc == "plan_low_confidence"

    def test_dynamics_violation(self):
        stages = [
            {"stage": "perceive", "status": "completed", "output": {"task_relevant": ["obj"]}},
            {"stage": "plan", "status": "completed", "output": {"confidence": 0.8}},
            {
                "stage": "act",
                "status": "completed",
                "output": {
                    "dynamics_analysis": {"joint_limits_ok": False, "self_collision": False}
                },
            },
            {"stage": "verify", "status": "completed", "output": {"success": False}},
        ]
        fs, fc = attribute_failure(stages, success=False)
        assert fs == "act"
        assert fc == "action_dynamics_violation"

    def test_action_infeasible_from_plausibility(self):
        stages = [
            {"stage": "perceive", "status": "completed", "output": {"task_relevant": ["obj"]}},
            {"stage": "plan", "status": "completed", "output": {"confidence": 0.8}},
            {"stage": "act", "status": "completed", "output": {}},
            {
                "stage": "verify",
                "status": "completed",
                "output": {
                    "success": False,
                    "action_plausibility": {"bounds_check": False},
                },
            },
        ]
        fs, fc = attribute_failure(stages, success=False)
        assert fs == "act"
        assert fc == "action_infeasible"

    def test_verification_mismatch(self):
        stages = [
            {"stage": "perceive", "status": "completed", "output": {"task_relevant": ["obj"]}},
            {"stage": "plan", "status": "completed", "output": {"confidence": 0.8}},
            {"stage": "act", "status": "completed", "output": {}},
            {
                "stage": "verify",
                "status": "completed",
                "output": {"success": False},
            },
        ]
        fs, fc = attribute_failure(stages, success=False)
        assert fs == "verify"
        assert fc == "verification_mismatch"

    def test_error_takes_priority_over_other_rules(self):
        stages = [
            {"stage": "perceive", "status": "completed", "output": {"task_relevant": []}},
            {"stage": "plan", "status": "error", "error": "crash"},
        ]
        fs, fc = attribute_failure(stages, success=False)
        assert fs == "plan"
        assert fc == "plan_error"


class TestSeedDeterminism:
    @pytest.mark.asyncio
    async def test_mock_vlm_deterministic_with_seed(self):
        from rove.adapters.mock_vlm import MockVLMAdapter

        a = MockVLMAdapter(config={"seed": 42, "mock_latency_ms": [0, 0]})
        b = MockVLMAdapter(config={"seed": 42, "mock_latency_ms": [0, 0]})

        r1 = await a.analyze_scene("img", "pick object")
        r2 = await b.analyze_scene("img", "pick object")
        # Static mock data, so always equal. The key test is verify_success which uses rng.
        assert r1 == r2

    @pytest.mark.asyncio
    async def test_mock_vlm_verify_deterministic_with_seed(self):
        from rove.adapters.mock_vlm import MockVLMAdapter

        ctx = {"completed_stages": ["perceive", "plan", "act"], "plan": {"target_object": "box"}}
        a = MockVLMAdapter(config={"seed": 42, "mock_latency_ms": [0, 0]})
        b = MockVLMAdapter(config={"seed": 42, "mock_latency_ms": [0, 0]})

        r1 = await a.verify_success("img", "img", "pick box", context=ctx)
        r2 = await b.verify_success("img", "img", "pick box", context=ctx)
        assert r1.success == r2.success
        assert r1.confidence == r2.confidence

    @pytest.mark.asyncio
    async def test_mock_vla_deterministic_with_seed(self):
        from rove.adapters.mock_vla import MockVLAAdapter

        a = MockVLAAdapter(config={"seed": 42, "mock_latency_ms": [0, 0]})
        b = MockVLAAdapter(config={"seed": 42, "mock_latency_ms": [0, 0]})

        r1 = await a.predict_action("img", "pick object")
        r2 = await b.predict_action("img", "pick object")
        assert r1.actions == r2.actions
        assert r1.confidence == r2.confidence

    @pytest.mark.asyncio
    async def test_different_seeds_produce_different_results(self):
        from rove.adapters.mock_vla import MockVLAAdapter

        a = MockVLAAdapter(config={"seed": 1, "mock_latency_ms": [0, 0]})
        b = MockVLAAdapter(config={"seed": 999, "mock_latency_ms": [0, 0]})

        r1 = await a.predict_action("img", "pick object")
        r2 = await b.predict_action("img", "pick object")
        # Extremely unlikely to be identical with different seeds
        assert r1.actions != r2.actions


def _make_result(
    sid="s1",
    success=True,
    latency=100.0,
    failure_stage=None,
    failure_category=None,
    perceive_task_relevant=None,
    plan_confidence=0.8,
    verify_success=True,
    plan_reasoning="",
    verify_reasoning="",
    models=None,
):
    """Helper to build a strategy result dict for testing."""
    stages = [
        {
            "stage": "perceive",
            "status": "completed",
            "output": {
                "task_relevant": perceive_task_relevant
                if perceive_task_relevant is not None
                else ["obj"],
                "raw_response": "saw objects",
            },
        },
        {
            "stage": "plan",
            "status": "completed",
            "output": {"confidence": plan_confidence, "reasoning": plan_reasoning},
        },
        {
            "stage": "act",
            "status": "completed",
            "output": {},
        },
        {
            "stage": "verify",
            "status": "completed",
            "output": {
                "success": verify_success,
                "reasoning": verify_reasoning,
                "stage_checks": [
                    {
                        "stage": "perceive",
                        "passed": True,
                        "confidence": 0.9,
                        "reasoning": "objects found",
                    },
                ],
            },
        },
    ]
    return {
        "strategy_id": sid,
        "display_name": sid,
        "success": success,
        "total_latency_ms": latency,
        "failure_stage": failure_stage,
        "failure_category": failure_category,
        "stages": stages,
        "models": models
        or {"perceive": "mock-vlm", "plan": "mock-vlm", "act": "mock-vla", "verify": "mock-vlm"},
    }


class TestRunInsights:
    def test_empty_results(self):
        insights = compute_run_insights([])
        assert insights["failure_breakdown"] == {}
        assert insights["stage_health"] == {}
        assert insights["model_comparison"] == []
        assert insights["degradation_signals"] == []
        assert "No results" in insights["top_finding"]
        assert insights["reasoning_trail"] == {}

    def test_all_success(self):
        results = [
            _make_result("s1", success=True, latency=100),
            _make_result("s2", success=True, latency=200),
        ]
        insights = compute_run_insights(results)
        assert insights["failure_breakdown"] == {}
        assert "All 2 strategies passed" in insights["top_finding"]
        assert insights["stage_health"]["verify"] == 1.0

    def test_failure_breakdown(self):
        results = [
            _make_result(
                "s1", success=False, failure_category="perceive_miss", verify_success=False
            ),
            _make_result(
                "s2", success=False, failure_category="perceive_miss", verify_success=False
            ),
            _make_result(
                "s3", success=False, failure_category="plan_low_confidence", verify_success=False
            ),
        ]
        insights = compute_run_insights(results)
        assert insights["failure_breakdown"]["perceive_miss"] == 2
        assert insights["failure_breakdown"]["plan_low_confidence"] == 1

    def test_stage_health_scores(self):
        results = [
            _make_result("s1", success=True, plan_confidence=0.9),
            _make_result("s2", success=False, plan_confidence=0.3, verify_success=False),
        ]
        insights = compute_run_insights(results)
        # perceive: both have task_relevant -> 1.0
        assert insights["stage_health"]["perceive"] == 1.0
        # plan: avg(0.9, 0.3) = 0.6
        assert insights["stage_health"]["plan"] == 0.6
        # verify: 1 success, 1 fail -> 0.5
        assert insights["stage_health"]["verify"] == 0.5

    def test_model_comparison(self):
        results = [
            _make_result(
                "s1", success=True, latency=100, models={"perceive": "gpt4o", "verify": "gpt4o"}
            ),
            _make_result(
                "s2",
                success=False,
                latency=200,
                models={"perceive": "qwen", "verify": "qwen"},
                verify_success=False,
            ),
        ]
        insights = compute_run_insights(results)
        mc = {m["model_id"]: m for m in insights["model_comparison"]}
        assert mc["gpt4o"]["success_rate"] == 1.0
        assert mc["qwen"]["success_rate"] == 0.0

    def test_degradation_signals(self):
        results = [
            _make_result(
                "s1",
                success=False,
                failure_stage="perceive",
                failure_category="perceive_miss",
                perceive_task_relevant=[],
                verify_success=False,
            ),
            _make_result(
                "s2",
                success=False,
                failure_stage="perceive",
                failure_category="perceive_miss",
                perceive_task_relevant=[],
                verify_success=False,
            ),
        ]
        insights = compute_run_insights(results)
        assert any("perceive stage failed" in s for s in insights["degradation_signals"])

    def test_top_finding_all_failed(self):
        results = [
            _make_result(
                "s1", success=False, failure_category="perceive_miss", verify_success=False
            ),
        ]
        insights = compute_run_insights(results)
        assert "All 1 strategies failed" in insights["top_finding"]
        assert "Perception Miss" in insights["top_finding"]

    def test_reasoning_trail(self):
        results = [
            _make_result(
                "s1", plan_reasoning="approach from left", verify_reasoning="task completed"
            ),
        ]
        insights = compute_run_insights(results)
        trail = insights["reasoning_trail"]
        assert "s1" in trail
        assert trail["s1"]["plan_reasoning"] == "approach from left"
        assert trail["s1"]["verify_reasoning"] == "task completed"
        assert len(trail["s1"]["stage_checks"]) == 1

    def test_top_finding_partial_success(self):
        results = [
            _make_result("s1", success=True),
            _make_result("s2", success=False, verify_success=False),
            _make_result("s3", success=False, verify_success=False),
        ]
        insights = compute_run_insights(results)
        assert "1/3 strategies passed" in insights["top_finding"]
