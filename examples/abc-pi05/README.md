# ABC-VLA and adapted pi0.5

This example turns prepared ABC/XDOF demonstrations into a LeRobot dataset,
produces a pi0.5 training plan, and evaluates compatible checkpoints using ROVE's
existing Trials and Campaigns. Start with the
[comparison guide](../../docs/product/abc-pi05-comparison.md) for the user journey,
success criterion, reports and evidence boundaries.

## 1. Prepare isolated runtimes

Keep ROVE, ABC and LeRobot in separate Python environments. ABC and LeRobot have
different ML dependencies. Model execution and training require a Linux NVIDIA
GPU host; the ROVE application can run on that same host. Install ROVE there using
the repository's normal setup. Azure hosting is a future step.

On a headless Linux GPU host, select MuJoCo's EGL renderer before starting ROVE
or its evaluation commands:

```bash
export MUJOCO_GL=egl
```

The host needs NVIDIA drivers and working EGL libraries; a container also needs
access to the NVIDIA GPU and its graphics libraries. ROVE's child processes
inherit this setting. The dependency check does not prove that camera rendering
works: verify a short rendered simulator episode before launching a long job.

Clone ABC and check out the source used by this integration:

```bash
git clone https://github.com/amazon-far/abc.git
cd abc
git checkout d0e987b61b376f1a1b8777b19149f190b2ba6939
uv sync
uv run prepare.py --sim-task put_plastic_bottles_in_bin
```

Follow the pinned ABC repository's preparation instructions for the
`vla_abc130k_200000_v2.pt` checkpoint, its metadata, prepared demonstrations and
tokenizer assets. That checkpoint and its prior training already include ABC task
data; this example cannot establish that its task was unseen during pretraining.

From the ROVE repository, create the LeRobot environment:

```bash
uv venv --python 3.12 /path/to/lerobot-env
uv pip install --python /path/to/lerobot-env/bin/python \
  -r examples/abc-pi05/requirements.txt
```

Install FFmpeg/FFprobe for video conversion. Download a local snapshot of
[`lerobot/pi05_base`](https://huggingface.co/lerobot/pi05_base) and its required
tokenizer assets after accepting any upstream access conditions. Use the exact
snapshot directory as `--base-checkpoint`; the training receipt records its
weight and configuration hashes. The requirements pin LeRobot source, but are
not a complete transitive dependency lock. Evaluation fingerprints the installed
runtime when you register it.

Only register trusted local checkpoints. ABC's native PyTorch loader reads its
checkpoint format directly. Evaluation launches use offline Hugging Face settings:
missing checkpoints or tokenizer assets cause an error instead of a download.

## 2. Convert training demonstrations

The prepared cache is the directory containing native `train_sim`, `train_real`,
`val_sim` and `val_real` episode directories from ABC's preparation tooling.
Keep validation directories present so the converter can detect overlapping
episode IDs and content hashes. It rejects known validation splits, duplicates,
invalid camera layouts and misaligned frame counts.

```bash
/path/to/lerobot-env/bin/python examples/abc-pi05/dataset.py \
  --cache /path/to/prepared-cache \
  --output /path/to/abc-bottles-training \
  --repo-id local/abc-bottles \
  --split train_sim \
  --task-name sim_put_the_plastic_bottles_in_the_bin
```

The output is a native LeRobot v3 dataset with videos, Parquet tables, training
statistics and a ROVE provenance manifest. Large camera files stay outside trial
JSON records. Current support is the prepared 30 Hz top/left/right camera format,
14D state and absolute joint targets. Raw or stereo XDOF recordings need an
explicit mapping before they can use this converter.

## 3. Inspect and execute an adaptation plan

```bash
/path/to/lerobot-env/bin/python examples/abc-pi05/train.py \
  --dataset /path/to/abc-bottles-training \
  --base-checkpoint /path/to/local-pi05-base-snapshot \
  --output /path/to/pi05-bottles-v1 \
  --steps 30000 --batch-size 1
```

The command above prints the plan. Add `--execute` on the GPU host to train.
Start with a short `--steps 20` integration check before a substantial training
job. This short run does not establish useful specialization. The native trainer
saves the model and its processors at
`checkpoints/last/pretrained_model`, alongside a durable training receipt.

The recipe trains the action expert while freezing the vision backbone, uses
absolute 14D actions, and saves normalization with the checkpoint. It disables
uploads, W&B, image augmentation and simulator evaluation inside the trainer.
ROVE runs held-out simulation episodes separately after adaptation.

## 4. Register and compare

```bash
rove bimanual init --output abc-profile.json
```

Edit the generated profile with the ABC source directory, both Python
interpreters, the ABC checkpoint and the adapted pi0.5 checkpoint directory.
Confirm `environment.prompt` and the success interpretation. The released ABC
evaluation uses `sim throw plastic bottles in bin`; the selected demonstrations
use `sim put the plastic bottles in the bin`. The converter preserves the training
prompt. Both models receive the same declared evaluation prompt, so choose it
explicitly and retain it in the frozen profile. Do not silently give each model
its own wording. Testing the alternate prompt is a separate comparison revision,
not a matched continuation with an unchanged task instruction. Then:

```bash
rove bimanual setup --config abc-profile.json
rove bimanual doctor
rove bimanual trial --strategy abc-vla --strategy pi05 --scene 11 --seed 7
rove bimanual run --name "Bottles comparison" \
  --strategy abc-vla --strategy pi05 \
  --scene 11 --scene 29 --scene 47 --seed 7 --seed 17 --seed 37 \
  --output bottles-report.html
```

This campaign creates 18 recorded trials. Open **ABC bimanual comparison** from
Trials or Campaigns for the equivalent UI. Use a new strategy ID for a revised
checkpoint, preserve the scene seeds and scoring settings, and compare reports.
Changing assets after registration blocks execution until the new profile is
registered. Completed scored failures are evaluation results; execution errors
make the command fail after preserving its report, which supports CI automation.

## Validation achieved

On 2026-09-15, the pinned LeRobot converter wrote and reopened two actual ABC
training-preview episodes: 1,870 frames, three RGB cameras, 14D state/actions and
saved quantile statistics. The training arguments passed native configuration
parsing. A real ABC/MuJoCo episode executed 30 control steps with three rendered
cameras, two replans and 1.02 simulated seconds, preserving reset and trajectory
evidence. It used a synthetic hold-position policy and correctly did not complete
the bottle-placement task.

GPU training, ABC-VLA inference and adapted pi0.5 inference remain unvalidated.
The example supplies no learned-model ranking or physical robot performance claim.
