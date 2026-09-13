"""Use the running local assistant while retaining its host-confirmation boundary."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from rove.api.assistant import AskRequest, ConfirmRequest
from rove.ui.cli_support import emit, read_document, run_cli


def local_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Use a loopback server URL without credentials, path or query")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as error:
        raise ValueError("Use a literal loopback address, such as http://127.0.0.1:5001") from error
    if not address.is_loopback or (port is not None and port == 0):
        raise ValueError("The assistant CLI connects only to a local loopback server")
    return value.rstrip("/")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "ask", "confirm"])
    parser.add_argument("target", nargs="?", help="AskRequest JSON/YAML or saved proposal file")
    parser.add_argument("--url", default="http://127.0.0.1:5001")
    parser.add_argument(
        "--proposal-output",
        type=Path,
        help="Create a private local file containing expiring confirmation proposals",
    )
    parser.add_argument(
        "--operation-id", help="Required when selecting among multiple saved proposals"
    )
    parser.add_argument(
        "--confirmed", action="store_true", help="Explicitly execute the selected reviewed proposal"
    )
    args = parser.parse_args(argv)
    if args.action != "status" and not args.target:
        parser.error("target is required")
    if args.action == "confirm" and not args.confirmed:
        parser.error("Review the saved proposal, then supply --confirmed to execute it")
    if args.proposal_output and args.action != "ask":
        parser.error("--proposal-output is only supported for ask")

    def execute():
        url = local_url(args.url)
        if args.proposal_output and args.proposal_output.exists():
            raise ValueError("Proposal output already exists; choose a new file")
        payload = None
        if args.action == "ask":
            payload = AskRequest.model_validate(read_document(args.target)).model_dump()
        elif args.action == "confirm":
            saved = read_document(args.target)
            if not isinstance(saved, dict):
                raise ValueError("Expected a saved proposal object")
            if saved.get("server_url") != url:
                raise ValueError("This proposal belongs to a different local server")
            proposals = saved.get("proposals", [])
            if not isinstance(proposals, list) or any(
                not isinstance(item, dict) for item in proposals
            ):
                raise ValueError("Expected a list of saved proposals")
            selected = [
                item
                for item in proposals
                if not args.operation_id or item["operation_id"] == args.operation_id
            ]
            if len(selected) != 1:
                raise ValueError("Select exactly one saved proposal with --operation-id")
            item = selected[0]
            payload = ConfirmRequest.model_validate(
                {
                    "operation_id": item["operation_id"],
                    "confirmation_token": item["confirmation_token"],
                    "confirmed": True,
                }
            ).model_dump()
        try:
            with httpx.Client(timeout=130, follow_redirects=False, trust_env=False) as client:
                endpoint = url + "/api/assistant/" + args.action
                response = (
                    client.get(endpoint) if payload is None else client.post(endpoint, json=payload)
                )
                response.raise_for_status()
                result = response.json()
        except httpx.HTTPError as error:
            # Request bodies can contain confirmation tokens; never echo them on failure.
            status = (
                error.response.status_code
                if isinstance(error, httpx.HTTPStatusError)
                else "unavailable"
            )
            raise ValueError(
                f"Local assistant request failed ({status}); check the server and proposal expiry"
            ) from None
        if not isinstance(result, dict):
            raise ValueError("The local assistant returned an invalid response")
        if args.proposal_output:
            # Explicit output uses exclusive creation and owner-only permissions. Tokens
            # remain local and expire with the server's proposal; stdout is sanitized.
            fd = os.open(args.proposal_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(
                    {"server_url": url, "proposals": result.get("proposals", [])}, stream, indent=2
                )
            result["proposal_file_saved"] = True
        return result

    run_cli(parser, lambda: emit(execute()))
