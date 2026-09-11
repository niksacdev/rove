# ROVE Demo Gallery — Sample Robot Workspace Images

Sample images for testing ROVE's evaluation pipeline. Organized by scene type with two data files serving different purposes.

## Data Files

| File | Purpose | Used by |
|------|---------|---------|
| `manifest.json` | **Demo tasks** — image + imperative task description for pipeline input | Dashboard gallery, `POST /api/evaluate` |
| `eval_qa.json` | **Benchmark QA** — VQA questions with ground truth answers for scoring | Automated evaluation, verify stage benchmarking |

### manifest.json (Demo Tasks)

Each entry maps an image to a manipulation task that feeds the pipeline:

```json
{
  "filename": "kitchen/robo2vlm_016.jpg",
  "scene_type": "kitchen",
  "task": "Put the marker inside the cup",
  "source": {
    "dataset": "Robo2VLM-1",
    "license": "Apache-2.0",
    "id": "droid_put_the_marker_inside_the_cup_7432_q23"
  }
}
```

### eval_qa.json (Benchmark QA)

Each entry has a VQA question with ground truth for scoring VLM accuracy:

```json
{
  "filename": "kitchen/robo2vlm_016.jpg",
  "question": "The robot is to put the marker inside the cup. Has the robot successfully completed the task?",
  "choices": ["No", "Yes", "Cannot be determined", "Task was not attempted"],
  "correct_answer_index": 1,
  "correct_answer_text": "Yes",
  "task": "Put the marker inside the cup",
  "source_id": "droid_put_the_marker_inside_the_cup_7432_q23"
}
```

## Scene Types

| Folder | Description | Count |
|--------|-------------|-------|
| `tabletop/` | Flat surface manipulation — pick, place, push objects | 4 |
| `kitchen/` | Kitchen environment — mugs, cups, bowls, markers | 4 |
| `drawer-cabinet/` | Drawer/cabinet interaction — open, close, reach into | 4 |
| `bin-picking/` | Container sorting — boxes, bins, baskets, plush toys | 4 |
| `assembly/` | Assembly tasks — peg-in-hole insertion | 4 |

## Demo Flow

1. User picks an image from the gallery (loaded from `manifest.json`)
2. Task field is auto-populated with the imperative task description
3. User selects strategies (pipeline configurations from `rove.yaml`)
4. Pipeline runs: perceive → plan → act → verify
5. Results compared across strategies

## Benchmark Flow

1. Load `eval_qa.json` entries
2. For each entry, send image + question to VLM
3. Compare VLM answer against `correct_answer_text`
4. Score accuracy across question types (task success, grasp stability, action phase, spatial reasoning)

## Attribution

All images sourced from publicly available robotics research datasets.

### Robo2VLM-1

- **Source**: [keplerccc/Robo2VLM-1](https://huggingface.co/datasets/keplerccc/Robo2VLM-1) on HuggingFace
- **License**: Apache 2.0
- **Paper**: [Robo2VLM: Visual Question Answering from Large-Scale In-the-Wild Robot Manipulation Datasets](https://arxiv.org/abs/2505.15517)
- **Images used**: 20 samples from the test split, classified by scene type
- **Original data sources**: DROID, Fractal, Stanford KUKA, VIOLA datasets

For dataset citations, use the authors' [paper](https://arxiv.org/abs/2505.15517).
See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for the LIBERO samples,
Panda robot assets, licenses and local modifications. Demo task rewrites and
selected frames are not a calibrated task-success benchmark.

## Adding More Images

Add entries to both `manifest.json` and `eval_qa.json`. Required fields:

**manifest.json**: `filename`, `scene_type`, `task`, `source.dataset`, `source.license`

**eval_qa.json**: `filename`, `question`, `choices`, `correct_answer_index`, `correct_answer_text`, `task`
