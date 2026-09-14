"""Freeze reviewed local VLA assets; never download weights or change an environment."""

import argparse
import importlib.util
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("tokenizer", type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[3] / "src/rove/adapters/lerobot_worker.py"
    spec = importlib.util.spec_from_file_location("rove_lerobot_manifest", source)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    for directory in (args.checkpoint, args.tokenizer):
        if not directory.is_dir():
            parser.error(f"Asset directory does not exist: {directory}")
    if not (args.checkpoint / "model.safetensors").is_file():
        parser.error("Checkpoint must contain model.safetensors")
    if not (args.tokenizer / "tokenizer_config.json").is_file():
        parser.error("Tokenizer must contain tokenizer_config.json")
    manifest = worker.asset_manifest(args.checkpoint, args.tokenizer)
    destination = args.checkpoint / "rove-manifest.json"
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Frozen {len(manifest['assets'])} assets: {destination}")
    print(f"Manifest SHA256: {worker.verify_asset_manifest(args.checkpoint, args.tokenizer)}")


if __name__ == "__main__":
    main()
