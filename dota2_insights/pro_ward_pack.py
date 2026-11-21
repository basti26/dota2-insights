"""Aggregate completed Tier-1 pro replay ward placements into a clustered Foresight packet."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .replay_holdout import refuse_locked_match

VALID_WARD_TYPES = ("observer", "sentry")
TEAM_KEYS = {2: "radiant", 3: "dire"}
PHASE_OPENING_END_S = 270.0   # < 04:30 (initial 2 obs + 3 sen + 02:15 3rd obs; expires ~05-06m)
PHASE_LANING_END_S = 720.0    # 04:30 .. 12:00 (1st replacement wave at 04:30 stock / 05:00 night)
PHASE_MID_END_S = 1440.0      # 12:00 .. 24:00 (mid map control); >= 24:00 -> late (obs lasts to 30m+)
DEFAULT_CLUSTER_RADIUS_WU = 130.0


def _validate_match_input(manifest: dict, wards_raw: bytes) -> tuple[str, list[dict]]:
    identifier = str(manifest.get("match_id", ""))
    if not identifier.isdigit():
        raise ValueError("pro_ward_invalid_match_id")
    refuse_locked_match(identifier)
    if (
        manifest.get("complete") is not True
        or manifest.get("parse_error") is not None
        or manifest.get("independent_footer_matches_parser") is not True
        or str(manifest.get("parsed_match_id")) != identifier
    ):
        raise ValueError(f"pro_ward_incomplete_match:{identifier}")
    spec = (manifest.get("files") or {}).get("wards.json") or {}
    digest = hashlib.sha256(wards_raw).hexdigest()
    if spec.get("sha256") != digest or spec.get("bytes") != len(wards_raw):
        raise ValueError(f"pro_ward_sha_mismatch:{identifier}")
    wards = json.loads(wards_raw)
    if not isinstance(wards, list) or len(wards) > 5000 or manifest.get("ward_count") != len(wards):
        raise ValueError(f"pro_ward_count_invalid:{identifier}")
    return identifier, wards


def _greedy_cluster(rows: list[dict], radius_wu: float) -> list[list[dict]]:
    """Density-ordered greedy clustering so high-frequency cliffs lock onto peak centers."""
    if not rows:
        return []
    n = len(rows)
    coords = [(r["x"], r["y"]) for r in rows]
    r2 = radius_wu * radius_wu
    # Compute local neighbor count for each point to seed densest spots first
    densities = []
    for i in range(n):
        xi, yi = coords[i]
        cnt = 0
        for j in range(n):
            dx, dy = coords[j][0] - xi, coords[j][1] - yi
            if dx * dx + dy * dy <= r2:
                cnt += 1
        densities.append((cnt, i))
    order = [i for _, i in sorted(densities, key=lambda pair: (-pair[0], pair[1]))]

    assigned = [False] * n
    clusters: list[list[dict]] = []
    for seed in order:
        if assigned[seed]:
            continue
        sx, sy = coords[seed]
        members_idx = [seed]
        assigned[seed] = True
        for j in order:
            if assigned[j]:
                continue
            dx, dy = coords[j][0] - sx, coords[j][1] - sy
            if dx * dx + dy * dy <= r2:
                members_idx.append(j)
                assigned[j] = True
        clusters.append([rows[k] for k in members_idx])
    return clusters


def build_aggregated_pack(
    match_records: list[tuple[dict, bytes]],
    *,
    league_id: int | None = None,
    league_name: str | None = None,
    game_build: int | None = None,
    cluster_radius_wu: float = DEFAULT_CLUSTER_RADIUS_WU,
    min_count: int = 1,
    max_points: int = 400,
) -> dict:
    """Build a schema-v1 `historical_ward_reference` packet with pro cluster frequency stats."""
    if not match_records:
        raise ValueError("pro_ward_no_matches")
    if cluster_radius_wu <= 0 or min_count < 1 or max_points < 1:
        raise ValueError("pro_ward_invalid_parameters")

    match_ids: list[str] = []
    seen_matches: set[str] = set()
    by_team_type: dict[tuple[str, str], list[dict]] = {}
    source_hashes: dict[str, dict[str, str]] = {}
    total_source_wards = 0
    unavailable_count = 0

    for manifest, wards_raw in match_records:
        mid, wards = _validate_match_input(manifest, wards_raw)
        if mid in seen_matches:
            continue
        seen_matches.add(mid)
        match_ids.append(mid)
        total_source_wards += len(wards)
        source_hashes[mid] = {
            "replay_sha256": str(manifest.get("source_sha256", "")),
            "wards_sha256": hashlib.sha256(wards_raw).hexdigest(),
        }
        for w in wards:
            if str(w.get("match_id")) != mid:
                raise ValueError(f"pro_ward_cross_match:{mid}")
            if w.get("after_match_end") is True:
                unavailable_count += 1
                continue
            wtype = w.get("ward_type")
            x, y = w.get("x"), w.get("y")
            if (
                wtype not in VALID_WARD_TYPES
                or type(x) not in (int, float)
                or type(y) not in (int, float)
                or not (math.isfinite(x) and math.isfinite(y))
            ):
                unavailable_count += 1
                continue
            team_key = TEAM_KEYS.get(w.get("team"), "unknown")
            by_team_type.setdefault((team_key, wtype), []).append({
                "match_id": mid,
                "x": float(x),
                "y": float(y),
                "ward_type": wtype,
                "team": w.get("team"),
                "team_key": team_key,
                "placed_game_time_s": w.get("placed_game_time_s"),
            })

    total_matches = len(match_ids)
    match_ids.sort(key=int)
    raw_points: list[dict] = []
    tag = str(league_id) if league_id is not None else "pro"

    for team_key in ("radiant", "dire", "unknown"):
        for wtype in VALID_WARD_TYPES:
            rows = by_team_type.get((team_key, wtype))
            if not rows:
                continue
            clusters = _greedy_cluster(rows, cluster_radius_wu)
            clusters.sort(key=lambda c: (-len(c), -len({m["match_id"] for m in c}), c[0]["x"], c[0]["y"]))
            for idx, members in enumerate(clusters):
                count = len(members)
                if count < min_count:
                    continue
                cx = sum(m["x"] for m in members) / count
                cy = sum(m["y"] for m in members) / count
                distinct_matches = sorted({m["match_id"] for m in members}, key=int)
                m_count = len(distinct_matches)
                times = sorted(
                    float(m["placed_game_time_s"])
                    for m in members
                    if type(m.get("placed_game_time_s")) in (int, float)
                    and math.isfinite(m["placed_game_time_s"])
                )
                opening = sum(1 for t in times if t < PHASE_OPENING_END_S)
                laning = sum(1 for t in times if PHASE_OPENING_END_S <= t < PHASE_LANING_END_S)
                mid_cnt = sum(1 for t in times if PHASE_LANING_END_S <= t < PHASE_MID_END_S)
                late = sum(1 for t in times if t >= PHASE_MID_END_S)
                phase_counts = {"opening": opening, "laning": laning, "mid": mid_cnt, "late": late}
                active_phases = [p for p in ("opening", "laning", "mid", "late") if phase_counts[p] > 0]
                median_time_s = round(times[len(times) // 2], 1) if times else None
                radiant = sum(1 for m in members if m.get("team") == 2)
                dire = sum(1 for m in members if m.get("team") == 3)
                point = {
                    "id": f"pro:{tag}:{team_key}:{wtype}:{idx}",
                    "x": round(cx, 2),
                    "y": round(cy, 2),
                    "ward_type": wtype,
                    "team": None if team_key == "unknown" else team_key,
                    "tree_clearance": "unknown",
                    "tree_evidence": None,
                    "count": count,
                    "match_count": m_count,
                    "frequency": round(m_count / total_matches, 4),
                    "early_count": opening + laning,
                    "mid_late_count": mid_cnt + late,
                    "phase_counts": phase_counts,
                    "phases": active_phases,
                    "median_time_s": median_time_s,
                    "team_counts": {"radiant": radiant, "dire": dire},
                    "source_matches": distinct_matches,
                }
                raw_points.append(point)

    # Sort by frequency & count, and allocate max_points fairly across teams so
    # alphabetical ID tie-breaking ('dire' < 'radiant') never starves one side.
    sort_key = lambda p: (-p["match_count"], -p["count"], p["id"])
    by_team_buckets: dict[str, list[dict]] = {}
    for pt in raw_points:
        by_team_buckets.setdefault(pt.get("team") or "unknown", []).append(pt)
    for bucket in by_team_buckets.values():
        bucket.sort(key=sort_key)
    teams_present = [t for t in ("radiant", "dire", "unknown") if by_team_buckets.get(t)]
    points: list[dict] = []
    if len(teams_present) <= 1:
        raw_points.sort(key=sort_key)
        points = raw_points[:max_points]
    else:
        quota = max(1, max_points // len(teams_present))
        leftovers: list[dict] = []
        for t in teams_present:
            b = by_team_buckets[t]
            points.extend(b[:quota])
            leftovers.extend(b[quota:])
        if len(points) < max_points and leftovers:
            leftovers.sort(key=sort_key)
            points.extend(leftovers[: max_points - len(points)])
        points.sort(key=sort_key)
        points = points[:max_points]

    packet: dict = {
        "schema_version": 1,
        "kind": "historical_ward_reference",
        "match_id": match_ids[-1],
        "aggregated_pro_pack": True,
        "league_id": league_id,
        "league_name": league_name,
        "aggregated_match_count": total_matches,
        "source_match_ids": match_ids,
        "source_match_hashes": source_hashes,
        "cluster_radius_world_units": float(cluster_radius_wu),
        "min_cluster_count": int(min_count),
        "coordinate_basis": "gem_cell128_plus_vec_raw_coordinates",
        "map_version": "unknown",
        "source_ward_count": total_source_wards,
        "unavailable_count": unavailable_count,
        "points": points,
        "unavailable": [],
        "tree_clearance_inferred": False,
        "preference_statistics": False,
        "world_projection_verified": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if type(game_build) is int and game_build > 0:
        packet["source_replay_game_build"] = game_build
    return packet


def fetch_league_completed_matches(bridge, *, league_id: int) -> tuple[list[tuple[dict, bytes]], str | None]:
    """Fetch all completed verified ward files for `league_id` from the Pi replay library."""
    catalog = bridge.catalog()
    selected: list[str] = []
    league_name: str | None = None
    for mid, record in sorted(catalog.get("matches", {}).items(), key=lambda kv: int(kv[0])):
        sampling = record.get("sampling") or {}
        if sampling.get("league_id") != league_id:
            continue
        if record.get("analysis_status") != "complete":
            continue
        refuse_locked_match(mid)
        selected.append(mid)
        if not league_name and sampling.get("league_name"):
            league_name = str(sampling["league_name"])
    if not selected:
        raise ValueError(f"no_completed_matches_for_league:{league_id}")

    script = (
        "import base64, hashlib, json\n"
        "from pathlib import Path\n"
        f"root = Path({bridge.config['remote_data']!r})\n"
        f"mids = {selected!r}\n"
        "cat = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))\n"
        "cfg = json.loads((root / 'config.json').read_text(encoding='utf-8'))\n"
        "ad = Path(cfg.get('analysis_directory', root / 'analysis'))\n"
        "out = []\n"
        "for mid in mids:\n"
        "    rec = cat['matches'][mid]\n"
        "    folder = ad / mid\n"
        "    mraw = (folder / 'manifest.json').read_bytes()\n"
        "    man = json.loads(mraw)\n"
        "    if rec.get('analysis_status') != 'complete' or not man.get('complete') or man.get('source_sha256') != rec.get('sha256'):\n"
        "        continue\n"
        "    wf = folder / 'wards.json'\n"
        "    if wf.stat().st_size > 2 * 1024**2:\n"
        "        continue\n"
        "    wraw = wf.read_bytes()\n"
        "    if hashlib.sha256(wraw).hexdigest() != man['files']['wards.json']['sha256']:\n"
        "        continue\n"
        "    out.append({'manifest': man, 'wards_base64': base64.b64encode(wraw).decode('ascii')})\n"
        "print(json.dumps({'items': out}))\n"
    )
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cmd_file = Path(tmp) / "fetch_league.py"
        cmd_file.write_text(script, encoding="utf-8")
        payload = bridge.execute("python3 -", source=cmd_file, timeout=60)
    records = [
        (item["manifest"], base64.b64decode(item["wards_base64"]))
        for item in payload.get("items", [])
    ]
    return records, league_name
