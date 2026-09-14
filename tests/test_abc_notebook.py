"""Offline checks for the executable tutorial, without training/model downloads."""

import ast
import importlib.util
import json
import sys
import textwrap
import uuid
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "abc_xdof" / "hello_abc.ipynb"
spec = importlib.util.spec_from_file_location("abc_tutorial", NOTEBOOK.with_name("tutorial.py"))
tutorial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tutorial)


def test_notebook_is_executable_python_and_ships_without_outputs():
    notebook = json.loads(NOTEBOOK.read_text())
    assert notebook["nbformat"] == 4
    assert len({c["id"] for c in notebook["cells"]}) == len(notebook["cells"])
    sources = []
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [] and cell["execution_count"] is None
            source = "".join(cell["source"])
            ast.parse(source)
            sources.append(source)
    combined = "\n".join(sources)
    assert "RUN_GPU = False" in combined
    assert "RUN_FINE_TUNING = False" in combined
    # Code executed inside ABC's environment must compile too.
    tree = ast.parse(combined)
    train_args = next(
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.List)
        and any(isinstance(t, ast.Name) and t.id == "training_args" for t in n.targets)
    )
    save_index = next(
        i
        for i, n in enumerate(train_args.elts)
        if isinstance(n, ast.Constant) and n.value == "--ckpt-every"
    )
    assert ast.unparse(train_args.elts[save_index + 1]) == "str(TRAIN_STEPS)"
    script = next(
        n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "prediction_source" for t in n.targets)
    )
    ast.parse(script)
    # The GPU prompt-resolution script is dedented before being written.
    prompt_script = next(
        n.args[0].args[0].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "prompt_script"
    )
    ast.parse(textwrap.dedent(prompt_script))


def episode(tmp_path, *, columns=28, metadata_steps=2):
    np.arange(2 * columns, dtype=np.float64).tofile(tmp_path / "states_actions.bin")
    tutorial.write_json(
        tmp_path / "episode_metadata.json",
        {"num_steps": metadata_steps, "cameras": ["top", "left", "right"]},
    )
    return tmp_path


def test_episode_keeps_references_out_of_current_state(tmp_path):
    _, state, reference = tutorial.read_episode(episode(tmp_path))
    assert state.shape == reference.shape == (2, 14)
    assert state[0].tolist() == list(range(14))
    assert reference[0].tolist() == list(range(14, 28))


@pytest.mark.parametrize("columns,steps", [(27, 2), (28, 3)])
def test_episode_rejects_malformed_or_misaligned_data(tmp_path, columns, steps):
    with pytest.raises(ValueError):
        tutorial.read_episode(episode(tmp_path, columns=columns, metadata_steps=steps))


def test_process_failure_is_preserved_and_stops_the_notebook(tmp_path):
    log = tmp_path / "execution.log"
    with pytest.raises(RuntimeError, match="exit 7"):
        tutorial.run_command(
            [sys.executable, "-c", "print('before failure'); raise SystemExit(7)"],
            cwd=tmp_path,
            log=log,
        )
    assert "before failure" in log.read_text()
    receipt = json.loads(log.with_suffix(".command.json").read_text())
    assert receipt["status"] == "failed" and receipt["returncode"] == 7


def test_command_arguments_remain_literal_without_shell_expansion(tmp_path):
    marker = tmp_path / "should-not-exist"
    argument = f"value; touch {marker}"
    log = tmp_path / "literal.log"
    tutorial.run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", argument], cwd=tmp_path, log=log
    )
    assert log.read_text().strip() == argument
    assert not marker.exists()


def result():
    return {
        "format": "abc_minimal_sim_eval/v1",
        "task": tutorial.TASK,
        "num_worlds": 1,
        "prompt": "sim put bottles in bin",
        "resolved_physics": {"control_hz": 30},
        "config": {
            "num_chunks": 236,
            "execute_chunk_dim": 15,
            "policy_seed": 0,
            "camera_backend": "mjwarp",
            "parallel_worlds": 0,
            "rtc": False,
        },
        "worlds": [
            {
                "success": True,
                "final_success": False,
                "world_seed": 11,
                "randomization": {"bottle_count": 3},
            }
        ],
    }


def test_result_preserves_ever_success_and_final_success(tmp_path):
    tutorial.write_json(
        tmp_path / "execution.command.json", {"status": "completed", "returncode": 0}
    )
    tutorial.write_json(tmp_path / "summary.json", result())
    world = tutorial.read_result(tmp_path)["worlds"][0]
    assert world["success"] and not world["final_success"]


def test_stale_summary_from_failed_command_is_not_accepted(tmp_path):
    tutorial.write_json(tmp_path / "execution.command.json", {"status": "failed", "returncode": 1})
    tutorial.write_json(tmp_path / "summary.json", result())
    with pytest.raises(ValueError, match="unsuccessful"):
        tutorial.read_result(tmp_path)


def test_missing_assessment_is_not_replaced_with_zero(tmp_path):
    data = result()
    del data["worlds"][0]["success"]
    tutorial.write_json(
        tmp_path / "execution.command.json", {"status": "completed", "returncode": 0}
    )
    tutorial.write_json(tmp_path / "summary.json", data)
    with pytest.raises(ValueError, match="Missing measured"):
        tutorial.read_result(tmp_path)


def test_comparison_flags_changed_prompt_backend_and_actual_reset():
    baseline = result()
    candidate = deepcopy(baseline)
    candidate["prompt"] = "sim throw bottles in bin"
    candidate["config"]["parallel_worlds"] = 10
    candidate["worlds"][0]["world_seed"] = 100011
    notes = tutorial.comparison_notes([baseline], [candidate])
    assert any("prompt" in n for n in notes)
    assert any("parallel_worlds" in n for n in notes)
    assert any("world_seed" in n for n in notes)
    assert tutorial.comparison_notes([baseline], [baseline]) == []


@pytest.mark.parametrize(
    "error,status", [(RuntimeError("failed"), "failed"), (KeyboardInterrupt(), "interrupted")]
)
def test_failed_notebook_rerun_invalidates_prior_success(tmp_path, error, status):
    notebook = json.loads(NOTEBOOK.read_text())
    source = "".join(next(c for c in notebook["cells"] if c["id"] == "cell-14")["source"])

    def fail(*args, **kwargs):
        raise error

    scope = {
        "RUN_GPU": True,
        "RUN_FINE_TUNING": False,
        "BASELINE": [result()],
        "CANDIDATE": [result()],
        "STAGES": {"evaluation": "completed"},
        "SESSION": tmp_path,
        "uuid": uuid,
        "EVAL_SEEDS": [11],
        "PARENT": tmp_path / "model.pt",
        "PARENT_PROMPT": "prompt",
        "evaluation_args": tutorial.evaluation_args,
        "abc_command": fail,
        "tracked_stage": tutorial.tracked_stage,
    }
    with pytest.raises(type(error)):
        exec(compile(source, "notebook-episode-cell", "exec"), scope)
    assert scope["BASELINE"] == scope["CANDIDATE"] == []
    assert scope["STAGES"]["evaluation"] == status
