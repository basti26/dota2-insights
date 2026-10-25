"""Join a bounded offline replay export to verified, past-only minimap context."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dota2_insights.minimap.replay_alignment import align_context, calibrate_translation
from dota2_insights.minimap.settings import atomic_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-samples", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--minimap-run", type=Path, required=True)
    parser.add_argument("--hero", action="append", default=[], help="extra omniscient hero reference")
    parser.add_argument("--max-clock-delta-s", type=float, default=.6)
    parser.add_argument("--translation-calibration", type=Path,
                        help="frozen translation from a separate game-clock window")
    parser.add_argument("--visibility-transitions", type=Path,
                        help="optional every-packet raw visibility transitions from the same DEM")
    parser.add_argument("--map-calibration", type=Path,
                        help="optional source-bound stationary-tower calibration for native map pixel references")
    parser.add_argument("--calibrate-translation", action="store_true",
                        help="propose an origin translation from this calibration window only")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-sequence",type=int)
    parser.add_argument("--max-frames",type=int,help="explicit bounded slice, 1..2000; requires start-sequence")
    args = parser.parse_args(argv)
    if args.calibrate_translation and args.translation_calibration is not None:
        parser.error("cannot evaluate and propose a translation in the same operation")
    output, diagnostics = args.output.resolve(), (ROOT / "diagnostics").resolve()
    if (output == diagnostics or not output.is_relative_to(diagnostics) or output.exists()
            or output.is_relative_to(args.minimap_run.resolve())
            or output == args.context.resolve() or output == args.replay_samples.resolve()):
        parser.error("output must be a new independent file under project/diagnostics")
    try:
        result = align_context(replay_path=args.replay_samples, context_path=args.context,
            minimap_run=args.minimap_run, diagnostics_root=diagnostics,
            max_clock_delta_s=args.max_clock_delta_s, comparison_heroes=tuple(args.hero),
            translation_calibration=args.translation_calibration,
            visibility_transitions=args.visibility_transitions, map_calibration=args.map_calibration,
            start_sequence=args.start_sequence,max_frames=args.max_frames)
        if args.calibrate_translation:
            result = calibrate_translation(result)
        content = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(content) > 32 * 1024 ** 2:
            raise ValueError("alignment_output_size_limit")
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, result)
        print(json.dumps({"output": str(output),
            "counts": result.get("counts"), "own_comparison_summary": result.get("own_comparison_summary"),
            "translation_to_gsi_world": result.get("translation_to_gsi_world")}, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print("minimap_replay_alignment_failed: " + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
