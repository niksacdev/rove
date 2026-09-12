"""Run the local ROVE dashboard from a source checkout."""

from __future__ import annotations

import argparse
import sys


def main():
    if sys.argv[1:2] == ["benchmark"]:
        from rove.benchmarks.cli import main as benchmark_main

        benchmark_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["exchange"]:
        from rove.trials.exchange_cli import main as exchange_main

        exchange_main(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description="ROVE local robotics evaluation workbench")
    parser.add_argument("command", nargs="?", choices=["serve"], default="serve")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    import uvicorn

    print(f"ROVE dashboard: http://127.0.0.1:{args.port}")
    uvicorn.run("rove.api.app:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
