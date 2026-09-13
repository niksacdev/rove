"""Local case curation commands using the same transactional services as the UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rove.datasets.models import ReviewInput
from rove.datasets.service import MAX_IMAGE_BYTES, ConflictError, DatasetService
from rove.trials.evidence import verified_asset
from rove.trials.snapshots import content_hash
from rove.trials.store import MAX_ASSET_BYTES
from rove.ui.cli_support import add_common, emit, read_document, run_cli


def _object(path):
    value = read_document(path)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON or YAML object")
    return value


def _bytes(path, limit):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError(f"Asset must contain between 1 and {limit} bytes")
    return data


def _image(service, path):
    data = _bytes(path, MAX_IMAGE_BYTES)
    media = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
    asset = service.trials.save_asset(data, media)
    service._image(asset["sha256"])
    return asset["sha256"]


def _case(service, identity):
    try:
        return service.get_case_revision(identity)
    except KeyError:
        return service.get_case(identity)


def _import_cases(service, path, operation):
    if str(path) == "-":
        raise ValueError("Case manifests require a file so image paths have a stable base")
    if Path(path).suffix.lower() == ".jsonl":
        try:
            content = _bytes(path, 16 * 1024 * 1024).decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Case JSONL must use UTF-8 text") from None
        records = []
        for number, line in enumerate(content.splitlines(), 1):
            if not line.strip():
                continue
            if len(records) == 1000:
                raise ValueError("Case manifest must contain at most 1000 records")
            try:
                records.append(json.loads(line))
            except (ValueError, RecursionError):
                raise ValueError(f"Invalid JSONL record on line {number}") from None
    else:
        records = read_document(path)
    if not isinstance(records, list) or not 1 <= len(records) <= 1000:
        raise ValueError("Case manifest must be a list of 1-1000 records with relative image paths")
    base = Path(path).resolve().parent
    prepared = []
    # Validate all records before inserting cases; image bytes remain addressed assets.
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("image"), str):
            raise ValueError("Each case needs an image path and case metadata")
        image = (base / record["image"]).resolve()
        if not image.is_relative_to(base):
            raise ValueError("Manifest images must remain inside the manifest directory")
        payload = {key: value for key, value in record.items() if key != "image"}
        service.validate_case_payload(payload)
        prepared.append((payload, _image(service, image)))
    cases = [
        service.import_case(
            payload,
            digest,
            operation_id=content_hash(["case-import", operation, index]) if operation else None,
        )
        for index, (payload, digest) in enumerate(prepared)
    ]
    return {"cases": cases, "count": len(cases)}


def _review_revision(service, identity, patch, expected):
    previous = service.get_review(identity)
    if previous["revision"] != expected:
        raise ConflictError("Review changed; reload before revising")
    if previous["status"] != "final":
        return service.update_review(identity, patch, expected_revision=expected)
    payload = {key: previous[key] for key in ReviewInput.model_fields}
    return service.create_review({**payload, **patch, "supersedes_id": identity})


def _execute(args):
    service = DatasetService(args.store)
    action = args.action
    if args.resource == "case":
        if action == "create":
            return service.import_case(
                _object(args.file), _image(service, args.image), operation_id=args.operation_id
            )
        if action == "revise":
            return service.revise_case(
                args.id,
                _object(args.file),
                expected_head_revision_id=args.expected_head,
                image_sha256=_image(service, args.image) if args.image else None,
                operation_id=args.operation_id,
            )
        if action == "import":
            return _import_cases(service, args.file, args.operation_id)
        if action == "import-samples":
            from rove.datasets.library import sync_library

            if not (args.directory / "manifest.json").is_file():
                raise ValueError("Sample directory requires manifest.json")
            return sync_library(args.store, args.directory)
        if action == "list":
            return service.search_cases(
                args.limit, args.offset, q=args.query, category=args.category
            )
        return _case(service, args.id)
    if args.resource == "contract":
        if action == "create":
            return service.create_contract(_object(args.file))
        if action == "list":
            return {"contracts": service.list_contracts(args.limit, args.offset)}
        return service.get_contract(args.id)
    if args.resource == "review":
        if action == "record":
            return service.create_review(_object(args.file))
        if action == "revise":
            return _review_revision(service, args.id, _object(args.file), args.expected_revision)
        if action == "list":
            return {"reviews": service.list_reviews(args.case, args.trial, args.limit, args.offset)}
        return service.get_review(args.id)
    if args.resource == "dataset":
        if action == "preview":
            return service.preview_freeze(_object(args.file))
        if action == "freeze":
            return service.freeze(
                _object(args.file),
                expected_preview_hash=args.preview_hash,
                operation_id=args.operation_id,
            )
        if action == "list":
            return {"datasets": service.list_datasets(args.limit, args.offset)}
        return service.get_dataset(args.id)
    if action == "create":
        data = _bytes(args.file, MAX_ASSET_BYTES)
        return service.trials.save_asset(data, args.media_type)
    if action == "export":
        if (
            args.output
            and str(args.output) != "-"
            and args.output.resolve() == args.destination.resolve()
        ):
            raise ValueError("Asset bytes and JSON metadata require different output paths")
        metadata, data = verified_asset(service.trials, args.id)
        # Export is a new file, never an overwrite of a managed asset or another user's file.
        with args.destination.open("xb") as stream:
            stream.write(data)
        return metadata
    return service.trials.asset_metadata(args.id)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="rove data",
        description="Curate cases, scoring rules, reviews and frozen datasets locally",
    )
    resources = parser.add_subparsers(dest="resource", required=True)

    def resource(name, help_text):
        return resources.add_parser(name, help=help_text).add_subparsers(
            dest="action", required=True
        )

    def command(group, name, help_text, *, file=False, identity=False, page=False):
        leaf = group.add_parser(name, help=help_text)
        add_common(leaf)
        leaf.add_argument("--output", type=Path, help="Write JSON to a file instead of stdout")
        if file:
            leaf.add_argument(
                "--file", type=Path, required=True, help="JSON/YAML input; '-' for stdin"
            )
        if identity:
            leaf.add_argument("id", help="Exact record or revision ID")
        if page:
            leaf.add_argument("--limit", type=int, default=50)
            leaf.add_argument("--offset", type=int, default=0)
        return leaf

    cases = resource("case", "Versioned observation/task cases")
    create = command(cases, "create", "Create a case from metadata and an image", file=True)
    create.add_argument("--image", type=Path, required=True)
    create.add_argument("--operation-id", help="Reuse the same ID when retrying this creation")
    revise = command(
        cases,
        "revise",
        "Create a case revision with optimistic concurrency",
        file=True,
        identity=True,
    )
    revise.add_argument("--image", type=Path)
    revise.add_argument("--expected-head", required=True, help="Current head revision ID")
    revise.add_argument("--operation-id")
    batch = command(
        cases,
        "import",
        "Import JSON/YAML lists or JSONL cases with relative image paths",
        file=True,
    )
    batch.add_argument("--operation-id", help="Stable batch identity for resumable retries")
    samples = command(cases, "import-samples", "Synchronize a bundled-format sample library")
    samples.add_argument("directory", type=Path, help="Directory containing manifest.json")
    listing = command(cases, "list", "Search current case revisions", page=True)
    listing.add_argument("--query", default="")
    listing.add_argument("--category", default="")
    command(cases, "show", "Show exact revision or current case head", identity=True)

    contracts = resource("contract", "Immutable success criteria and scoring definitions")
    command(contracts, "create", "Validate and save a success contract", file=True)
    command(contracts, "list", "List saved success contracts", page=True)
    command(contracts, "show", "Show a success contract", identity=True)

    reviews = resource("review", "Explicit expert judgments and annotations")
    command(reviews, "record", "Record a case or trial review", file=True)
    revision = command(
        reviews, "revise", "Edit a draft or supersede a final review", file=True, identity=True
    )
    revision.add_argument("--expected-revision", type=int, required=True)
    listing = command(reviews, "list", "List reviews with optional case/trial filters", page=True)
    listing.add_argument("--case", help="Case revision ID")
    listing.add_argument("--trial", help="Trial ID")
    command(reviews, "show", "Show a review and its revision", identity=True)

    datasets = resource("dataset", "Frozen case and review memberships")
    command(datasets, "preview", "Inspect exact membership and its freeze hash", file=True)
    freeze = command(datasets, "freeze", "Freeze a previously previewed membership", file=True)
    freeze.add_argument(
        "--preview-hash", required=True, help="Exact hash returned by dataset preview"
    )
    freeze.add_argument("--operation-id")
    command(datasets, "list", "List frozen dataset revisions", page=True)
    command(datasets, "show", "Show a frozen dataset and retained memberships", identity=True)
    command(
        datasets,
        "export",
        "Export dataset metadata; assets remain references (use rove exchange for a full bundle)",
        identity=True,
    )

    assets = resource("asset", "Managed images and episode evidence")
    asset = command(
        assets, "create", "Store bounded evidence bytes separately from metadata", file=True
    )
    asset.add_argument(
        "--media-type",
        required=True,
        choices=[
            "application/json",
            "application/octet-stream",
            "video/mp4",
            "image/png",
            "image/jpeg",
        ],
    )
    command(assets, "show", "Show managed asset metadata", identity=True)
    export = command(assets, "export", "Copy verified bytes to a new file", identity=True)
    export.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    return run_cli(parser, lambda: emit(_execute(args), args.output))


if __name__ == "__main__":
    main()
