"""Shared local command input/output conventions; machine-readable stdout."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from rove.trials.snapshots import sanitize


def add_common(parser):
    parser.add_argument(
        "--store",
        type=Path,
        default=Path(".rove/benchmarks"),
        help="Local durable evaluation store",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("rove.yaml"),
        help="Strategy/endpoint configuration (includes revision catalog)",
    )


def read_document(path):
    limit = 16 * 1024 * 1024
    if str(path) == "-":
        content = sys.stdin.read(limit + 1)
    else:
        with Path(path).open() as stream:
            content = stream.read(limit + 1)
    if len(content.encode()) > limit:
        raise ValueError("Command input exceeds 16 MiB; use managed assets for large evidence")
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError:
        raise ValueError("Input must be valid JSON or YAML") from None


def emit(value, output=None):
    text = json.dumps(sanitize(value), indent=2, allow_nan=False) + "\n"
    if output is None or str(output) == "-":
        print(text, end="")
    else:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)


def run_cli(parser, action):
    try:
        return action()
    except ValidationError as error:
        # Pydantic's default string includes rejected input values, which can
        # contain private task data or host confirmation tokens.
        fields = [".".join(map(str, item["loc"])) + ": " + item["type"] for item in error.errors()]
        parser.error("Invalid command input: " + "; ".join(fields))
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        parser.error(str(error))
