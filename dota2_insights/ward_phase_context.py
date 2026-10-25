"""Per-match ten-minute target and pre-phase raw-counter ledger, without inference."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import re
from pathlib import Path
import sqlite3
import zlib

from .postgame_store import bounded, sha, number
from .postgame_event_types import TARGET_FAMILIES, event_family
from .postgame_capture_review import clock_time
from .ward_patterns import PHASE
from .ward_team_phase_states import SCHEMA as TEAM_SCHEMA
from .ward_sequence_context import SCHEMA as TARGET_SCHEMA, TIME_BASES, _gate
from .ward_context_balance import checkpoint, MAX_AGE
from .postgame_target_names import attributes as target_attributes, VERSION as NAME_SCHEMA

SCHEMA = 'ward_phase_context_ledger_v2'


def analyze(database, team_path, target_path, output):
    database, team_path, target_path, output = (Path(p).resolve() for p in (database, team_path, target_path, output))
    if output.exists():
        raise ValueError('phase_context_new_output_required')
    digest = sha(bounded(database, 128 * 1024**2))
    team_raw, target_raw = (bounded(p, 128 * 1024**2) for p in (team_path, target_path))
    team, target = json.loads(team_raw), json.loads(target_raw)
    if (team.get('schema_version') != TEAM_SCHEMA or target.get('schema_version') != TARGET_SCHEMA
            or team.get('database_sha256') != digest or target.get('database_sha256') != digest):
        raise ValueError('phase_context_input_binding')
    c = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    records, events, decisions = [], {}, {}
    try:
        matches = {r['match_id']: dict(r) for r in c.execute('SELECT * FROM matches')}
        sources = {r['match_id']: dict(r) for r in c.execute('SELECT * FROM context_sources')}
        if len(matches) > 256:
            raise ValueError('phase_context_match_budget')
        membership = {}
        for group in team['cohorts']:
            for mid in group['match_ids']:
                if mid not in matches or mid in membership:
                    raise ValueError('phase_context_cohort_membership')
                membership[mid] = group['cohort_id']
        if set(membership) != set(matches) or set(target['source_decisions']) != set(matches):
            raise ValueError('phase_context_match_coverage')
        indexed = {(e['match_id'], e['event_kind'], e['event_index']): e for e in target['target_events']}
        if len(indexed) != len(target['target_events']):
            raise ValueError('phase_context_duplicate_event')
        accepted = set()
        packets = defaultdict(list)
        for row in c.execute('SELECT * FROM replay_metric_packets ORDER BY source_id,time_s,packet_tick,packet_index'):
            packets[row['source_id']].append(dict(row))
        if sum(map(len, packets.values())) > 100_000:
            raise ValueError('phase_context_packet_budget')
        for mid, match in sorted(matches.items()):
            source = sources.get(mid)
            decision = target['source_decisions'][mid]
            if (decision['source_id'] != (source['source_id'] if source else None)
                    or decision['source_kind'] != (source['source_kind'] if source else None)):
                raise ValueError('phase_context_selected_source_changed')
            gate = None
            if source is None:
                status = 'context_not_recorded'
            elif source['source_kind'] == 'api_projection':
                status = 'public_projection_clock_unverified'
            else:
                try:
                    gate = _gate(c, match, source)
                    status = 'same_replay_manifest_clock_bound'
                except (ValueError, KeyError, TypeError, zlib.error):
                    status = 'source_rejected'
            if decision['status'] != status:
                raise ValueError('phase_context_source_gate_changed')
            decisions[mid] = {**decision, 'event_coverage': 'recorded_targets_not_exhaustiveness_proof',
                              'original_missing_packet_time': json.loads(source['provenance_json']).get('event_packet_time_missing')
                              if source else None}
            if gate is not None:
                if (decision['clock_sha256'] != gate['clock_sha256']
                        or decision['ward_manifest_sha256'] != gate['ward_manifest_sha256']
                        or decision['primary_source_sha256'] != source['primary_sha256']):
                    raise ValueError('phase_context_clock_binding')
                for row in c.execute('''SELECT e.*,r.family,r.phase FROM events e JOIN context_event_refs r
                    USING(match_id,event_kind,event_index) WHERE r.source_id=?''', (source['source_id'],)):
                    if row['family'] not in TARGET_FAMILIES:
                        continue
                    payload = json.loads(row['payload'])
                    if (row['phase'] != 'in_game' or not number(row['time_s'])
                            or not 0 <= row['time_s'] < match['duration_s']
                            or row['context_sha256'] != gate['expected_event_context_sha256']
                            or row['time_basis'] != TIME_BASES[source['source_kind']]
                            or event_family(row) != row['family']):
                        continue
                    tick = payload.get('packet_tick')
                    final = clock_time(gate['clock'], tick) if type(tick) is int and tick >= 0 else None
                    if (final is None or not number(payload.get('packet_game_time_s'))
                            or abs(final - row['time_s']) > 1e-5
                            or abs(payload['packet_game_time_s'] - row['time_s']) > 1e-5):
                        continue
                    key = mid, row['event_kind'], row['event_index']
                    event = indexed.get(key)
                    unit = payload.get('target')
                    unit = unit if isinstance(unit, str) and re.fullmatch('npc_dota_[a-z0-9_]{1,100}', unit) else None
                    expected_event = {'event_id': sha((source['source_id'] + ':' + row['event_kind'] + ':' + str(row['event_index'])).encode()),
                                      'match_id': mid, 'source_id': source['source_id'],
                                      'event_kind': row['event_kind'], 'event_index': row['event_index'],
                                      'family': row['family'], 'packet_tick': tick, 'game_time_s': row['time_s'],
                                      'event_clock_s_reference_only': payload.get('event_game_time_s')
                                      if number(payload.get('event_game_time_s')) else None,
                                      'target_unit': unit, 'time_basis': row['time_basis'],
                                      'spatial_relation': 'not_verified_time_only'}
                    if event != expected_event:
                        raise ValueError('phase_context_target_reference_changed')
                    accepted.add(key)
                    events[event['event_id']] = {**event, 'target_name_attributes': target_attributes(unit, row['family'])}
            count = math.ceil(match['duration_s'] / PHASE)
            if not 1 <= count <= 36:
                raise ValueError('phase_context_duration_budget')
            own_events = sorted((e for e in events.values() if e['match_id'] == mid),
                                key=lambda e: (e['game_time_s'], e['event_id']))
            for phase in range(count):
                start, end = phase * PHASE, min((phase + 1) * PHASE, match['duration_s'])
                within = [e for e in own_events if start <= e['game_time_s'] < end]
                prior = [e for e in own_events if e['game_time_s'] < start]
                if gate is None:
                    cp = {'status': status, 'source_id': source['source_id'] if source else None,
                          'requested_window_start_s': start, 'packet_ref': None,
                          'raw_team_counter_sums': None, 'radiant_minus_dire_raw': None, 'players': []}
                else:
                    cp = checkpoint(packets[source['source_id']], source, start)
                records.append({'match_id': mid, 'cohort_id': membership[mid], 'phase': phase,
                                'start_s': start, 'observed_end_s': end, 'nominal_end_s': (phase + 1) * PHASE,
                                'phase_status': 'complete' if end == (phase + 1) * PHASE else 'partial_reference_only',
                                'source_status': status, 'source_id': source['source_id'] if source else None,
                                'phase_event_ids': [e['event_id'] for e in within],
                                'phase_event_family_counts': dict(Counter(e['family'] for e in within)) if gate else None,
                                'before_start_event_ids': [e['event_id'] for e in prior],
                                'before_start_event_family_counts': dict(Counter(e['family'] for e in prior)) if gate else None,
                                'pre_phase_checkpoint': cp})
        if accepted != set(indexed):
            raise ValueError('phase_context_all_target_references_required')
    finally:
        c.close()
    protocol = {'schema_version': SCHEMA, 'database_sha256': digest, 'source_team_phase_sha256': sha(team_raw),
                'source_target_context_sha256': sha(target_raw), 'phase_seconds': PHASE,
                'phase_event_interval': 'start_inclusive_observed_end_exclusive',
                'pre_phase_packet': 'strictly_before_start_same_source_in_game_latest_time_tick_index_no_fallback',
                'max_checkpoint_age_s': MAX_AGE, 'raw_field': 'm_iNetWorth_not_semantically_certified',
                'target_name_schema': NAME_SCHEMA,
                'target_name_basis': 'recorded_class_name_not_attacker_team_unique_entity_or_world_position',
                'name_record_groups_are_not_entity_counts_or_automatic_deduplication': True,
                'missing_event_source_counts_are_null_not_zero': True,
                'before_start_events_are_recorded_counts_not_towers_remaining': True,
                'limits': ['recorded_target_coverage_not_exhaustive', 'no_spatial_or_visibility_certification',
                           'raw_counter_not_player_GSI_or_visible_equipment_economy',
                           'phase_events_are_outcomes_during_phase_not_prephase_conditions',
                           'do_not_condition_prephase_comparisons_on_future_phase_events',
                           'partial_phases_reference_only_no_interpolation_no_causal_or_tactical_claim']}
    by_name = defaultdict(list)
    for event in events.values():
        if event['target_unit'] is not None:
            by_name[event['match_id'], event['target_unit']].append(event['event_id'])
    name_groups = [{'match_id': mid, 'unit_name': unit,
                    'event_ids': sorted(ids, key=lambda eid: (events[eid]['game_time_s'], eid)),
                    'status': 'multiple_records_same_class_name_not_unique_entity' if len(ids) > 1 else 'single_record_name_not_unique_entity'}
                   for (mid, unit), ids in sorted(by_name.items())]
    result = {**protocol, 'source_decisions': decisions, 'events': list(events.values()), 'records': records,
              'target_name_record_groups': name_groups,
              'counts': {'matches': len(matches), 'phases': len(records), 'bound_target_events': len(events),
                         'source_statuses': dict(Counter(v['status'] for v in decisions.values())),
                         'phase_statuses': dict(Counter(v['phase_status'] for v in records)),
                         'checkpoint_statuses': dict(Counter(v['pre_phase_checkpoint']['status'] for v in records)),
                         'target_name_statuses': dict(Counter(v['target_name_attributes']['status'] for v in events.values())),
                         'target_name_kinds': dict(Counter(v['target_name_attributes']['kind'] or 'not_parsed' for v in events.values())),
                         'multiple_records_same_class_name_groups': sum(len(r['event_ids']) > 1 for r in name_groups)}}
    if (sha(bounded(database, 128 * 1024**2)) != digest or sha(bounded(team_path, 128 * 1024**2)) != sha(team_raw)
            or sha(bounded(target_path, 128 * 1024**2)) != sha(target_raw)):
        raise ValueError('phase_context_inputs_changed')
    output.mkdir(parents=True)
    for name, value in (('phase-context.json', result), ('protocol.json', protocol)):
        (output / name).write_text(json.dumps(value, separators=(',', ':')), encoding='utf-8', newline='\n')
    return result
