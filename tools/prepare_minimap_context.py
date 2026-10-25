"""Export closed main journals as separate past-only minimap side evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dota2_insights.minimap.context_archive import build_archive
from dota2_insights.minimap.settings import atomic_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimap-run", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True, help="closed launcher recorders.json")
    parser.add_argument("--self-run", type=Path)
    parser.add_argument("--draft-run", action="append", type=Path, default=[])
    parser.add_argument("--session-run", action="append", type=Path, default=[])
    parser.add_argument("--start-sequence", type=int, default=1)
    parser.add_argument("--stop-sequence", type=int)
    parser.add_argument("--max-frames", type=int, default=600,
                        help="default 600; explicit full-run export is bounded to 30000")
    parser.add_argument("--own-fresh-seconds", type=float, default=3.0)
    parser.add_argument("--output", type=Path, required=True, help="new independent diagnostics folder")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    diagnostics = (ROOT / "diagnostics").resolve()
    if (not output.is_relative_to(diagnostics) or output == diagnostics or output.exists()
            or output.is_relative_to(args.minimap_run.resolve())
            or any(output.is_relative_to(path.resolve()) for path in [*args.draft_run, *args.session_run]
                   + ([args.self_run] if args.self_run else []))):
        parser.error("output must be a new independent folder under project/diagnostics")
    try:
        report = build_archive(minimap_run=args.minimap_run, registry_path=args.registry,
            self_run=args.self_run, draft_runs=args.draft_run, session_runs=args.session_run,
            start_sequence=args.start_sequence, stop_sequence=args.stop_sequence,
            max_frames=args.max_frames, own_fresh_s=args.own_fresh_seconds)
        content = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        if len(content) > 128 * 1024 ** 2:
            raise ValueError("derived_context_archive_size_limit")
        output.mkdir(parents=True, exist_ok=False)
        atomic_json(output / "context.json", report)
        summary = {key: value for key, value in report.items() if key != "frames"}
        atomic_json(output / "summary.json", summary)
        print(json.dumps({"output": str(output), "selection": report["selection"],
                          "counts": report["counts"]}, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Input text, arbitrary paths and malformed JSON values are not copied
        # into a console/log when a source is unavailable or invalid.
        print("minimap_context_export_failed: " + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
