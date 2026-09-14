# Hello ABC with XDOF in Jupyter

Open [hello_abc.ipynb](hello_abc.ipynb) to inspect a real robot demonstration,
predict an action chunk with ABC-VLA, run complete simulator episodes, and
optionally fine-tune and compare a checkpoint. The default laptop path downloads
only ABC's small prepared real/sim preview and produces video, camera views,
state/action plots and a local provenance export.

This is a standalone learning example. It uses ABC directly and does not change
ROVE's application, dependencies, SQLite records or model adapters.

## Open the notebook

Install Python 3.12, Git, [uv](https://docs.astral.sh/uv/) and FFmpeg. On macOS,
FFmpeg is available through Homebrew (`brew install ffmpeg`); on Ubuntu use
`sudo apt-get install ffmpeg`. Then, from the ROVE repository root:

```bash
uv venv --python 3.12 notebooks/abc_xdof/.venv
uv pip install --python notebooks/abc_xdof/.venv/bin/python -r notebooks/abc_xdof/requirements.txt
notebooks/abc_xdof/.venv/bin/jupyter lab notebooks/abc_xdof/hello_abc.ipynb --ip=127.0.0.1
```

Choose **Python 3 (ipykernel)** and **Run all cells**. Jupyter stays authenticated
and bound to loopback; use an SSH tunnel when the notebook runs on a remote GPU
machine. Do not disable its token or expose the server publicly.

The notebook creates its own ignored `.work/` directory. The first execution
clones the pinned ABC source and downloads approximately 162 MB of compressed
preview data. Every execution creates a fresh session folder; prepared data and
weights remain cached. Keep the notebook beside `tutorial.py` when copying it.

## Enable inference and fine-tuning

For the GPU cells, run Jupyter on a Linux host with an NVIDIA GPU and a driver
compatible with ABC's CUDA 12.8 PyTorch build. Set `RUN_GPU = True` in the configuration
cell and run from the beginning. ABC installs into its own `.venv`; its roughly
8.8 GB VLA checkpoint and simulator assets are separate downloads. Upstream has
not established a minimum evaluation VRAM requirement.

After the baseline works, set `RUN_FINE_TUNING = True`. The default 20 training
steps on the preview demonstrate the mechanics, not effective specialization.
Training uses ABC's real/sim bottles mixture and inherited normalization. Full
VLA training has a much larger memory footprint than inference; `TRAIN_GPUS > 1`
selects FSDP. Size the budget from a measured run before increasing steps or data.

The comparison records both prompts and actual reset metadata. A changed prompt
or reset produces a visible comparison note. Three episodes are a small
demonstration, not a performance claim. The notebook never substitutes human
demonstrations, action agreement or model explanations for simulator outcomes.

## What is saved

Each session keeps prepared-data hashes, selected observation/frame, commands,
exit status, and any actual predictions, checkpoints, summaries and videos.
Model and data files remain ignored. Clear notebook outputs before committing
or sharing, especially after using customer data. W&B upload is disabled.

The public preview is an ABC-prepared export, not a pinned snapshot of the raw
Hugging Face release. For other tasks, ABC provides `scripts/export_hf_task.py`;
raw [XDOF/ABC-130k](https://huggingface.co/datasets/XDOF/ABC-130k) access requires
accepting its access conditions. Use a new cache and preserve the dataset
revision, episode identities and train/validation separation.

## Validation and scope

The source notebook contains no saved outputs. Tests validate code-cell syntax,
episode shape/metadata checks, process exit handling and report comparison
conditions. Actual local data-preview execution and the GPU validation boundary
are recorded in [the tutorial concept](../../docs/product/abc-jupyter-tutorial.md).

ABC source is pinned to
[`d0e987b61b376f1a1b8777b19149f190b2ba6939`](https://github.com/amazon-far/abc/tree/d0e987b61b376f1a1b8777b19149f190b2ba6939).
The kernel requirements pin direct packages; ABC resolves its own dependencies
from that source version and records the installed package list during GPU
execution. Neither environment is a claim of a fully frozen training system.

See [ABC licenses](https://github.com/amazon-far/abc#licenses) for code, Gemma-derived
VLA weights and simulator assets. This notebook provides no physical-robot
validation or arbitrary-robot compatibility.
