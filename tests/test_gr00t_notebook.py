"""Exercise notebook comparison controls without installing or running GR00T."""

import ast
import json
import re
import shlex
from pathlib import Path

import pytest

NOTEBOOK = (
    Path(__file__).resolve().parents[1]
    / "notebooks/gr00t/gr00t_n17_finetuning_mujoco_mcap_lab_v2.ipynb"
)


def code_cells():
    return [
        "".join(cell["source"])
        for cell in json.loads(NOTEBOOK.read_text())["cells"]
        if cell["cell_type"] == "code"
    ]


def cell_containing(marker):
    return next(cell for cell in code_cells() if marker in cell)


def notebook_functions():
    names = {
        "list_checkpoints",
        "checkpoint_save_interval",
        "validate_checkpoint_pair",
        "compare_success_rates",
    }
    definitions = [
        statement
        for cell in code_cells()
        for statement in ast.parse(cell).body
        if isinstance(statement, ast.FunctionDef) and statement.name in names
    ]
    namespace = {"Path": Path, "re": re, "shlex": shlex}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(NOTEBOOK), "exec"), namespace)
    return namespace


def comparison_context(tmp_path, suite="goal", requested=False):
    namespace = notebook_functions()
    output = tmp_path / "checkpoints"
    output.mkdir(exist_ok=True)
    namespace.update(
        LIBERO_SUITE=suite,
        REPO_DIR=tmp_path / "upstream with spaces",
        WORK_ROOT=tmp_path / "workspace with spaces",
        COMPLEX_OUT=output,
        RUN_MUJOCO_BEFORE_AFTER=requested,
        uv="uv",
    )
    exec(cell_containing("LIBERO_REPOS ="), namespace)
    return namespace


def test_notebook_has_valid_code_and_no_saved_outputs():
    notebook = json.loads(NOTEBOOK.read_text())
    assert notebook["nbformat"] == 4
    assert len(notebook["cells"]) == 87
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["execution_count"] is None
            assert cell["outputs"] == []
            ast.parse("".join(cell["source"]))


@pytest.mark.parametrize(
    ("suite", "task"),
    [
        ("goal", "put_the_bowl_on_the_plate"),
        ("object", "pick_up_the_alphabet_soup_and_place_it_in_the_basket"),
        ("spatial", "pick_up_the_black_bowl_from_table_center_and_place_it_on_the_plate"),
        ("10", "KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it"),
    ],
)
def test_selected_suite_reaches_both_rollout_templates(tmp_path, capsys, suite, task):
    namespace = comparison_context(tmp_path, suite, requested=True)
    for step in (50, 100):
        (namespace["COMPLEX_OUT"] / f"checkpoint-{step}").mkdir()
    namespace.update(RUN_LIBERO_SIM_SETUP=False, FULL_GROOT_SUPPORTED=False)
    exec(
        cell_containing('setup_cmd = ["bash", "gr00t/eval/sim/LIBERO/setup_libero.sh"]'), namespace
    )
    setup_text = capsys.readouterr().out
    assert f"--env-name libero_sim/{task}" in setup_text
    exec(cell_containing("COMPARISON_PAIR = None"), namespace)
    output = capsys.readouterr().out
    commands = [
        shlex.split(line)
        for line in output.splitlines()
        if "run_gr00t_server.py" in line or "rollout_policy.py" in line
    ]
    assert len(commands) == 4
    servers, clients = commands[::2], commands[1::2]
    assert [Path(cmd[cmd.index("--model-path") + 1]).name for cmd in servers] == [
        "checkpoint-50",
        "checkpoint-100",
    ]
    for cmd in clients:
        assert cmd[cmd.index("--env-name") + 1] == f"libero_sim/{task}"
        assert cmd[cmd.index("--seed") + 1] == "42"
        assert cmd[cmd.index("--n-episodes") + 1] == "10"
    assert (
        clients[0][clients[0].index("--video-dir") + 1]
        != clients[1][clients[1].index("--video-dir") + 1]
    )


def test_unknown_suite_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown LIBERO suite"):
        comparison_context(tmp_path, "typo")


@pytest.mark.parametrize("steps", [2, 3, 100, 101])
def test_training_command_saves_two_checkpoints(tmp_path, steps):
    namespace = comparison_context(tmp_path)
    namespace.update(
        COMPLEX_MAX_STEPS=steps,
        GLOBAL_BATCH_SIZE=32,
        RUN_COMPLEX_FINETUNE=False,
        FULL_GROOT_SUPPORTED=False,
    )
    exec(cell_containing("complex_cmd = ["), namespace)
    cmd = namespace["complex_cmd"]
    interval = int(cmd[cmd.index("--save-steps") + 1])
    assert len(range(interval, steps + 1, interval)) >= 2
    assert cmd[cmd.index("--save-total-limit") + 1] == "2"
    if steps == 100:
        assert interval == 50


@pytest.mark.parametrize("steps", [0, 1, -1, True, 2.5])
def test_invalid_comparison_training_budget_is_rejected(steps):
    with pytest.raises(ValueError, match="at least 2"):
        notebook_functions()["checkpoint_save_interval"](steps)


@pytest.mark.parametrize("count", [0, 1])
def test_no_checkpoints_keeps_data_path_usable_but_blocks_requested_comparison(tmp_path, count):
    namespace = comparison_context(tmp_path)
    if count:
        (namespace["COMPLEX_OUT"] / "checkpoint-100").mkdir()
    exec(cell_containing("COMPARISON_PAIR = None"), namespace)
    assert namespace["COMPARISON_PAIR"] is None
    assert namespace["LATE_CKPT"] is None
    namespace["RUN_MUJOCO_BEFORE_AFTER"] = True
    with pytest.raises(ValueError, match="Two checkpoints are required"):
        exec(cell_containing("COMPARISON_PAIR = None"), namespace)


def test_duplicate_checkpoint_alias_blocks_commands_and_plot(tmp_path):
    namespace = comparison_context(tmp_path, requested=True)
    early = namespace["COMPLEX_OUT"] / "checkpoint-50"
    early.mkdir()
    late = namespace["COMPLEX_OUT"] / "checkpoint-100"
    late.symlink_to(early, target_is_directory=True)
    with pytest.raises(ValueError, match="different checkpoints"):
        exec(cell_containing("COMPARISON_PAIR = None"), namespace)
    with pytest.raises(ValueError, match="different checkpoints"):
        namespace["compare_success_rates"]("success rate: 0.2", "success rate: 0.9")


def test_missing_checkpoint_directory_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="must exist"):
        notebook_functions()["validate_checkpoint_pair"](tmp_path, tmp_path / "missing")


def test_checkpoint_listing_ignores_unfinished_and_non_numeric_entries(tmp_path):
    for name in ("checkpoint-100", "checkpoint-50", "checkpoint-pending", "old-checkpoint-1"):
        (tmp_path / name).mkdir()
    (tmp_path / "checkpoint-75").write_text("not a directory")
    assert [p.name for p in notebook_functions()["list_checkpoints"](tmp_path)] == [
        "checkpoint-50",
        "checkpoint-100",
    ]
