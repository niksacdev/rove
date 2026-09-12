"""Explicit local export/import entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from rove.trials.exchange import export_bundle, import_bundle


def main(argv):
    parser = argparse.ArgumentParser(description="Versioned ROVE relational exchange")
    parser.add_argument("action", choices=["export", "import"])
    parser.add_argument("path", type=Path, help="ZIP output for export; ZIP input for import")
    parser.add_argument(
        "--store", type=Path, required=True, help="Source store or new import destination"
    )
    args = parser.parse_args(argv)
    try:
        manifest = (
            export_bundle(args.store, args.path)
            if args.action == "export"
            else import_bundle(args.path, args.store)
        )
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    print(
        f"{args.action}: {len(manifest['tables'])} relational tables, {len(manifest['assets'])} verified assets; format v{manifest['version']}"
    )
