# GR00T N1.7, MuJoCo and MCAP lab

Open [the notebook](gr00t_n17_finetuning_mujoco_mcap_lab_v2.ipynb) for a learning
exercise covering SO100 demonstration inspection, MuJoCo fundamentals, optional
GR00T fine-tuning and LIBERO evaluation, and synthetic ROS 2 MCAP cleaning.
Browse the [notebook index](../README.md) for the ABC and XDOF lab.

This is a standalone notebook, not a GR00T integration with ROVE's evaluation
engine. Its source is included without saved outputs. Notebook structure and
code-cell syntax are checked, and regression tests exercise suite selection,
checkpoint scheduling and comparison guards. Its installations, training and
GPU evaluation cells have not been executed as part of this addition.

## Open with a dedicated kernel

Install Python 3.12, Git, uv and FFmpeg. Git LFS may be needed for upstream demo
assets. From the ROVE repository root:

```bash
uv venv --python 3.12 --seed notebooks/gr00t/.venv
uv pip install --python notebooks/gr00t/.venv/bin/python jupyterlab ipykernel
notebooks/gr00t/.venv/bin/python -m ipykernel install --sys-prefix \
  --name rove-gr00t --display-name "ROVE GR00T (Python 3.12)"
GR00T_LAB_ROOT="$PWD/notebooks/gr00t/.work" \
  notebooks/gr00t/.venv/bin/jupyter lab \
  notebooks/gr00t/gr00t_n17_finetuning_mujoco_mcap_lab_v2.ipynb --ip=127.0.0.1
```

Choose **ROVE GR00T (Python 3.12)** in Jupyter. In VS Code, use **Select Kernel →
Python Environments** and select `notebooks/gr00t/.venv/bin/python`. Selecting
Homebrew's system Python instead can produce the missing `ipykernel` message.
The seeded environment includes pip because the notebook installs its data and
simulation dependencies into the selected kernel.

## Choose the execution scope

The notebook separates macOS/CPU data, MuJoCo and MCAP exercises from the full
Linux/NVIDIA model path. Work through the prerequisite checks first. The full
path needs a compatible CUDA environment, appropriate model access and graphics
libraries for rendered simulation. Docker also needs NVIDIA GPU access.

Fine-tuning, larger dataset downloads and LIBERO evaluation have explicit flags
in the first configuration cell. Keep them disabled until their prerequisites are
ready. Some earlier setup cells still install packages, clone the upstream GR00T
repository and retrieve assets. Dependencies and the upstream checkout are not
pinned by this notebook; inspect the commands before execution and record the
versions used for any comparison.

The MCAP exercise generates synthetic data and exports a staging episode; it does
not by itself create a validated production training dataset. Short training runs
demonstrate mechanics and do not establish model quality or real-robot success.

## Compare checkpoints

Choose `LIBERO_SUITE` once. The notebook selects a matching example task from the
[upstream task list](https://github.com/NVIDIA/Isaac-GR00T/blob/main/examples/LIBERO/README.md)
and uses it in both rollout command templates. The default `goal` suite evaluates
putting the bowl on the plate; suite `10` uses the stove/moka task.

The 100-step learning exercise now saves checkpoints at steps 50 and 100. The
comparison requires two existing, distinct checkpoint directories, including
when paths are symlink aliases. Missing or identical checkpoints block comparison
command generation and the before/after plot. With valid checkpoints, enable
`RUN_MUJOCO_BEFORE_AFTER` to print each server/client command pair for manual
execution with the same task, seed and episode count, and separate video folders.

## Keep generated files local

The launch command puts downloads, model checkpoints and generated data under
the ignored `.work/` folder. The notebook's default `gr00t_lab/` folder is also
ignored when it is created beside the notebook. Keep credentials outside notebook
cells, and clear outputs before committing or sharing an executed copy.
