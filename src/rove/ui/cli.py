"""Run the local ROVE dashboard from a source checkout."""
from __future__ import annotations

import argparse


def main():
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
