"""Ten-minute, team-specific placement neighborhoods with explicit exposure."""
from collections import Counter, defaultdict
import hashlib
import json
import math

from .ward_reference_export import build
from .ward_proximity import declared_map_version

PRODUCER = ('parser_version', 'parser_commit', 'parser_sha256', 'ward_extractor_sha256', 'adapter_sha256')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def neighborhoods(rows, radius):
    """Direct radius neighborhoods, never connected-component chains."""
    grid = defaultdict(list)
    for r in rows:
        grid[math.floor(r['x']/radius), math.floor(r['y']/radius)].append(r)
    candidates = []
    for anchor in rows:
        gx, gy = math.floor(anchor['x']/radius), math.floor(anchor['y']/radius)
        members = [r for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   for r in grid[gx+dx, gy+dy]
                   if math.hypot(r['x']-anchor['x'], r['y']-anchor['y']) <= radius]
        if len(members) > 1:
            candidates.append((anchor, members))
    candidates.sort(key=lambda v: (-len({r['match_id'] for r in v[1]}), -len(v[1]), v[0]['id']))
    selected = []
    for anchor, members in candidates:
        if any(math.hypot(h['x']-anchor['x'], h['y']-anchor['y']) <= radius for h in selected):
            continue
        selected.append({'anchor_id': anchor['id'], 'x': anchor['x'], 'y': anchor['y'],
                         'placements': len(members), 'support_match_ids': sorted({r['match_id'] for r in members}),
                         'end_kinds': dict(Counter(r['end_kind'] or 'unknown' for r in members)),
                         'members': [r['id'] for r in members]})
    return selected


def analyze(sources, *, radius=256):
    if not finite(radius) or not 0 < radius <= 2048:
        raise ValueError('ward_phase_radius_invalid')
    matches, rows, cohorts = {}, [], {}
    unassigned_team = []
    unattributable = defaultdict(set)
    excluded = Counter()
    for manifest, raw in sources:
        packet = build(manifest, raw)
        mid = packet['match_id']
        if mid in matches:
            raise ValueError('ward_phase_duplicate_match')
        duration = manifest.get('match_end_game_time_s')
        if manifest.get('source_unchanged_stat') is not True or manifest.get('stop_reason') != 'replay_eof':
            raise ValueError('ward_phase_finished_source_required')
        if not finite(duration) or not 0 < duration <= 10800:
            raise ValueError('ward_phase_duration_required')
        producer = {k: manifest.get(k) for k in PRODUCER}
        if not all(isinstance(v, str) and v for v in producer.values()):
            raise ValueError('ward_phase_producer_required')
        # Unknown metadata stays explicit, never inferred from folder/date.
        meta = {k: manifest.get(k) for k in ('game_build', 'game_mode', 'map_version', 'semantic_patch')}
        if any(v is not None and type(v) not in (str, int) for v in meta.values()):
            raise ValueError('ward_phase_metadata_invalid')
        meta['map_version'] = declared_map_version(meta['map_version'])
        match_only = meta['map_version'] is None
        cohort_key = [producer, meta, mid] if match_only else [producer, meta]
        cohort = hashlib.sha256(json.dumps(cohort_key, sort_keys=True).encode()).hexdigest()[:16]
        cohorts[cohort] = {'id': cohort, 'producer': producer, 'metadata': meta,
                           'spatial_scope': 'single_match_unknown_map' if match_only else 'same_declared_map',
                           'map_compatibility_verified': False}
        matches[mid] = {'match_id': mid, 'duration_s': duration, 'cohort_id': cohort,
                        'wards_sha256': packet['source_wards_sha256'],
                        'replay_sha256': packet['source_replay_sha256']}
        excluded.update(u['reason'] for u in packet['unavailable'])
        wards = json.loads(raw)
        for point in packet['points']:
            ward = wards[int(point['id'].rsplit(':', 1)[1])]
            if ward.get('coordinate_basis') != 'gem_cell128_plus_vec_raw_coordinates':
                excluded['unsupported_coordinate_basis'] += 1
                continue
            t = point['placed_game_time_s']
            if not finite(t):
                excluded['invalid_placement_time'] += 1
                continue
            if t >= duration:
                excluded['at_or_after_match_end'] += 1
                continue
            phase = -1 if t < 0 else int(t//600)
            row = {**point, 'match_id': mid, 'cohort_id': cohort, 'phase': phase,
                   'end_kind': ward.get('end_kind')}
            if type(point['team']) is not int or point['team'] not in (2, 3):
                excluded['unknown_team'] += 1
                unattributable[cohort, phase, point['ward_type']].add(mid)
                unassigned_team.append({**row, 'team': None})
                continue
            rows.append(row)
        if len(matches) > 256 or len(rows) + len(unassigned_team) > 100000:
            raise ValueError('ward_phase_budget')
    if not matches:
        raise ValueError('ward_phase_sources_required')
    strata = []
    buckets = defaultdict(list)
    for r in rows:
        buckets[r['cohort_id'], r['phase'], r['team'], r['ward_type']].append(r)
    for cid in sorted(cohorts):
        mids = {mid for mid, m in matches.items() if m['cohort_id'] == cid}
        max_duration = max(matches[mid]['duration_s'] for mid in mids)
        phases = list(range(math.ceil(max_duration/600)))
        if (any(r['cohort_id'] == cid and r['phase'] == -1 for r in rows)
                or any((cid, -1, kind) in unattributable for kind in ('observer', 'sentry'))):
            phases.insert(0, -1)
        match_only = cohorts[cid]['spatial_scope'] == 'single_match_unknown_map'
        for phase in phases:
            start, end = phase*600, (phase+1)*600
            full = sorted(mid for mid in mids if matches[mid]['duration_s'] >= end) if phase >= 0 else []
            partial = sorted(mid for mid in mids if start < matches[mid]['duration_s'] < end) if phase >= 0 else sorted(mids)
            full_set = set(full)
            ended = sorted(mids-full_set-set(partial))
            for team in (2, 3):
                for kind in ('observer', 'sentry'):
                    observed = buckets[cid, phase, team, kind]
                    incomplete_team = full_set & unattributable.get((cid, phase, kind), set())
                    eligible = full_set - incomplete_team if not match_only else set()
                    complete_rows = [r for r in observed if r['match_id'] in full_set]
                    comparison_rows = [r for r in complete_rows if r['match_id'] in eligible]
                    hotspot_rows = complete_rows if match_only else comparison_rows
                    if phase < 0:
                        hotspot_rows = observed
                    hotspots = neighborhoods(hotspot_rows, radius)
                    for h in hotspots:
                        h['support_matches'] = len(h['support_match_ids'])
                        h['full_matches'] = len(eligible)
                        h['match_fraction'] = h['support_matches']/len(eligible) if eligible else None
                    strata.append({'cohort_id': cid, 'phase': phase, 'start_s': start, 'end_s': end,
                                   'exposure_basis': 'complete_ten_minute_interval' if phase >= 0 else 'pregame_coverage_not_verified',
                                   'team': team, 'ward_type': kind, 'full_match_ids': full,
                                   'frequency_match_ids': sorted(eligible),
                                   'unattributable_full_match_ids': sorted(incomplete_team),
                                   'partial_match_ids_reference_only': partial, 'ended_before_phase_ids': ended,
                                   'placements_full': len(complete_rows),
                                   'placements_partial_reference_only': len(observed)-len(complete_rows),
                                   'hotspots': hotspots})
    return {'schema_version': 2, 'kind': 'descriptive_ward_phase_profile', 'radius': radius,
            'cohorts': list(cohorts.values()), 'matches': list(matches.values()), 'rows': rows,
            'strata': strata, 'excluded': dict(excluded), 'unassigned_team': unassigned_team,
            'limits': ['Only completed ten-minute match intervals enter frequency denominators.',
                       'Partial interval placements are reference only, including observed successes.',
                       'Multiple placements from one match count once in match support.',
                       'Neighborhoods may overlap; totals must not be added.',
                       'Unknown map versions support single-match descriptions only.',
                       'Unassigned team placements exclude the affected match, interval and ward type from both team rates.',
                       'Observed popularity and parsed end kinds do not prove effectiveness.']}
