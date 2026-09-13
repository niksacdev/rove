"""Manage the running dashboard's recent list; durable trial records remain separate."""

import argparse
from urllib.parse import quote

import httpx

from rove.ui.assistant_cli import local_url
from rove.ui.cli_support import emit, run_cli


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "show", "remove"])
    parser.add_argument("identity", nargs="?", help="Recent evaluation ID (not a durable trial ID)")
    parser.add_argument("--url", default="http://127.0.0.1:5001")
    args = parser.parse_args(argv)
    if args.action != "list" and not args.identity:
        parser.error("An evaluation ID is required")

    def execute():
        url = local_url(args.url) + "/api/history"
        if args.action != "list":
            url += "/" + quote(args.identity, safe="")
        try:
            with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
                response = client.delete(url) if args.action == "remove" else client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as error:
            status = (
                error.response.status_code
                if isinstance(error, httpx.HTTPStatusError)
                else "unavailable"
            )
            raise ValueError(f"Local recent-history request failed ({status})") from None

    run_cli(parser, lambda: emit(execute()))
