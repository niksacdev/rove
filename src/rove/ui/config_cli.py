"""Inspect and validate the same configuration loaded by ROVE execution."""

import argparse
from pathlib import Path

from rove.models.config import load_config
from rove.ui.cli_support import emit, run_cli


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["show", "models", "validate"])
    parser.add_argument("--config", type=Path, default=Path("rove.yaml"))
    args = parser.parse_args(argv)

    def execute():
        config = load_config(args.config)
        if args.action == "models":
            return {
                "endpoints": {
                    key: value.model_dump(mode="json") for key, value in config.endpoints.items()
                }
            }
        if args.action == "validate":
            return {
                "valid": True,
                "strategies": len(config.strategies),
                "endpoints": len(config.endpoints),
            }
        return config.model_dump(mode="json")

    run_cli(parser, lambda: emit(execute()))
