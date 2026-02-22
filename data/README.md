# ROVE Demo Gallery — Sample Robot Workspace Images

Sample images for testing ROVE's VLM/VLA evaluation pipeline. Organized by scene type for the demo image gallery.

## Scene Types

| Folder | Description | Count |
|--------|-------------|-------|
| `tabletop/` | Flat surface manipulation — pick, place, push objects | 4 |
| `kitchen/` | Kitchen environment — mugs, cups, markers | 4 |
| `drawer-cabinet/` | Drawer/cabinet interaction — open, close, reach into | 4 |
| `bin-picking/` | Container sorting — boxes, bins, baskets | 4 |
| `assembly/` | Assembly tasks — peg-in-hole, insertion | 4 |

## Usage

Each image has a paired task description in `manifest.json`. Load the manifest to get image paths and tasks:

```python
import json
from pathlib import Path

data_dir = Path("data")
manifest = json.loads((data_dir / "manifest.json").read_text())

for entry in manifest:
    print(f"{entry['filename']} — {entry['task_description']}")
```

## Attribution

All images in this directory are sourced from publicly available robotics research datasets. Proper attribution is required when using these images.

### Robo2VLM-1

- **Source**: [keplerccc/Robo2VLM-1](https://huggingface.co/datasets/keplerccc/Robo2VLM-1) on HuggingFace
- **License**: Apache 2.0
- **Paper**: [Robo2VLM: Visual Question Answering from Robotic Observations](https://arxiv.org/abs/2505.15517)
- **Authors**: Kepler Cite et al.
- **Images used**: 20 samples from the test split, classified by scene type
- **Usage**: Demo gallery for ROVE evaluation pipeline testing

If you use these images in publications or derived works, please cite the original dataset:

```bibtex
@article{robo2vlm2025,
  title={Robo2VLM: Visual Question Answering from Robotic Observations},
  author={Cite, Kepler and others},
  journal={arXiv preprint arXiv:2505.15517},
  year={2025}
}
```

## Adding More Images

To add images from additional datasets, update `manifest.json` with entries containing:
- `filename`: relative path from `data/` (e.g., `tabletop/new_image.jpg`)
- `scene_type`: one of `tabletop`, `kitchen`, `drawer-cabinet`, `bin-picking`, `assembly`
- `source_dataset`: dataset name for attribution
- `source_license`: license identifier (e.g., `Apache-2.0`, `CC-BY-4.0`)
- `task_description`: natural language task for the demo pipeline
