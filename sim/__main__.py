"""CLI entrypoint for the internal SLAM/Nav simulator.

Usage:
  python3 -m sim              # open local GUI in browser
  python3 -m sim --test       # run deterministic regressions
  python3 -m sim --drift      # apartment_loop localization drift report
  python3 -m sim --map-quality  # map integrity + auto-map vs ground truth
  python3 -m sim --headless   # serve GUI without opening a browser
"""

from __future__ import annotations

import argparse
import json
import sys

from .engine import run_regressions
from .gui import run_gui
from .harness import run_drift_cli, run_map_quality_cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Internal hardware-free SLAM + Nav test bed (not part of the dashboard)."
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run deterministic regression checks and exit.",
    )
    parser.add_argument(
        "--drift",
        action="store_true",
        help="Run apartment_loop localization drift benchmark and exit.",
    )
    parser.add_argument(
        "--map-quality",
        action="store_true",
        help="Run apartment_loop map integrity / auto-map benchmark and exit.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="GUI bind host.")
    parser.add_argument("--port", type=int, default=8877, help="GUI bind port.")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Serve the GUI without opening a browser window.",
    )
    args = parser.parse_args(argv)

    if args.test:
        result = run_regressions()
        print(json.dumps(result, indent=2))
        return 0 if result["pass"] else 1

    if args.drift:
        return run_drift_cli()

    if args.map_quality:
        return run_map_quality_cli()

    run_gui(host=args.host, port=args.port, open_browser=not args.headless)
    return 0


if __name__ == "__main__":
    sys.exit(main())
