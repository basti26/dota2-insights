"""Immutable postgame facts from existing exports, without coaching verdicts."""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

VERSION = 'postgame_facts_v1'
HEX = re.compile(r'[a-f0-9]{64}')
WARD_FIELDS = ('tick', 'player_id', 'placer', 'ward_type', 'team', 'x', 'y',
               'expires_tick', 'killed_tick', 'killer', 'left_player_id', 'match_id',
               'placed_game_time_s', 'ended_game_time_s', 'end_kind', 'coordinate_basis',
               'duration_s', 'after_match_end', 'offline_only')


def sha(content):
    return hashlib.sha256(content).hexdigest()


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def mid(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value):
        raise ValueError('postgame_match_id_invalid')
    return value


def bounded(path, limit):
    with Path(path).open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError('postgame_source_size_limit')
    return content


def create_store(snapshot_path, output, context_paths=()):
    """Build a new SQLite snapshot. Never update source exports or old runs."""
    snapshot_path, output = Path(snapshot_path).resolve(), Path(output).resolve()
    if output.exists() or len(context_paths) > 256:
        raise ValueError('postgame_new_output_required')
    raw = bounded(snapshot_path, 32 * 1024**2)
    snapshot = json.loads(raw)
    if (snapshot.get('kind') != 'frozen_verified_ward_exports'
            or snapshot.get('schema_version') != 1 or snapshot.get('raw_replays_transferred') is not False
            or not isinstance(snapshot.get('matches'), list) or not 1 <= len(snapshot['matches']) <= 256):
        raise ValueError('postgame_frozen_snapshot_required')
    when = datetime.fromisoformat(snapshot['frozen_at_utc'])
    if when.utcoffset() is None or when.utcoffset().total_seconds() != 0:
        raise ValueError('postgame_snapshot_UTC_required')
    contexts, context_sources, context_files = {}, [], []
    for path in context_paths:
        path = Path(path).resolve()
        content = bounded(path, 4 * 1024**2)
        receipt = json.loads(content)
        data = receipt.get('data')
        if not isinstance(data, dict):
            raise ValueError('postgame_public_projection_required')
        identifier = mid(str(data.get('match_id')))
        if identifier in contexts:
            raise ValueError('postgame_duplicate_context_match')
        if (receipt.get('projection') != 'public_gameplay_fields_no_accounts_names_chat_credentials'
                or receipt.get('source_url') != 'https://api.opendota.com/api/matches/' + identifier
                or not HEX.fullmatch(str(receipt.get('raw_response_sha256', '')))):
            raise ValueError('postgame_context_provenance_invalid')
        contexts[identifier] = (data, sha(content))
        context_sources.append({'file': path.name, 'sha256': sha(content)})
        context_files.append((path,sha(content)))
    matches, wards, sources, events, issues = [], [], [], [], []
    player_summaries, player_series, context_coverage, public_wards = [], [], [], []
    seen = set()
    for entry in snapshot['matches']:
        identifier = mid(entry['match_id'])
        if identifier in seen:
            raise ValueError('postgame_duplicate_match')
        seen.add(identifier)
        manifest_raw = base64.b64decode(entry['manifest_base64'], validate=True)
        ward_raw = base64.b64decode(entry['wards_base64'], validate=True)
        if len(manifest_raw) > 256 * 1024 or len(ward_raw) > 5 * 1024**2:
            raise ValueError('postgame_export_budget')
        manifest, records = json.loads(manifest_raw), json.loads(ward_raw)
        catalog = entry['catalog']
        if (manifest.get('match_id') != identifier or manifest.get('complete') is not True
                or manifest.get('source_unchanged_stat') is not True or manifest.get('parse_error')
                or manifest.get('stop_reason') != 'replay_eof'
                or manifest.get('independent_footer_matches_parser') is not True
                or not HEX.fullmatch(str(catalog.get('sha256', '')))
                or manifest.get('source_sha256') != catalog['sha256']
                or manifest.get('files', {}).get('wards.json', {}).get('sha256') != sha(ward_raw)
                or catalog.get('analysis_sha256') != sha(ward_raw)
                or not isinstance(records, list) or len(records) > 5000
                or manifest.get('ward_count') != len(records)):
            raise ValueError('postgame_export_binding_invalid')
        duration = manifest.get('match_end_game_time_s')
        if not number(duration) or not 0 < duration <= 10800:
            raise ValueError('postgame_match_duration_invalid')
        origin = catalog.get('sources')
        if not isinstance(origin, list) or not origin or any(s not in ('own', 'd2pt') for s in origin):
            raise ValueError('postgame_origin_invalid')
        source_group = 'own' if 'own' in origin else 'd2pt'
        producer = {k: manifest.get(k) for k in ('parser_version', 'parser_commit', 'parser_sha256',
                                                'ward_extractor_sha256', 'adapter_sha256')}
        if not all(isinstance(v, str) and v for v in producer.values()):
            raise ValueError('postgame_producer_provenance_missing')
        cohort = sha(json.dumps(producer, sort_keys=True).encode())
        context, context_sha = contexts.get(identifier, ({}, None))
        patch = context.get('patch')
        mode = context.get('game_mode')
        parsed = type(context.get('version')) is int
        objectives = context.get('objectives')
        players = context.get('players') or []
        coverage = 'parsed_public_projection' if parsed else 'public_summary_only' if context else 'not_recorded'
        sampling = catalog.get('sampling') or {}
        mmr = sampling.get('mmr') if type(sampling.get('mmr')) is int else None
        match = (identifier, source_group, json.dumps(origin), duration, catalog['sha256'], sha(ward_raw),
                 cohort, json.dumps(producer, sort_keys=True), str(patch) if type(patch) is int else 'not_recorded',
                 'not_recorded', str(mode) if type(mode) is int else 'not_recorded', mmr,
                 coverage, context_sha, int(context['radiant_win']) if type(context.get('radiant_win')) is bool else None)
        matches.append(match)
        sources.extend([(identifier, 'manifest.json', sha(manifest_raw), manifest_raw),
                        (identifier, 'wards.json', sha(ward_raw), ward_raw)])
        fingerprints = Counter()
        for index, record in enumerate(records):
            if (not isinstance(record, dict) or record.get('match_id') != identifier
                    or record.get('ward_type') not in ('observer', 'sentry')):
                raise ValueError('postgame_ward_identity_invalid')
            # Preserve known fields, never unrestricted future/raw entity data.
            ward = {k: record.get(k) for k in WARD_FIELDS}
            placed, ended = ward['placed_game_time_s'], ward['ended_game_time_s']
            x, y, team = ward['x'], ward['y'], ward['team']
            numeric = number(placed) and all(number(v) and abs(v) <= 65536 for v in (x, y))
            if not numeric or type(team) is not int or team not in (2, 3):
                placement_status = 'invalid_or_missing_fields'
            elif placed < 0:
                placement_status = 'pregame'
            elif placed >= duration or ward['after_match_end'] is True:
                placement_status = 'postgame_or_end_boundary'
            else:
                placement_status = 'in_game'
            lifetime = ('observed_end' if number(ended) and number(placed) and placed <= ended <= duration
                        and ward['end_kind'] in ('killed', 'expired') else
                        'right_censored_at_match_end' if number(ended) and ended > duration else 'unknown_end')
            fp = sha(json.dumps([ward[k] for k in ('tick','player_id','ward_type','team','x','y')]).encode())
            fingerprints[fp] += 1
            wards.append((identifier, index, ward['ward_type'], team,
                          x if number(x) else None, y if number(y) else None,
                          placed if number(placed) else None, ended if number(ended) else None,
                          ward['duration_s'] if number(ward['duration_s']) else None,
                          ward['end_kind'], placement_status, lifetime, ward['coordinate_basis'], fp,
                          json.dumps(ward, ensure_ascii=False, allow_nan=False)))
            if len(wards)>100_000:
                raise ValueError('postgame_total_ward_budget')
        for fp, count in fingerprints.items():
            if count > 1:
                issues.append((identifier, 'repeated_event_fingerprint_not_automatically_deduplicated',
                               json.dumps({'fingerprint': fp, 'count': count})))
        if context:
            status=lambda value: ('reported_empty' if isinstance(value,list) and not value else
                                  'reported_values' if isinstance(value,list) else 'not_recorded')
            context_coverage.append((identifier,'objectives',status(objectives),context_sha))
            if not isinstance(players,list) or len(players)>10:
                raise ValueError('postgame_public_player_budget')
            slots=set()
            for player in players:
                slot=player.get('player_slot');hero=player.get('hero_id')
                if (type(slot) is not int or slot not in (*range(5),*range(128,133))
                        or slot in slots or type(hero) is not int or not 1<=hero<=10000):
                    raise ValueError('postgame_public_player_identity')
                slots.add(slot)
                fields=('kills','deaths','assists','last_hits','denies','gold_per_min','xp_per_min',
                        'net_worth','gold_spent','hero_damage','tower_damage','hero_healing',
                        'lane','lane_role','lane_efficiency_pct')
                summary={k:player.get(k) if number(player.get(k)) else None for k in fields}
                summary['is_roaming']=player.get('is_roaming') if type(player.get('is_roaming')) is bool else None
                player_summaries.append((identifier,slot,hero,2 if slot<128 else 3,
                                         json.dumps(summary,allow_nan=False),context_sha))
                for field in ('times','gold_t','xp_t','lh_t','dn_t','purchase_log','kills_log',
                              'buyback_log','obs_log','sen_log'):
                    context_coverage.append((identifier,field+':'+str(slot),status(player.get(field)),context_sha))
                times=player.get('times')
                if isinstance(times,list):
                    if len(times)>1000:
                        raise ValueError('postgame_series_budget')
                    for index,stamp in enumerate(times):
                        if not number(stamp):
                            continue
                        for field in ('gold_t','xp_t','lh_t','dn_t'):
                            values=player.get(field)
                            value=values[index] if isinstance(values,list) and index<len(values) else None
                            valid=number(value)
                            player_series.append((identifier,slot,field,index,stamp,value if valid else None,
                                                  'reported_value' if valid else 'not_recorded_at_time',context_sha))
                for field,kind in (('obs_log','observer'),('sen_log','sentry')):
                    records_public=player.get(field) or []
                    if not isinstance(records_public,list) or len(records_public)>5000:
                        raise ValueError('postgame_public_ward_budget')
                    for index,record in enumerate(records_public):
                        if number(record.get('time')):
                            payload={k:record.get(k) for k in ('x','y','z','entityleft','ehandle')}
                            public_wards.append((identifier,slot,kind,index,record['time'],
                                json.dumps(payload,allow_nan=False),context_sha,
                                'opendota_xy_not_transformed_or_merged_with_DEM_coordinates'))
        # Only explicitly reported public events enter the event table. Missing
        # logs remain absent, not "zero events" or inferred from final outcomes.
        if parsed:
            for index, event in enumerate(objectives if isinstance(objectives, list) else []):
                if not number(event.get('time')):
                    continue
                payload = {k: event[k] for k in ('type','key','team','unit','slot','player_slot','value') if k in event}
                events.append((identifier, 'objective', index, event['time'],
                               event.get('team') if type(event.get('team')) is int else None,
                               None, json.dumps(payload), context_sha, 'opendota_reported_time_not_exact_demo_alignment'))
            for player in players:
                slot = player.get('player_slot')
                if type(slot) is not int or slot not in (*range(5), *range(128,133)):
                    continue
                team = 2 if slot < 128 else 3
                for kind, key in (('hero_kill','kills_log'), ('buyback','buyback_log'),('purchase','purchase_log')):
                    for index, event in enumerate(player.get(key) or []):
                        if number(event.get('time')):
                            payload = {k: event[k] for k in ('key','type') if k in event}
                            events.append((identifier, kind + ':' + str(slot), index, event['time'], team,
                                           player.get('hero_id'), json.dumps(payload), context_sha,
                                           'opendota_reported_time_not_exact_demo_alignment'))
    for entry in snapshot.get('excluded_catalog_rows',[]):
        issues.append((mid(entry['match_id']),'catalog_row_not_imported',json.dumps({
            k:entry.get(k) for k in ('download_status','analysis_status')})))
    if (sha(bounded(snapshot_path, 32 * 1024**2)) != sha(raw)
            or any(sha(bounded(path,4*1024**2))!=expected for path,expected in context_files)):
        raise ValueError('postgame_snapshot_changed')
    output.mkdir(parents=True)
    connection = sqlite3.connect(output / 'facts.sqlite')
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript('''
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE matches(match_id TEXT PRIMARY KEY,source_group TEXT,source_types TEXT,duration_s REAL,
                replay_sha256 TEXT,wards_sha256 TEXT,producer_cohort TEXT,producer_json TEXT,
                patch_key TEXT,map_key TEXT,mode_key TEXT,source_mmr INTEGER,context_status TEXT,
                context_sha256 TEXT,radiant_win INTEGER);
            CREATE TABLE wards(match_id TEXT,ward_index INTEGER,ward_type TEXT,team INTEGER,x REAL,y REAL,
                placed_s REAL,ended_s REAL,duration_s REAL,end_kind TEXT,placement_status TEXT,
                lifetime_status TEXT,coordinate_basis TEXT,event_fingerprint TEXT,record_json TEXT,
                PRIMARY KEY(match_id,ward_index),FOREIGN KEY(match_id) REFERENCES matches(match_id));
            CREATE TABLE sources(match_id TEXT,kind TEXT,sha256 TEXT,content BLOB,
                PRIMARY KEY(match_id,kind),FOREIGN KEY(match_id) REFERENCES matches(match_id));
            CREATE TABLE events(match_id TEXT,event_kind TEXT,event_index INTEGER,time_s REAL,team INTEGER,
                actor_hero_numeric INTEGER,payload TEXT,context_sha256 TEXT,time_basis TEXT,
                PRIMARY KEY(match_id,event_kind,event_index),FOREIGN KEY(match_id) REFERENCES matches(match_id));
            CREATE TABLE issues(match_id TEXT,kind TEXT,detail TEXT);
            CREATE TABLE player_summaries(match_id TEXT,player_slot INTEGER,hero_numeric INTEGER,team INTEGER,
                summary_json TEXT,context_sha256 TEXT,PRIMARY KEY(match_id,player_slot),
                FOREIGN KEY(match_id) REFERENCES matches(match_id));
            CREATE TABLE player_series(match_id TEXT,player_slot INTEGER,metric TEXT,sample_index INTEGER,
                time_s REAL,value REAL,status TEXT,context_sha256 TEXT,
                PRIMARY KEY(match_id,player_slot,metric,sample_index),
                FOREIGN KEY(match_id) REFERENCES matches(match_id));
            CREATE TABLE context_coverage(match_id TEXT,field TEXT,status TEXT,context_sha256 TEXT,
                PRIMARY KEY(match_id,field),FOREIGN KEY(match_id) REFERENCES matches(match_id));
            CREATE TABLE public_ward_logs(match_id TEXT,player_slot INTEGER,ward_type TEXT,event_index INTEGER,
                time_s REAL,payload TEXT,context_sha256 TEXT,coordinate_basis TEXT,
                PRIMARY KEY(match_id,player_slot,ward_type,event_index),
                FOREIGN KEY(match_id) REFERENCES matches(match_id));
            CREATE INDEX ward_space_time ON wards(match_id,ward_type,team,placed_s);
            CREATE INDEX match_cohort ON matches(source_group,producer_cohort,patch_key,map_key,mode_key);
        ''')
        with connection:
            connection.executemany('INSERT INTO metadata VALUES (?,?)', [
                ('schema_version', VERSION), ('snapshot_sha256', sha(raw)),
                ('frozen_at_utc', snapshot['frozen_at_utc']), ('policy', 'facts_not_coaching_verdicts')])
            connection.executemany('INSERT INTO matches VALUES ('+','.join('?'*15)+')', matches)
            connection.executemany('INSERT INTO wards VALUES ('+','.join('?'*15)+')', wards)
            connection.executemany('INSERT INTO sources VALUES (?,?,?,?)', sources)
            connection.executemany('INSERT INTO events VALUES ('+','.join('?'*9)+')', events)
            connection.executemany('INSERT INTO issues VALUES (?,?,?)', issues)
            connection.executemany('INSERT INTO player_summaries VALUES (?,?,?,?,?,?)',player_summaries)
            connection.executemany('INSERT INTO player_series VALUES (?,?,?,?,?,?,?,?)',player_series)
            connection.executemany('INSERT INTO context_coverage VALUES (?,?,?,?)',context_coverage)
            connection.executemany('INSERT INTO public_ward_logs VALUES (?,?,?,?,?,?,?,?)',public_wards)
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('postgame_database_integrity_failed')
    finally:
        connection.close()
    result = {'schema_version': VERSION, 'snapshot_sha256': sha(raw),
              'frozen_at_utc': snapshot['frozen_at_utc'], 'matches': len(matches), 'wards': len(wards),
              'context_events': len(events), 'source_blobs': len(sources), 'issues': len(issues),
              'player_summaries':len(player_summaries),'player_series_rows':len(player_series),
              'context_coverage_rows':len(context_coverage),'public_ward_logs':len(public_wards),
              'pending_context_matches': sorted(set(contexts)-seen),
              'context_sources': context_sources, 'integrity_check': integrity,
              'database_sha256': sha((output/'facts.sqlite').read_bytes()),
              'limits': ['Unknown patch/map/mode remain unknown, never inferred from the current installed game.',
                         'Completed exports are parser evidence, not independently verified detection/vision truth.',
                         'No player names/accounts/chat or raw entity dumps are imported.']}
    (output/'import-receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    return result
