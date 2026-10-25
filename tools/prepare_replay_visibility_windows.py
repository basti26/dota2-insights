"""Mine bounded, local replay raw team-bit windows for offline image review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dota2_insights.minimap.replay_visibility_windows import prepare_windows
from dota2_insights.minimap.settings import atomic_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-samples", type=Path, required=True)
    parser.add_argument("--player-team", type=int, choices=(2, 3), required=True)
    parser.add_argument("--max-sample-gap-s", type=float, default=2.5)
    parser.add_argument("--minimum-hidden-s", type=float, default=3)
    parser.add_argument("--assumed-walk-speed", type=float, default=800)
    parser.add_argument("--game-context", type=Path, help="closed main-session context.jsonl")
    parser.add_argument("--self-samples", type=Path, help="closed self-economy samples.jsonl")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output, diagnostics = args.output.resolve(), (ROOT / "diagnostics").resolve()
    if output == diagnostics or not output.is_relative_to(diagnostics) or output.exists():
        parser.error("output must be a new file under project/diagnostics")
    try:
        report = prepare_windows(replay_path=args.replay_samples, player_team=args.player_team,
            max_sample_gap_s=args.max_sample_gap_s, minimum_hidden_s=args.minimum_hidden_s,
            assumed_walk_speed=args.assumed_walk_speed, game_context=args.game_context,
            self_samples=args.self_samples)
        if len(json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 32 * 1024 ** 2:
            raise ValueError("visibility_report_output_limit")
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, report)
        print(json.dumps({"output": str(output), "counts": report["counts"]}, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print("replay_visibility_windows_failed: " + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
