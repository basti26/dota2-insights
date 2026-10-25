"""Compare saved accepted HUD slots with a separate replay sample archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.replay_economy_review import compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="defaults to manifest.json next to samples")
    parser.add_argument("--lines", help="explicit original JSONL line numbers, comma separated")
    parser.add_argument("--tolerance", type=float, default=1.0)
    parser.add_argument("--max-observations", type=int, default=1000)
    parser.add_argument("--max-clock-age", type=float, default=1.0)
    parser.add_argument("--output", type=Path, required=True, help="new JSON under diagnostics")
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if output.exists() or not output.is_relative_to((ROOT / "diagnostics").resolve()) or output.suffix != ".json":
            raise ValueError("new_independent_diagnostics_json_required")
        lines = [int(value) for value in args.lines.split(",")] if args.lines else []
        if any(value <= 0 for value in lines):
            raise ValueError("positive_observation_lines_required")
        report = compare(args.observations, args.samples, lines=lines,
                         tolerance_s=args.tolerance, max_observations=args.max_observations,
                         manifest=args.manifest, max_clock_age_s=args.max_clock_age)
        body = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)
        if len(body.encode("utf-8")) > 16 * 1024 ** 2:
            raise ValueError("comparison_output_limit")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(body + "\n")
        print(json.dumps({"status": "compared", "observations": len(report["rows"]), "counts": report["counts"]}, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
