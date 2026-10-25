"""Export stationary building anchors without moving or uploading a replay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deps", type=Path, default=ROOT / ".replay-deps")
    parser.add_argument("--max-seconds", type=float, default=60)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if (output.exists() or output.suffix != ".json"
                or not output.is_relative_to((ROOT / "diagnostics").resolve())):
            raise ValueError("new_diagnostics_json_required")
        if not args.deps.is_dir():
            raise ValueError("isolated_replay_dependencies_missing")
        sys.path.insert(0, str(args.deps.resolve()))
        sys.path.insert(0, str(ROOT))
        from dota2_insights.replay_landmarks import extract_landmarks
        result = extract_landmarks(args.source, max_seconds=args.max_seconds)
        body = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(body + "\n")
        print(json.dumps({"match_id": result["match_id"], "landmarks": len(result["landmarks"]),
                          "elapsed_seconds": result["elapsed_seconds"]}))
    except (OSError, ValueError, ImportError) as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "reason": str(error)[:150]}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
