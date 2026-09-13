# Compare strategies on an ABC observation

Use a local episode from the [ABC team's public preview or conversion tools](https://github.com/amazon-far/abc)
to create a ROVE case. No episode data, model weights or copied upstream code are
bundled here. This example uses a source checkout containing `import-abc`; an older
published ROVE package may not include it.

## Prepare an episode

Follow the upstream README's `prepare.py` preview instructions to obtain their small
converted sample. For already authorized local ABC-130k MCAPs, their
`export_mcap.py` converts recordings into the same layout. Access to the full
[Hugging Face dataset](https://huggingface.co/datasets/XDOF/ABC-130k) is gated; ROVE
does not accept conditions or download gated files for you.

Select one converted episode directory containing:

```text
episode_<id>/
  episode_metadata.json
  combined_camera-images-rgb.mp4
  states_actions.bin
```

Use a validation episode when available. Keep its real/sim domain and original split
explicit. Supply a 40-character Git commit or 64-character SHA-256 identifying the
source revision/archive. The importer also hashes each file's bytes; a supplied hash
is a provenance declaration, not proof that ROVE verified the remote source.

## Preview and import

Replace the episode directory, ID and revision values with your selected source.
These examples assume a real validation episode; use the values matching your data.
Install `ffmpeg` and `ffprobe` locally before previewing an episode.

```sh
rove data case import-abc /path/to/val_real/episode_ID \
  --episode-id episode_ID --source-revision YOUR_SOURCE_REVISION \
  --split val --domain real --frame-index 0 --camera top \
  --preview --store .rove/abc-study --output abc-preview.json
```

Inspect the preview's observation, state, source details and limitations. Then reuse
the exact preview hash from that output:

```sh
rove data case import-abc /path/to/val_real/episode_ID \
  --episode-id episode_ID --source-revision YOUR_SOURCE_REVISION \
  --split val --domain real --frame-index 0 --camera top \
  --preview-hash YOUR_PREVIEW_HASH \
  --store .rove/abc-study --output abc-import.json
```

Preview creates no durable case. Import creates a normal immutable case; repeating an
identical import reuses its identity. `--camera combined` retains the exported camera
stack when a strategy can use it. A left/right selection requires that camera in the
episode. Unknown source split or domain should use `unknown`, never a guessed value.

The browser equivalent is **Cases → Import cases → ABC episode**. Upload the three
files, supply the same selection/source information, preview, and import. The case
joins the campaign's ordinary case selection. Metadata is limited to 256 KiB;
video and state/action files are each limited to 64 MiB.

## Compare and retain evidence

Use the returned case revision with your configured strategy IDs:

```sh
rove trial run --case YOUR_CASE_REVISION \
  --strategy YOUR_FIRST_STRATEGY --strategy YOUR_SECOND_STRATEGY \
  --store .rove/abc-study --config rove.yaml
```

Choose strategies compatible with a selected image and the available robot context.
Imported joint state does not supply a verified robot model or simulator. For plan
quality, review the proposed outputs against criteria you have checked against the
observation. The full demonstration is reference material; its completion is not a
success result for either new strategy.

Use the existing [campaign and CLI workflow](../../docs/product/cli-ui-parity.md) to
repeat trials, review uncertain outputs, save a case collection, set a baseline and
compare later strategy revisions. The [CI example](../ci/README.md) shows how to retain
study state and reports between automated evaluations.

## What this does not run

This imports a recorded observation; it does not execute ABC-DiT, ABC-VLA, MuJoCo or
hardware. The upstream `eval_policy.py` provides a useful future source of independent
simulation rollout evidence, but a ROVE adapter for those reports is not included.
No physical success, action-execution or raw-sensor-completeness claim follows from
importing the three converted files. See the
[product contract](../../docs/product/abc-dataset-support.md) for evidence boundaries.
