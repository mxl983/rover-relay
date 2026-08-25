"""CLI for navigation rehearsal UI.

Examples:
  # Fetch latest artifacts from the nav container, then open the UI
  python3 -m tools.nav_rehearsal --from-docker

  # Use local files
  python3 -m tools.nav_rehearsal \\
    --run /tmp/navigation_run.jsonl \\
    --map /tmp/slam_map.json \\
    --docker-log /tmp/nav_docker.log

  # List recorded runs without opening UI
  python3 -m tools.nav_rehearsal --from-docker --list
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .app import run_app
from .parse import latest_nav_id, list_runs, load_jsonl

DEFAULT_CONTAINER = "relay-ros2-nav-1"
DEFAULT_CACHE = Path.home() / ".cache" / "rover-nav-rehearsal"


def _docker_cp(container: str, src: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["docker", "cp", f"{container}:{src}", str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
        return dest.is_file() and dest.stat().st_size > 0
    except (subprocess.CalledProcessError, FileNotFoundError) as err:
        print(f"warn: docker cp {src} failed: {err}", file=sys.stderr)
        return False


def _docker_logs(container: str, dest: Path, *, tail: int = 8000) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["docker", "logs", "--tail", str(tail), container],
            check=False,
            capture_output=True,
            text=True,
        )
        # docker logs put app output on stderr often
        text = (proc.stdout or "") + (proc.stderr or "")
        dest.write_text(text, encoding="utf-8")
        return bool(text.strip())
    except FileNotFoundError as err:
        print(f"warn: docker logs failed: {err}", file=sys.stderr)
        return False


def fetch_from_docker(container: str, cache: Path) -> tuple[Path, Path | None, Path | None, Path | None]:
    run_path = cache / "navigation_run.jsonl"
    map_path = cache / "slam_map.json"
    live_path = cache / "slam_live.json"
    log_path = cache / "nav_docker.log"
    ok_run = _docker_cp(container, "/app/lidar/navigation_run.jsonl", run_path)
    if not ok_run:
        raise SystemExit(
            f"Could not copy navigation_run.jsonl from {container}. "
            "Is the container running?"
        )
    ok_map = _docker_cp(container, "/app/lidar/slam_map.json", map_path)
    ok_live = _docker_cp(container, "/app/lidar/slam.json", live_path)
    ok_log = _docker_logs(container, log_path)
    return (
        run_path,
        map_path if ok_map else None,
        live_path if ok_live else None,
        log_path if ok_log else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a recorded nav run step-by-step.")
    parser.add_argument(
        "--from-docker",
        action="store_true",
        help=f"Copy latest run/map/logs from {DEFAULT_CONTAINER}",
    )
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--run", type=Path, help="Path to navigation_run.jsonl")
    parser.add_argument("--map", type=Path, help="Path to slam_map.json")
    parser.add_argument("--slam-live", type=Path, help="Path to slam.json (preferred map)")
    parser.add_argument("--docker-log", type=Path, help="Optional raw docker logs text file")
    parser.add_argument("--list", action="store_true", help="List runs and exit")
    parser.add_argument("--nav-id", help="Default run to open (else latest)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--headless", action="store_true", help="Do not open a browser")
    args = parser.parse_args(argv)

    run_path = args.run
    map_path = args.map
    live_path = args.slam_live
    log_path = args.docker_log

    if args.from_docker:
        run_path, map_path, live_path, log_path = fetch_from_docker(args.container, args.cache)
        print(f"cached under {args.cache}")

    if run_path is None:
        for candidate in (
            Path("/tmp/navigation_run.jsonl"),
            args.cache / "navigation_run.jsonl",
        ):
            if candidate.is_file():
                run_path = candidate
                break
    if run_path is None or not run_path.is_file():
        print(
            "Need a navigation_run.jsonl. Try:\n"
            "  python3 -m tools.nav_rehearsal --from-docker",
            file=sys.stderr,
        )
        return 2

    if map_path is None:
        for candidate in (Path("/tmp/slam_map.json"), args.cache / "slam_map.json"):
            if candidate.is_file():
                map_path = candidate
                break
    if live_path is None:
        for candidate in (Path("/tmp/slam.json"), args.cache / "slam_live.json"):
            if candidate.is_file():
                live_path = candidate
                break

    events = load_jsonl(run_path)
    runs = list_runs(events)
    if args.list:
        for r in runs:
            ph = "".join(str(p) for p in r.phases) or "?"
            print(
                f"{r.nav_id}\t{r.label}\tP{ph}\t{r.event_count}ev\t"
                f"{r.result or '-'}\t{r.start_iso} → {r.end_iso}"
            )
        print(f"latest: {latest_nav_id(events)}")
        return 0

    if not runs:
        print("No nav runs found in jsonl.", file=sys.stderr)
        return 1

    if args.nav_id:
        print(f"default hint nav_id={args.nav_id} (select in UI)")

    run_app(
        run_jsonl=run_path,
        slam_map=map_path,
        slam_live=live_path,
        docker_log=log_path,
        host=args.host,
        port=args.port,
        open_browser=not args.headless,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
