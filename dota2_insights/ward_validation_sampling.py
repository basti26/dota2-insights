"""Deterministic draft samples and blank observations, never eligibility approval."""
from collections import Counter, defaultdict
import hashlib
import json
import math


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def prepare_samples(records, replay_sha256, random_count=12, special_count=6, *, match_end_game_s=None):
    """Original list positions stay the source indices; caller must gate admission.

    This function is also safe for already-exposed development/synthetic fixtures.
    Its output is explicitly a draft, not a frozen independent validation set.
    """
    if (len(replay_sha256) != 64 or any(c not in '0123456789abcdef' for c in replay_sha256)
            or type(random_count) is not int or random_count < 1
            or type(special_count) is not int or special_count < 0):
        raise ValueError('invalid_sample_parameters')
    strata = defaultdict(list)
    tags = {}
    coordinates = Counter((r.get('x'), r.get('y')) for r in records
                          if finite(r.get('x')) and finite(r.get('y')))
    for index, row in enumerate(records):
        team = str(row.get('team')) if row.get('team') in (2, 3) else 'unknown'
        kind = row.get('ward_type') if row.get('ward_type') in ('observer', 'sentry') else 'unknown'
        placed = row.get('placed_game_time_s')
        phase = ('early' if placed < 600 else 'middle' if placed < 1200 else 'late') if finite(placed) else 'unknown'
        strata[(team, kind, phase)].append(index)
        labels = []
        if row.get('end_kind') in (None, 'unknown', 'unobserved'):
            labels.append('unknown_end')
        if row.get('after_match_end') is True:
            labels.append('postgame_placement')
        ended = row.get('ended_game_time_s')
        if (finite(match_end_game_s) and finite(placed) and finite(ended)
                and placed < match_end_game_s < ended):
            labels.append('end_crosses_match_end')
        if finite(row.get('duration_s')) and 0 <= row['duration_s'] <= 30:
            labels.append('short_lifetime')
        if row.get('map_edge_verified') is True:
            labels.append('map_edge')
        if finite(row.get('x')) and finite(row.get('y')) and coordinates[(row['x'], row['y'])] > 1:
            labels.append('repeated_coordinates')
        if row.get('end_kind') is not None:
            labels.append('end_kind:' + str(row['end_kind']))
        tags[index] = labels

    def rank(index):
        return hashlib.sha256(f'ward-validation-v1|{replay_sha256}|{index}'.encode()).hexdigest()

    phases = ('early', 'middle', 'late', 'unknown')
    keys = [(team, kind, phase) for team in ('2', '3', 'unknown')
            for kind in ('observer', 'sentry', 'unknown') for phase in phases]
    pools = {key: sorted(strata[key], key=rank) for key in keys}
    random_indices = []
    while len(random_indices) < random_count:
        added = False
        for key in keys:
            if pools[key]:
                random_indices.append(pools[key].pop(0))
                added = True
                if len(random_indices) == random_count:
                    break
        if not added:
            break
    selected = set(random_indices)
    special_indices = []
    categories = ['unknown_end', 'end_crosses_match_end', 'postgame_placement', 'short_lifetime', 'map_edge',
                  'repeated_coordinates'] + sorted({t for values in tags.values() for t in values if t.startswith('end_kind:')})
    for category in categories:
        candidates = [i for i in range(len(records)) if i not in selected and category in tags[i]]
        if candidates and len(special_indices) < special_count:
            index = candidates[0]
            special_indices.append(index)
            selected.add(index)
    # Fill remaining targeted capacity by original index, never by correctness.
    for index in range(len(records)):
        if len(special_indices) >= special_count:
            break
        if index not in selected and tags[index]:
            special_indices.append(index)
            selected.add(index)
    blind, answers = [], []
    for group, indices in (('random', random_indices), ('special', special_indices)):
        for index in indices:
            row = records[index]
            sample_id = f'{replay_sha256[:12]}-{index}'
            placed = row.get('placed_game_time_s')
            blind.append({'sample_id': sample_id, 'record_index': index, 'group': group,
                          'navigation_game_s': placed - 5 if finite(placed) else None,
                          'observed_type': None, 'observed_team': None,
                          'placed_game_s': None, 'ended_game_s': None, 'lifetime_s': None,
                          'end_reason': None, 'world_x': None, 'world_y': None,
                          'map_u': None, 'map_v': None, 'screenshot_paths': [],
                          'screenshot_sha256': [], 'reviewer': None, 'reviewed_at_utc': None,
                          'unjudgeable_reason': None, 'status': 'pending'})
            answers.append({'sample_id': sample_id, 'record_index': index, 'group': group,
                            'special_tags': tags[index] if group == 'special' else [], 'record': row.copy()})
    return {'status': 'draft_not_admitted', 'independent_validation_passed': False,
            'replay_sha256': replay_sha256,
            'match_end_game_s': match_end_game_s,
            'records_sha256': hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False,
                                                       allow_nan=False).encode()).hexdigest(),
            'random_requested': random_count, 'random_selected': len(random_indices),
            'special_requested': special_count, 'special_selected': len(special_indices),
            'strata': [{'team': key[0], 'type': key[1], 'phase': key[2], 'available': len(strata[key])}
                       for key in keys], 'blind': blind, 'answers': answers}
