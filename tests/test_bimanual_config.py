import json

import pytest

from rove.bimanual.config import Environment, Strategy, build_spec, tree_digest, validate_profile
from rove.evaluation.models import EvaluationConfig


def profile():
    return EvaluationConfig(
        environment=Environment(source="/abc", python="/abc/python").model_dump(),
        strategies={
            "abc-vla": Strategy(
                kind="abc_vla", label="ABC", checkpoint="/weights.pt", python="/abc/python"
            ).model_dump(),
            "pi05": Strategy(
                kind="pi05", label="Pi", checkpoint="/pi", python="/pi/python"
            ).model_dump(),
        },
    )


def test_scene_cases_and_policy_repeats_are_distinct():
    spec, config = build_spec(
        profile(),
        name="Bottles",
        strategy_ids=["abc-vla", "pi05"],
        reset_seeds=[2, 9],
        policy_seeds=[1, 3, 5],
        max_steps=3540,
    )
    assert spec.planned_trials == 12
    assert [c.inputs["reset_seed"] for c in spec.tasks] == [2, 9]
    assert spec.seeds == [1, 3, 5]
    assert config.environment["physics_dt"] == 0.002
    assert spec.ks == [1, 3]
    assert all(c.reference == {} for c in spec.tasks)


@pytest.mark.parametrize("scenes", [[1, 1], [-1], [True], [2**32]])
def test_invalid_scene_seeds(scenes):
    with pytest.raises(ValueError):
        build_spec(
            profile(),
            name="Bottles",
            strategy_ids=["abc-vla"],
            reset_seeds=scenes,
            policy_seeds=[0],
            max_steps=3540,
        )


def test_unregistered_profiles_cannot_appear_ready():
    result = validate_profile(profile(), verify_assets=False)
    assert not result["ready"]
    assert all(not row["ready"] for row in result["strategies"])


def test_fingerprint_includes_weights_and_normalization(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"original")
    (tmp_path / "config.json").write_text(json.dumps({"actions": 14}))
    first = tree_digest(tmp_path)
    (tmp_path / "normalization.json").write_text('{"scale":1}')
    assert tree_digest(tmp_path) != first


def test_changed_checkpoint_not_just_model_name(monkeypatch, tmp_path):
    checkpoint = tmp_path / "weights.pt"
    checkpoint.write_bytes(b"trained")
    config = profile()
    config.environment.update(source_digest="source", runtime_digest="runtime")
    config.strategies = {"abc-vla": config.strategies["abc-vla"]}
    config.strategies["abc-vla"].update(
        checkpoint=str(checkpoint),
        checkpoint_digest=tree_digest(checkpoint),
        runtime_digest="runtime",
    )
    monkeypatch.setattr("rove.bimanual.config.source_digest", lambda _: "source")
    monkeypatch.setattr("rove.bimanual.config.runtime_digest", lambda _: "runtime")
    monkeypatch.setattr("rove.bimanual.config.runtime_health", lambda *_: [])
    assert validate_profile(config)["ready"]
    checkpoint.write_bytes(b"different")
    assert not validate_profile(config)["ready"]
