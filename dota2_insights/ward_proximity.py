"""Bounded-radius descriptive ward reuse; no inferred vision or placement advice."""
from collections import Counter
from itertools import combinations
import math


def analyze(packets, radii=(128, 256, 512)):
    rows = []
    seen = set()
    for packet, wards in packets:
        for point in packet['points']:
            if point['id'] in seen:
                raise ValueError('duplicate_ward_identity')
            seen.add(point['id'])
            ward = wards[int(point['id'].rsplit(':', 1)[1])]
            rows.append({**point, 'match_id': packet['match_id'],
                         'end_kind': ward.get('end_kind'),
                         'ended_s': ward.get('ended_game_time_s'),
                         'duration_s': ward.get('duration_s')})
    results = []
    for radius in radii:
        if type(radius) not in (int, float) or not math.isfinite(radius) or radius <= 0:
            raise ValueError('positive_finite_radius_required')
        pairs = []
        for a, b in combinations(rows, 2):
            if a['ward_type'] != b['ward_type']:
                continue
            distance = math.hypot(a['x']-b['x'], a['y']-b['y'])
            if distance > radius:
                continue
            same = a['match_id'] == b['match_id']
            known_team = a['team'] in (2, 3) and b['team'] in (2, 3)
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
            if same and first['ended_s'] is not None and last['placed_game_time_s'] is not None:
                gap = last['placed_game_time_s'] - first['ended_s']
            pairs.append({'a': first['id'], 'b': last['id'], 'ward_type': a['ward_type'],
                          'distance': distance, 'relation': relation,
                          'observed_overlap_s': overlap, 'later_minus_first_end_s': gap})
        # Each hotspot is a real placement and a direct radius neighborhood.
        # Greedy suppression only hides redundant anchors; it never grows a chain.
        hotspots = []
        candidates = []
        for anchor in rows:
            members = [r for r in rows if r['ward_type'] == anchor['ward_type'] and
                       math.hypot(r['x']-anchor['x'], r['y']-anchor['y']) <= radius]
            if len(members) >= 2:
                candidates.append((anchor, members))
        candidates.sort(key=lambda v: (-len({r['match_id'] for r in v[1]}), -len(v[1]), v[0]['id']))
        for anchor, members in candidates:
            if any(h['ward_type'] == anchor['ward_type'] and
                   math.hypot(h['x']-anchor['x'], h['y']-anchor['y']) <= radius for h in hotspots):
                continue
            ends = Counter(r['end_kind'] or 'unknown' for r in members)
            hotspots.append({'anchor_id': anchor['id'], 'x': anchor['x'], 'y': anchor['y'],
                             'ward_type': anchor['ward_type'], 'placement_count': len(members),
                             'match_count': len({r['match_id'] for r in members}),
                             'team_counts': dict(Counter(str(r['team']) for r in members)),
                             'end_kinds': dict(ends), 'members': [r['id'] for r in members]})
        results.append({'radius': radius, 'pair_counts': dict(Counter(p['relation'] for p in pairs)),
                        'same_team_positive_overlap_pairs': sum(p['relation'] == 'same_team' and
                            p['observed_overlap_s'] is not None and p['observed_overlap_s'] > 0 for p in pairs),
                        'pairs': pairs, 'hotspots': hotspots})
    return {'schema_version': 1, 'match_ids': sorted({r['match_id'] for r in rows}),
            'ward_count': len(rows), 'rows': rows, 'radii': results,
            'coordinate_basis': 'gem_cell128_plus_vec_raw_coordinates',
            'map_compatibility_verified': False, 'independent_game_verification': False,
            'limits': ['Distances are raw world units, not visibility overlap.',
                       'Cross-match neighborhoods require independent map-version confirmation.',
                       'Neighborhoods may overlap; counts must not be summed.',
                       'End kinds are parser observations, not independently confirmed dewards.',
                       'Frequency does not establish effectiveness or professional preference.']}
