"""Source-bound target-event time references for exact placement batches."""
from __future__ import annotations

import base64
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sqlite3
import zlib

from .postgame_store import bounded, sha, number
from .postgame_event_types import TARGET_FAMILIES, event_family
from .postgame_capture_review import clock_time
from .ward_placement_sequences import SCHEMA as SEQUENCES

SCHEMA = 'ward_sequence_target_time_context_v1'
RADIUS = 90
TIME_BASES = {
    'legacy_archive': 'same_DEM_packet_clock_manifest_equal_combat_event_time_retained_separately',
    'replay_capture': 'same_DEM_same_pass_packet_clock_event_clock_retained_separately',
}


def _blob(c, source_id, kind, limit):
    row = c.execute('SELECT * FROM context_source_blobs WHERE source_id=? AND kind=?', (source_id, kind)).fetchone()
    if (row is None or row['encoding'] != 'zlib' or not 0 <= row['raw_bytes'] <= limit
            or len(row['content']) > limit or sha(row['content']) != row['stored_sha256']):
        raise ValueError('source_blob_binding')
    decoder = zlib.decompressobj()
    raw = decoder.decompress(row['content'], limit + 1)
    if (len(raw) != row['raw_bytes'] or len(raw) > limit or not decoder.eof
            or decoder.unused_data or decoder.unconsumed_tail or sha(raw) != row['raw_sha256']):
        raise ValueError('source_blob_expansion_or_digest')
    return raw


def _gate(c, match, source):
    mid, kind = match['match_id'], source['source_kind']
    p = json.loads(source['provenance_json'])
    if source['source_id'] != sha((mid + ':' + kind + ':' + source['primary_sha256']).encode()):
        raise ValueError('source_identifier_binding')
    ward = c.execute("SELECT * FROM sources WHERE match_id=? AND kind='manifest.json'", (mid,)).fetchone()
    if ward is None or sha(ward['content']) != ward['sha256']:
        raise ValueError('ward_manifest_digest')
    wm = json.loads(ward['content'])
    if kind == 'legacy_archive':
        raw = _blob(c, source['source_id'], 'legacy_manifest', 1024**2)
        m = json.loads(raw)
        if (sha(raw) != source['primary_sha256'] or sha(raw) != p['manifest_sha256']
                or p.get('game_clock_equal_to_ward_export') is not True
                or m.get('kind') != 'offline_replay_archive'
                or str(m.get('parsed_match_id')) != mid or m.get('truncated_at_tick') is not None
                or m.get('parser', {}).get('version') != wm.get('parser_version')
                or m.get('parser', {}).get('upstream_commit') != wm.get('parser_commit')):
            raise ValueError('legacy_manifest_binding')
        expected_context = sha(raw)
    elif kind == 'replay_capture':
        raw = _blob(c, source['source_id'], 'frozen_capture', 20 * 1024**2)
        frozen = json.loads(raw)
        if (sha(raw) != p['capture_sha256']
                or frozen.get('kind') != 'isolated_postgame_capture_export' or frozen.get('schema_version') != 1):
            raise ValueError('capture_export_binding')
        manifest_raw = base64.b64decode(frozen['manifest_base64'], validate=True)
        context_raw = base64.b64decode(frozen['context_base64'], validate=True)
        wards_raw = base64.b64decode(frozen['wards_base64'], validate=True)
        m = json.loads(manifest_raw)
        if (sha(manifest_raw) != p['manifest_sha256'] or sha(context_raw) != p['context_sha256']
                or sha(context_raw) != source['primary_sha256']
                or sha(wards_raw) != match['wards_sha256']
                or m.get('complete') is not True or m.get('independent_footer_matches_parser') is not True
                or m.get('parser_version') != wm.get('parser_version') or m.get('parser_commit') != wm.get('parser_commit')
                or p.get('ward_bytes_identical_to_baseline') is not True):
            raise ValueError('capture_manifest_binding')
        for name, body in (('wards.json', wards_raw), ('postgame_context.json', context_raw)):
            if m.get('files', {}).get(name, {}).get('sha256') != sha(body) or m['files'][name].get('bytes') != len(body):
                raise ValueError('capture_file_digest')
        expected_context = sha(context_raw)
    else:
        raise ValueError('source_kind_unverified')
    if (str(m.get('match_id')) != mid or p.get('match_id') != mid
            or m.get('source_sha256') != match['replay_sha256'] or wm.get('source_sha256') != match['replay_sha256']
            or p.get('replay_sha256') != match['replay_sha256']
            or m.get('game_clock') != wm.get('game_clock') or not isinstance(wm.get('game_clock'), dict)
            or m.get('source_unchanged_stat') is not True or m.get('stop_reason') != 'replay_eof' or m.get('parse_error')):
        raise ValueError('source_replay_or_clock_binding')
    return {'clock': wm['game_clock'], 'clock_sha256': sha(json.dumps(wm['game_clock'], sort_keys=True).encode()),
            'expected_event_context_sha256': expected_context, 'ward_manifest_sha256': ward['sha256']}


def analyze(database, sequence_path, output):
    database, sequence_path, output = (Path(p).resolve() for p in (database, sequence_path, output))
    if any((output / n).exists() for n in ('sequence-target-context.json', 'sequence-context-protocol.json')):
        raise ValueError('sequence_context_new_files_required')
    digest = sha(bounded(database, 128 * 1024**2))
    raw = bounded(sequence_path, 128 * 1024**2)
    sequences = json.loads(raw)
    if sequences.get('schema_version') != SEQUENCES or sequences.get('database_sha256') != digest:
        raise ValueError('sequence_context_input_binding')
    c = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    decisions, targets, exclusions = {}, [], []
    try:
        matches = {r['match_id']: dict(r) for r in c.execute('SELECT * FROM matches')}
        sources = {r['match_id']: dict(r) for r in c.execute('SELECT * FROM context_sources')}
        if len(matches) > 256:
            raise ValueError('sequence_context_match_budget')
        for mid, match in sorted(matches.items()):
            source = sources.get(mid)
            decision = {'match_id': mid, 'source_id': source['source_id'] if source else None,
                        'source_kind': source['source_kind'] if source else None}
            if source is None:
                decision['status'] = 'context_not_recorded'
                decisions[mid] = decision
                continue
            if source['source_kind'] == 'api_projection':
                decision['status'] = 'public_projection_clock_unverified'
                decisions[mid] = decision
                continue
            try:
                gate = _gate(c, match, source)
            except (ValueError, KeyError, TypeError, zlib.error) as exc:
                decision['status'] = 'source_rejected'
                reason = str(exc)
                decision['reason'] = reason if re.fullmatch('[a-z_]{1,80}', reason) else type(exc).__name__
                decisions[mid] = decision
                continue
            decision.update({'status': 'same_replay_manifest_clock_bound',
                             'clock_sha256': gate['clock_sha256'], 'ward_manifest_sha256': gate['ward_manifest_sha256'],
                             'primary_source_sha256': source['primary_sha256'],
                             'coverage': 'hero_involvement_filtered_partial_objectives' if source['source_kind'] == 'legacy_archive'
                             else 'recorded_capture_target_families_not_exhaustiveness_proof'})
            decisions[mid] = decision
            rows = c.execute('''SELECT e.*,r.family,r.phase,r.source_id FROM events e JOIN context_event_refs r
                USING(match_id,event_kind,event_index) WHERE r.source_id=? ORDER BY e.time_s,e.event_kind,e.event_index''', (source['source_id'],))
            for row in rows:
                if row['family'] not in TARGET_FAMILIES:
                    continue
                event_id = sha((source['source_id'] + ':' + row['event_kind'] + ':' + str(row['event_index'])).encode())
                reason = None
                payload = json.loads(row['payload'])
                if row['time_s'] is None:
                    reason = 'target_packet_time_not_recorded'
                elif row['phase'] != 'in_game' or not number(row['time_s']) or not 0 <= row['time_s'] < match['duration_s']:
                    reason = 'outside_in_game_boundary'
                elif row['context_sha256'] != gate['expected_event_context_sha256'] or row['time_basis'] != TIME_BASES[source['source_kind']]:
                    reason = 'event_context_or_time_basis_mismatch'
                elif event_family(row) != row['family']:
                    reason = 'event_family_mismatch'
                else:
                    tick = payload.get('packet_tick')
                    reconstructed = clock_time(gate['clock'], tick) if type(tick) is int and tick >= 0 else None
                    if (reconstructed is None or not number(payload.get('packet_game_time_s'))
                            or abs(reconstructed - row['time_s']) > 1e-5
                            or abs(payload['packet_game_time_s'] - row['time_s']) > 1e-5):
                        reason = 'event_packet_clock_mismatch'
                if reason:
                    exclusions.append({'match_id': mid, 'source_id': source['source_id'], 'event_id': event_id, 'reason': reason})
                    continue
                target = payload.get('target')
                target = target if isinstance(target, str) and re.fullmatch('npc_dota_[a-z0-9_]{1,100}', target) else None
                targets.append({'event_id': event_id, 'match_id': mid, 'source_id': source['source_id'],
                                'event_kind': row['event_kind'], 'event_index': row['event_index'],
                                'family': row['family'], 'packet_tick': payload['packet_tick'],
                                'game_time_s': row['time_s'], 'event_clock_s_reference_only': payload.get('event_game_time_s')
                                if number(payload.get('event_game_time_s')) else None,
                                'target_unit': target, 'time_basis': row['time_basis'], 'spatial_relation': 'not_verified_time_only'})
    finally:
        c.close()
    targets.sort(key=lambda e: (e['match_id'], e['game_time_s'], e['event_id']))
    by_match = defaultdict(list)
    for event in targets:
        by_match[event['match_id']].append(event)
    times = {mid: [r['game_time_s'] for r in rows] for mid, rows in by_match.items()}
    contexts = []
    for sequence_index, timeline in enumerate(sequences['timelines']):
        mid = timeline['match_id']
        if mid not in decisions or timeline['match_end_s'] != matches[mid]['duration_s']:
            raise ValueError('sequence_context_timeline_match_binding')
        if decisions[mid]['status'] != 'same_replay_manifest_clock_bound':
            continue
        events, stamps = by_match[mid], times.get(mid, [])
        for i, batch in enumerate(timeline['batches']):
            stamp = batch['placed_s']
            neighbors = [{'event_id': r['event_id'], 'event_minus_placement_s': r['game_time_s'] - stamp}
                         for r in events[bisect_left(stamps, stamp - RADIUS):bisect_right(stamps, stamp + RADIUS)]]
            contexts.append({'sequence_index': sequence_index, 'batch_index': i, 'placed_s': stamp,
                             'window_start_s': max(0, stamp - RADIUS), 'window_end_s': min(timeline['match_end_s'], stamp + RADIUS),
                             'full_before_window': stamp >= RADIUS,
                             'full_after_window': stamp + RADIUS <= timeline['match_end_s'],
                             'status': 'recorded_target_time_neighbors' if neighbors else 'no_recorded_target_in_window',
                             'neighbor_events': neighbors})
    if len(targets) > 5000 or len(contexts) > 100_000 or sum(len(r['neighbor_events']) for r in contexts) > 500_000:
        raise ValueError('sequence_context_row_budget')
    if sha(bounded(database, 128 * 1024**2)) != digest or sha(bounded(sequence_path, 128 * 1024**2)) != sha(raw):
        raise ValueError('sequence_context_inputs_changed')
    protocol = {'schema_version': SCHEMA, 'database_sha256': digest, 'source_sequence_sha256': sha(raw),
                'radius_s': RADIUS, 'target_families': sorted(TARGET_FAMILIES),
                'source_gate': 'exact_replay_manifest_clock_and_packet_tick_no_api_clock_fallback',
                'delta_definition': 'event_game_time_minus_placement_game_time_positive_event_is_later',
                'all_recorded_neighbors_within_inclusive_window': True,
                'no_context_is_missing_not_zero': True, 'no_spatial_or_causal_inference': True,
                'counts': {'match_decisions': dict(Counter(r['status'] for r in decisions.values())),
                           'target_events': len(targets), 'excluded_target_events': len(exclusions),
                           'batch_contexts': len(contexts), 'batch_context_statuses': dict(Counter(r['status'] for r in contexts)),
                           'time_neighbor_references': sum(len(r['neighbor_events']) for r in contexts)}}
    result = {**protocol, 'source_decisions': decisions, 'target_events': targets,
              'excluded_events': exclusions, 'batch_contexts': contexts}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (('sequence-target-context.json', result), ('sequence-context-protocol.json', protocol)):
        with (output / name).open('x', encoding='utf-8', newline='\n') as f:
            json.dump(value, f, separators=(',', ':'))
    return result
