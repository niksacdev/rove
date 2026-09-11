"""Regression cases for local access and honest, incomplete physics evidence."""
import pytest
from httpx import ASGITransport, AsyncClient

from rove.api.app import app
from rove.orchestrator.failure_attribution import _get_evidence_value, attribute_failure


@pytest.mark.parametrize("host,origin,expected", [
    ("localhost:5001", None, 200),
    ("127.0.0.1:5001", "http://127.0.0.1:5001", 200),
    ("[::1]:5001", "http://[::1]:5001", 200),
    ("attacker.example:5001", None, 403),
    ("localhost:5001", "https://attacker.example", 403),
    ("localhost:5001", "null", 403),
    ("localhost:5001", "http://localhost:9000", 403),
])
async def test_local_browser_boundary(host, origin, expected):
    headers = {"Host": host}
    if origin:
        headers["Origin"] = origin
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get("/api/strategies", headers=headers)
    assert response.status_code == expected
    assert "access-control-allow-origin" not in response.headers


async def test_cross_origin_write_rejected_before_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post("/api/evaluate", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403


def test_unknown_evidence_cannot_be_overridden_by_legacy_summary():
    dyn = {"evidence": [{"field": "self_collision", "value": None, "confidence": "unknown"}],
           "summary": {"self_collision": True}, "self_collision": True}
    assert _get_evidence_value(dyn, "self_collision") is None
    assert attribute_failure([{"stage": "dynamics", "output": dyn}], False) == (None, None)
    dyn["evidence"][0].update(value=True, confidence="estimated")
    assert _get_evidence_value(dyn, "self_collision") is None
    dyn["evidence"][0]["confidence"] = "hard"
    assert _get_evidence_value(dyn, "self_collision") is True
    dyn["not_computed"] = [{"field": "self_collision", "reason": "missing geometry"}]
    assert _get_evidence_value(dyn, "self_collision") is None


def test_missing_mesh_never_passes_collision_or_torque(tmp_path):
    pytest.importorskip("mujoco")
    from rove.adapters.dynamics_mujoco import compute_dynamics
    urdf = tmp_path / "missing.urdf"
    urdf.write_text('''<robot name="fixture"><link name="base"/>
      <link name="arm"><collision><geometry><mesh filename="missing.stl"/></geometry></collision></link>
      <joint name="hinge" type="revolute"><parent link="base"/><child link="arm"/>
      <axis xyz="0 0 1"/><limit lower="-1" upper="1" effort="10" velocity="1"/></joint></robot>''')
    result = compute_dynamics(str(urdf), [[0.1], [0.1], [0.1]], initial_qpos=[0.0])
    for field in ("self_collision", "torque_feasible", "gravity_feasible"):
        evidence = next(e for e in result["evidence"] if e["field"] == field)
        assert evidence["value"] is None and evidence["confidence"] == "unknown"
        assert result["summary"][field] is None
    assert result["assumptions"]["model_simplified"] is True
    assert result["summary"]["joint_limits_ok"] is True


def test_valid_geometry_can_report_observed_contact(tmp_path):
    pytest.importorskip("mujoco")
    from rove.adapters.dynamics_mujoco import compute_dynamics
    model = tmp_path / "contacts.xml"
    model.write_text('''<mujoco><worldbody>
      <body name="a"><joint name="a" type="slide" axis="1 0 0" range="-1 1"/>
      <geom type="sphere" size="0.1"/></body>
      <body name="b" pos="0.1 0 0"><joint name="b" type="slide" axis="1 0 0" range="-1 1"/>
      <geom type="sphere" size="0.1"/></body>
      </worldbody></mujoco>''')
    result = compute_dynamics(str(model), [[0., 0.]], initial_qpos=[0., 0.])
    assert _get_evidence_value(result, "self_collision") is True
    assert result["summary"]["torque_feasible"] is None
    estimated = compute_dynamics(str(model), [[0., 0.]])
    assert _get_evidence_value(estimated, "self_collision") is None


def test_unknown_dynamics_not_counted_as_failure_or_success():
    from rove.orchestrator.insights import _compute_stage_health, _dynamics_all_ok
    dyn = {"evidence": [{"field": "self_collision", "value": None, "confidence": "unknown"}]}
    assert _dynamics_all_ok(dyn) is None
    health = _compute_stage_health([{"stages": [{"stage": "dynamics", "output": dyn}]}])
    assert "dynamics" not in health


def test_server_launcher_uses_loopback_without_reloader(monkeypatch):
    import sys

    import uvicorn

    from rove.ui.cli import main
    calls = []
    monkeypatch.setattr(sys, "argv", ["rove", "serve"])
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append(kw))
    main()
    assert calls == [{"host": "127.0.0.1", "port": 5001}]


def test_gallery_keeps_robot_and_control_metadata():
    from rove.api.app import _load_example_data
    example = _load_example_data("libero/libero_000.jpg")
    assert example.extras["robot"] == "panda"
    assert example.extras["control_space"] == "end_effector_delta"


def test_dynamics_uses_action_convention_not_untyped_proprioception(tmp_path):
    pytest.importorskip("mujoco")
    from rove.models import ActionPrediction, ActionSpace, PipelineContext
    from rove.orchestrator.pipeline import EvaluationPipeline
    model = tmp_path / "robot.xml"
    model.write_text('<mujoco><worldbody><body><joint type="hinge"/><geom type="sphere" size="0.1"/></body></worldbody></mujoco>')
    pipeline = EvaluationPipeline(urdf_path=str(model), compute_dynamics=True)
    ctx = PipelineContext(task="demo", proprioception=[1., 2., 3., 4., 5., 6., 7.])
    prediction = ActionPrediction(actions=[[0.01, 0., 0., 0., 0., 0., 1.]], num_steps=1, confidence=0.5, action_space=ActionSpace.EEF_DELTA)
    assert pipeline._compute_dynamics_analysis(ctx, prediction) is None
    assert ctx.dynamics_analysis["analysis_mode"] == "ee_delta_jacobian_ik"
    assert ctx.dynamics_analysis["assumptions"]["initial_state"] == "model_default"
    assert all(e["confidence"] != "hard" for e in ctx.dynamics_analysis["evidence"])
    prediction.action_space = ActionSpace.EEF_ABSOLUTE
    assert "does not support" in pipeline._compute_dynamics_analysis(ctx, prediction)


async def test_mock_report_retains_computed_evidence_and_repeats_actions():
    from scripts.demo import build_report
    first = await build_report(42)
    second = await build_report(42)
    first_stages = {s["stage"]: s["output"] for s in first["stages"]}
    second_stages = {s["stage"]: s["output"] for s in second["stages"]}
    assert first_stages["act"]["actions"] == second_stages["act"]["actions"]
    evidence = first_stages["verify"]["dynamics_analysis"]
    assert _get_evidence_value(evidence, "self_collision") is None
    assert evidence["summary"]["torque_feasible"] is None
    assert evidence["analysis_mode"] == "ee_delta_jacobian_ik"
