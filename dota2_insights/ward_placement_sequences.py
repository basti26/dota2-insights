"""Exact-clock placement batches; adjacent batches are not ownership transfers."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sqlite3

from .postgame_store import bounded, sha
from .ward_patterns import PHASE
from .ward_interval_phases import WIDTHS
from .ward_team_phase_states import SCHEMA as TEAM_PHASE_SCHEMA

SCHEMA = 'ward_cell_placement_batches_v1'


def analyze(database, team_phase_path, output):
    database, team_phase_path, output = (Path(p).resolve() for p in (database, team_phase_path, output))
    if any((output / name).exists() for name in ('placement-sequences.json', 'sequence-protocol.json')):
        raise ValueError('placement_sequences_new_files_required')
    digest = sha(bounded(database, 128 * 1024**2))
    raw = bounded(team_phase_path, 128 * 1024**2)
    source = json.loads(raw)
    if source.get('schema_version') != TEAM_PHASE_SCHEMA or source.get('database_sha256') != digest:
        raise ValueError('placement_sequences_source_binding')
    c = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        duration = dict(c.execute('SELECT match_id,duration_s FROM matches'))
        rows = [dict(r) for r in c.execute("""SELECT match_id,ward_index,team,ward_type,x,y,placed_s
            FROM wards WHERE placement_status='in_game'
            AND coordinate_basis='gem_cell128_plus_vec_raw_coordinates' ORDER BY match_id,ward_index""")]
    finally:
        c.close()
    if rows != source['placements'] or len(rows) > 100_000:
        raise ValueError('placement_sequences_original_rows_or_budget')
    cohorts = {mid: g['cohort_id'] for g in source['cohorts'] for mid in g['match_ids']}
    if set(cohorts) != set(duration):
        raise ValueError('placement_sequences_match_coverage')
    complete = {(p['cohort_id'], p['phase']): set(p['complete_match_ids']) for p in source['populations']}
    lookup = {(r['match_id'], r['ward_index']): r for r in rows}
    buckets = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row['team'] not in (2, 3) or not 0 <= row['placed_s'] < duration[row['match_id']]:
            raise ValueError('placement_sequences_team_or_time')
        for width in WIDTHS:
            for offset in (0, width // 2):
                key = (row['match_id'], row['ward_type'], width, offset,
                       math.floor((row['x'] - offset) / width), math.floor((row['y'] - offset) / width))
                buckets[key][row['placed_s']].append(row['ward_index'])
    if len(buckets) > 250_000:
        raise ValueError('placement_sequences_timeline_budget')
    timelines, stats = [], Counter()
    for (mid, kind, width, offset, gx, gy), times in sorted(buckets.items()):
        batches = []
        for timestamp, refs in sorted(times.items()):
            phase = int(timestamp // PHASE)
            if (cohorts[mid], phase) not in complete:
                raise ValueError('placement_sequences_missing_phase')
            if (mid in complete[cohorts[mid], phase]) != (duration[mid] >= (phase + 1) * PHASE):
                raise ValueError('placement_sequences_phase_coverage_changed')
            batch = {'placed_s': timestamp, 'phase': phase,
                     'phase_complete': mid in complete[cohorts[mid], phase],
                     'radiant_refs': sorted(i for i in refs if lookup[mid, i]['team'] == 2),
                     'dire_refs': sorted(i for i in refs if lookup[mid, i]['team'] == 3)}
            batches.append(batch)
            stats['batches'] += 1
            stats['same_clock_multi_ward_batches'] += int(len(refs) > 1)
            stats['same_clock_mixed_team_batches'] += int(bool(batch['radiant_refs']) and bool(batch['dire_refs']))
            stats['partial_phase_batches_reference_only'] += int(not batch['phase_complete'])
        edges = []
        for i, (a, b) in enumerate(zip(batches, batches[1:])):
            left = {team for team, field in ((2, 'radiant_refs'), (3, 'dire_refs')) if a[field]}
            right = {team for team, field in ((2, 'radiant_refs'), (3, 'dire_refs')) if b[field]}
            relation = ('mixed_team_batch_order_ambiguous' if len(left) > 1 or len(right) > 1
                        else 'same_team_next_batch' if left == right else 'opposing_team_next_batch')
            distances = [math.dist((lookup[mid, x]['x'], lookup[mid, x]['y']),
                                   (lookup[mid, y]['x'], lookup[mid, y]['y']))
                         for x in a['radiant_refs'] + a['dire_refs'] for y in b['radiant_refs'] + b['dire_refs']]
            if b['placed_s'] <= a['placed_s']:
                raise ValueError('placement_sequences_strict_batch_order')
            edges.append({'before_batch': i, 'after_batch': i + 1, 'delta_s': b['placed_s'] - a['placed_s'],
                          'relation': relation, 'minimum_position_distance': min(distances),
                          'maximum_position_distance': max(distances)})
            stats['adjacent_batch_edges'] += 1
            stats[relation] += 1
            stats['cross_phase_edges'] += int(a['phase'] != b['phase'])
            stats['complete_to_partial_phase_edges'] += int(a['phase_complete'] and not b['phase_complete'])
        timelines.append({'match_id': mid, 'cohort_id': cohorts[mid], 'ward_type': kind,
                          'cell_width': width, 'grid_offset': offset, 'grid_x': gx, 'grid_y': gy,
                          'match_end_s': duration[mid], 'batches': batches, 'adjacent_batches': edges,
                          'last_batch_followup_to_match_end_s': duration[mid] - batches[-1]['placed_s'],
                          'last_batch_status': 'no_later_record_before_match_end_not_future_absence'})
    if sha(bounded(database, 128 * 1024**2)) != digest or sha(bounded(team_phase_path, 128 * 1024**2)) != sha(raw):
        raise ValueError('placement_sequences_inputs_changed')
    protocol = {'schema_version': SCHEMA, 'database_sha256': digest, 'source_team_phase_sha256': sha(raw),
                'source_in_game_placements': len(rows), 'widths': list(WIDTHS),
                'same_clock_grouping': 'exact_raw_game_timestamp_no_epsilon_or_index_order',
                'adjacency': 'next_strictly_later_batch_in_same_match_kind_grid',
                'all_original_ward_refs_retained': True, 'partial_phases_reference_only': True,
                'no_revisit_probability_or_fixed_horizon_estimate': True,
                'meaning': 'placement_order_not_vision_overlap_detection_deward_or_control_transfer',
                'limitations': ['same_grid_not_same_point', 'no_cross_match_chronology',
                                'last_batch_followup_ends_at_match_end', 'unknown_build_not_same_version',
                                'alternate_scales_not_independent_observations'],
                'counts': {**stats, 'timelines': len(timelines)}}
    result = {**protocol, 'timelines': timelines}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (('placement-sequences.json', result), ('sequence-protocol.json', protocol)):
        with (output / name).open('x', encoding='utf-8', newline='\n') as f:
            json.dump(value, f, separators=(',', ':'))
    return result
