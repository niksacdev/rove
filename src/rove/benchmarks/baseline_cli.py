"""Manage exact, immutable assessment baseline revisions from local automation."""

import argparse

from rove.benchmarks.baselines import BaselineInput, BaselineStore
from rove.ui.cli_support import add_common, emit, read_document, run_cli


def main(argv):
    parser = argparse.ArgumentParser(
        prog="rove baseline", description="Name and inspect frozen assessment baselines"
    )
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("list")
    commands.add_parser("save").add_argument(
        "input", help="BaselineInput JSON/YAML including operation_id"
    )
    for name in ("show", "revisions", "revise"):
        command = commands.add_parser(name)
        command.add_argument("baseline_id")
        if name == "revise":
            command.add_argument(
                "input", help="BaselineInput JSON/YAML including expected_head_revision_id"
            )
    for command in commands.choices.values():
        add_common(command)
        command.add_argument("--output")
    args = parser.parse_args(argv)

    def action():
        store = BaselineStore(args.store)
        if args.action in {"save", "revise"}:
            result = store.save(
                BaselineInput.model_validate(read_document(args.input)),
                args.baseline_id if args.action == "revise" else None,
            )
        elif args.action == "list":
            result = {"baselines": store.list()}
        elif args.action == "show":
            result = store.get(args.baseline_id)
        else:
            result = {"revisions": store.revisions(args.baseline_id)}
        emit(result, args.output)

    run_cli(parser, action)
