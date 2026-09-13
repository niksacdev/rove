"""Scriptable strategy inspection and immutable component revisions."""

import argparse
from pathlib import Path

from rove.models.config import load_config
from rove.strategies.revisions import (
    RevisionDraft,
    RevisionSave,
    list_revisions,
    parent,
    preview,
    save,
)
from rove.ui.cli_support import emit, read_document, run_cli


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "show", "revisions", "preview", "save"])
    parser.add_argument(
        "target", nargs="?", help="Strategy ID for show; JSON/YAML file for preview/save"
    )
    parser.add_argument("--config", type=Path, default=Path("rove.yaml"))
    args = parser.parse_args(argv)
    if args.action in {"show", "preview", "save"} and not args.target:
        parser.error("target is required")

    def execute():
        config = load_config(args.config)
        if args.action == "list":
            return {
                "strategies": {
                    key: value.model_dump(mode="json") for key, value in config.strategies.items()
                }
            }
        if args.action == "show":
            return parent(config, args.target)
        if args.action == "revisions":
            return {"revisions": list_revisions(args.config, config)}
        document = read_document(args.target)
        if args.action == "preview":
            return preview(config, RevisionDraft.model_validate(document))
        return save(args.config, RevisionSave.model_validate(document))

    run_cli(parser, lambda: emit(execute()))
