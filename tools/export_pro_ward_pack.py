"""Export a clustered Tier-1 pro ward reference pack from completed Pi analyses."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dota2_insights.pi_replay_library import Bridge
from dota2_insights.pro_ward_pack import (
    DEFAULT_CLUSTER_RADIUS_WU,
    build_aggregated_pack,
    fetch_league_completed_matches,
)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--league-id", type=int, required=True, help="Verified Tier-1 OpenDota league_id (e.g. 20208)")
    cli.add_argument("--game-build", type=int, default=None, help="Client build number (e.g. 6986)")
    cli.add_argument("--radius", type=float, default=DEFAULT_CLUSTER_RADIUS_WU, help="Cluster radius in world units")
    cli.add_argument("--min-count", type=int, default=1, help="Minimum placements per cluster")
    cli.add_argument("--max-points", type=int, default=400, help="Maximum clustered spots to emit")
    cli.add_argument("--output", type=Path, required=True, help="Output JSON path")
    args = cli.parse_args()

    bridge = Bridge()
    records, league_name = fetch_league_completed_matches(bridge, league_id=args.league_id)
    packet = build_aggregated_pack(
        records,
        league_id=args.league_id,
        league_name=league_name,
        game_build=args.game_build,
        cluster_radius_wu=args.radius,
        min_count=args.min_count,
        max_points=args.max_points,
    )
    raw = (json.dumps(packet, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(args.output)
    digest = hashlib.sha256(raw).hexdigest()
    print(json.dumps({
        "output": str(args.output),
        "league_id": args.league_id,
        "league_name": league_name,
        "matches_aggregated": packet["aggregated_match_count"],
        "source_ward_count": packet["source_ward_count"],
        "clustered_points": len(packet["points"]),
        "observer_clusters": sum(1 for p in packet["points"] if p["ward_type"] == "observer"),
        "sentry_clusters": sum(1 for p in packet["points"] if p["ward_type"] == "sentry"),
        "top_hotspots": [
            {"type": p["ward_type"], "count": p["count"], "matches": p["match_count"], "freq": p["frequency"]}
            for p in packet["points"][:5]
        ],
        "packet_sha256": digest,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
