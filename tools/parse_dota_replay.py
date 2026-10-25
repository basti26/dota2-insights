"""Explicit, bounded offline replay export into an isolated diagnostics folder."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--deps", type=Path, default=PROJECT / ".replay-deps")
    cli.add_argument("--sample-hz", type=float, default=2.0)
    cli.add_argument("--start-game-time", type=float, default=0.0)
    cli.add_argument("--end-game-time", type=float, default=5400.0)
    cli.add_argument("--max-seconds", type=float, default=600.0, help="wall-clock resource limit")
    cli.add_argument("--max-output-mib", type=int, default=128)
    cli.add_argument("--cancel-file", type=Path)
    cli.add_argument("--include-modifiers", action="store_true", help="bounded per-hero ActiveModifiers snapshots; useful for short evidence windows")
    cli.add_argument("--visibility-transitions", action="store_true", help="record every completed-packet team-bit/life/identity state change without coordinates")
    args = cli.parse_args()
    # This isolated dependency tree must precede all global assistant packages.
    if not args.deps.is_dir():
        cli.error("isolated replay dependencies missing; see replay setup documentation")
    sys.path.insert(0, str(args.deps.resolve()))
    sys.path.insert(0, str(PROJECT))
    from dota2_insights.replay_archive import ArchiveOptions, parse_archive
    options = ArchiveOptions(args.sample_hz, args.start_game_time, args.end_game_time, args.max_seconds, args.max_output_mib, str(args.cancel_file.resolve()) if args.cancel_file else None, args.include_modifiers, args.visibility_transitions)
    try:
        result = parse_archive(args.source, args.output, options)
    except (OSError, ValueError, ImportError) as exc:
        cli.error(str(exc))
    print(json.dumps({key: result[key] for key in ("match_id", "stop_reason", "elapsed_seconds", "counts", "output_bytes", "parse_error")}, ensure_ascii=False))
    return 0 if result["export_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
