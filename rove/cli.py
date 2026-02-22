"""Minimal CLI for ROVE demo."""

from __future__ import annotations

import sys


def main():
    args = sys.argv[1:]

    if not args or args[0] == "serve":
        port = 8000
        if "--port" in args:
            idx = args.index("--port")
            port = int(args[idx + 1])

        import uvicorn
        print(f"\n  ROVE — Robot Observation & Vision Evaluation")
        print(f"  Dashboard: http://localhost:{port}\n")
        uvicorn.run("rove.api.app:app", host="0.0.0.0", port=port, reload=True)
    elif args[0] == "--help":
        print("Usage: python -m rove [serve] [--port PORT]")
        print("  serve    Start the ROVE dashboard (default)")
        print("  --port   Port number (default: 8000)")
    else:
        print(f"Unknown command: {args[0]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
