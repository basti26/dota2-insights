"""Bounded-radius descriptive ward reuse; no inferred vision or placement advice."""
from collections import Counter, defaultdict
from itertools import combinations
import math
import re


RAW_COORDINATES = 'gem_cell128_plus_vec_raw_coordinates'


def declared_map_version(value):
    if type(value) is int and value > 0:
        return str(value)
    if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', value)
            and value.lower() not in ('unknown', 'none', 'null', 'n-a', 'na', '0', '-')):
        return value
    return None


def analyze(packets, radii=(128, 256, 512)):
    rows = []
    seen, sources, maps = set(), {}, {}
    buckets = defaultdict(list)
    duplicate_exports = 0
    for packet, wards in packets:
        mid = packet['match_id']
        fingerprints = (packet.get('source_replay_sha256'), packet.get('source_wards_sha256'))
        if mid in sources:
            if (all(isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest) for digest in fingerprints)
                    and sources[mid] == (packet, wards)):
                duplicate_exports += 1
                continue
            raise ValueError('duplicate_ward_identity_conflicting_or_unverified_export')
        sources[mid] = (packet, wards)
        basis = packet.get('coordinate_basis', RAW_COORDINATES)
        if basis != RAW_COORDINATES:
            raise ValueError('ward_proximity_coordinate_basis_unsupported')
        version = declared_map_version(packet.get('map_version'))
        maps[mid] = version
        scope = ('map', version) if version is not None else ('match', mid)
        for point in packet['points']:
            if point['id'] in seen:
                raise ValueError('duplicate_ward_identity')
            seen.add(point['id'])
            ward = wards[int(point['id'].rsplit(':', 1)[1])]
            row = {**point, 'match_id': mid, 'map_version': version, 'end_kind': ward.get('end_kind'),
                   'ended_s': ward.get('ended_game_time_s'), 'duration_s': ward.get('duration_s')}
            rows.append(row)
            buckets[(*scope, point['ward_type'])].append(row)
    scope_by_ward = {row['id']: scope for scope, group in buckets.items() for row in group}
    map_pairs = Counter()
    for a, b in combinations(maps.values(), 2):
        reason = ('unknown_version' if a is None or b is None else
                  'different_version' if a != b else 'same_declared_version')
        map_pairs[reason] += 1
    results = []
    for radius in radii:
        if type(radius) not in (int, float) or not math.isfinite(radius) or radius <= 0:
            raise ValueError('positive_finite_radius_required')
        pairs = []
        for a, b in (pair for group in buckets.values() for pair in combinations(group, 2)):
            distance = math.hypot(a['x']-b['x'], a['y']-b['y'])
            if distance > radius:
                continue
            same = a['match_id'] == b['match_id']
            known_team = all(type(r['team']) is int and r['team'] in (2, 3) for r in (a, b))
            relation = ('cross_match' if not same else 'unknown_team' if not known_team
                        else 'same_team' if a['team'] == b['team'] else 'opponents')
            first, last = sorted((a, b), key=lambda r: (r['placed_game_time_s'] is None,
                                                       r['placed_game_time_s'] or 0, r['id']))
            overlap = None
            if same and all(type(v) in (int, float) and math.isfinite(v) for v in
                            (a['placed_game_time_s'], b['placed_game_time_s'], a['ended_s'], b['ended_s'])):
                overlap = max(0, min(a['ended_s'], b['ended_s']) -
                              max(a['placed_game_time_s'], b['placed_game_time_s']))
            gap = None
            if same and all(type(v) in (int, float) and math.isfinite(v) for v in
                            (first['placed_game_time_s'], first['ended_s'], last['placed_game_time_s'])):
                gap = last['placed_game_time_s'] - first['ended_s']
            pairs.append({'a': first['id'], 'b': last['id'], 'ward_type': a['ward_type'],
                          'distance': distance, 'relation': relation,
                          'observed_overlap_s': overlap, 'later_minus_first_end_s': gap})
        # Each hotspot is a real placement and a direct radius neighborhood.
        # Greedy suppression only hides redundant anchors; it never grows a chain.
        hotspots = []
        candidates = []
        for anchor in rows:
            members = [r for r in buckets[scope_by_ward[anchor['id']]] if
                       math.hypot(r['x']-anchor['x'], r['y']-anchor['y']) <= radius]
            if len(members) >= 2:
                candidates.append((anchor, members))
        candidates.sort(key=lambda v: (-len({r['match_id'] for r in v[1]}), -len(v[1]), v[0]['id']))
        for anchor, members in candidates:
            if any(scope_by_ward[h['anchor_id']] == scope_by_ward[anchor['id']] and
                   math.hypot(h['x']-anchor['x'], h['y']-anchor['y']) <= radius for h in hotspots):
                continue
            ends = Counter(r['end_kind'] or 'unknown' for r in members)
            hotspots.append({'anchor_id': anchor['id'], 'x': anchor['x'], 'y': anchor['y'],
                             'ward_type': anchor['ward_type'], 'map_version': anchor['map_version'],
                             'placement_count': len(members),
                             'match_count': len({r['match_id'] for r in members}),
                             'team_counts': dict(Counter(str(r['team']) for r in members)),
                             'end_kinds': dict(ends), 'members': [r['id'] for r in members]})
        counts = Counter(p['relation'] for p in pairs)
        results.append({'radius': radius, 'pair_counts': dict(counts),
                        'cross_match_pair_count': counts['cross_match'] if map_pairs['same_declared_version'] else None,
                        'same_team_positive_overlap_pairs': sum(p['relation'] == 'same_team' and
                            p['observed_overlap_s'] is not None and p['observed_overlap_s'] > 0 for p in pairs),
                        'pairs': pairs, 'hotspots': hotspots})
    return {'schema_version': 2, 'match_ids': sorted(maps), 'match_map_versions': maps,
            'cross_match_inputs': {key: map_pairs[key] for key in ('same_declared_version', 'different_version', 'unknown_version')},
            'duplicate_exports_omitted': duplicate_exports,
            'ward_count': len(rows), 'rows': rows, 'radii': results,
            'coordinate_basis': RAW_COORDINATES,
            'map_compatibility_verified': False, 'independent_game_verification': False,
            'limits': ['Distances are raw world units, not visibility overlap.',
                       'Unknown or different versions are isolated by match or declared version.',
                       'Matching declared versions permit a descriptive comparison only.',
                       'Neighborhoods may overlap; counts must not be summed.',
                       'End kinds are parser observations, not independently confirmed dewards.',
                       'Frequency does not establish effectiveness or professional preference.']}
