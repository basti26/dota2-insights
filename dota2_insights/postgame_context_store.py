"""Immutable extension of postgame facts with source-bound replay contexts."""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import zlib

from .postgame_store import VERSION,bounded,sha
from .postgame_legacy_context import load_archive
from .postgame_replay_context import load_capture
from .postgame_event_types import event_family

EXTENSION='postgame_context_extension_v1'


def build(database,output,*,api_contexts=(),legacy_archives=(),captured_contexts=(),captured_metric_clock_policy='strict',captured_event_clock_policy='strict'):
    database,output=Path(database).resolve(),Path(output).resolve()
    if output.exists() or len(api_contexts)>256 or len(legacy_archives)>16 or len(captured_contexts)>16:
        raise ValueError('context_store_new_output_and_source_limits')
    baseline_digest=sha(bounded(database,128*1024**2))
    source=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    connection=None;files_used=[]
    try:
        metadata=dict(source.execute('SELECT key,value FROM metadata'))
        if metadata.get('schema_version')!=VERSION or 'context_extension_version' in metadata:
            raise ValueError('context_store_unextended_baseline_required')
        if source.execute('PRAGMA journal_mode').fetchone()[0]=='wal':
            raise ValueError('context_store_frozen_non_wal_baseline_required')
        matches={r['match_id']:dict(r) for r in source.execute('SELECT * FROM matches')}
        manifests={r['match_id']:json.loads(r['content']) for r in source.execute("SELECT match_id,content FROM sources WHERE kind='manifest.json'")}
        if len(matches)>256:raise ValueError('context_store_match_budget')
        output.mkdir(parents=True)
        connection=sqlite3.connect(output/'facts.sqlite');source.backup(connection)
        connection.row_factory=sqlite3.Row;connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript('''
            CREATE TABLE context_sources(source_id TEXT PRIMARY KEY,match_id TEXT NOT NULL,
                source_kind TEXT NOT NULL,primary_sha256 TEXT NOT NULL,provenance_json TEXT NOT NULL,
                FOREIGN KEY(match_id) REFERENCES matches(match_id),UNIQUE(match_id));
            CREATE TABLE context_source_blobs(source_id TEXT,kind TEXT,raw_sha256 TEXT,raw_bytes INTEGER,
                encoding TEXT,stored_sha256 TEXT,content BLOB,PRIMARY KEY(source_id,kind),
                FOREIGN KEY(source_id) REFERENCES context_sources(source_id));
            CREATE TABLE context_event_refs(match_id TEXT,event_kind TEXT,event_index INTEGER,
                source_id TEXT,family TEXT,phase TEXT,PRIMARY KEY(match_id,event_kind,event_index),
                FOREIGN KEY(match_id,event_kind,event_index) REFERENCES events(match_id,event_kind,event_index),
                FOREIGN KEY(source_id) REFERENCES context_sources(source_id));
            CREATE TABLE replay_metric_packets(source_id TEXT,packet_index INTEGER,match_id TEXT,
                packet_tick INTEGER,time_s REAL,phase TEXT,mapping_resolved INTEGER,
                packet_sha256 TEXT,packet_json BLOB,PRIMARY KEY(source_id,packet_index),
                FOREIGN KEY(source_id) REFERENCES context_sources(source_id),FOREIGN KEY(match_id) REFERENCES matches(match_id));
            CREATE TABLE replay_player_metrics(source_id TEXT,packet_index INTEGER,logical_player_id INTEGER,
                team INTEGER,team_slot INTEGER,resource_index INTEGER,hero_id TEXT,metric TEXT,value INTEGER,status TEXT,
                PRIMARY KEY(source_id,packet_index,logical_player_id,metric),
                FOREIGN KEY(source_id,packet_index) REFERENCES replay_metric_packets(source_id,packet_index));
            CREATE INDEX context_events_by_source ON context_event_refs(source_id,family,phase);
            CREATE INDEX replay_metrics_by_field ON replay_player_metrics(source_id,logical_player_id,metric,packet_index);
        ''')
        imported=set();summaries=[]

        def read_file(path,limit):
            path=Path(path).resolve();body=bounded(path,limit);files_used.append((path,limit,sha(body)))
            return body

        def phase(mid,stamp):
            return ('missing_time' if stamp is None else 'pregame' if stamp<0 else
                    'in_game' if stamp<matches[mid]['duration_s'] else 'postgame_or_end_boundary')

        def begin(mid,kind,primary,provenance):
            if mid not in matches or mid in imported:raise ValueError('context_store_missing_match_or_mixed_source')
            identifier=sha((mid+':'+kind+':'+primary).encode())
            connection.execute('INSERT INTO context_sources VALUES(?,?,?,?,?)',
                (identifier,mid,kind,primary,json.dumps(provenance,allow_nan=False)))
            imported.add(mid);summaries.append({'match_id':mid,'source_kind':kind,'source_id':identifier})
            return identifier

        def blob(identifier,kind,body):
            packed=zlib.compress(body,6)
            connection.execute('INSERT INTO context_source_blobs VALUES(?,?,?,?,?,?,?)',
                (identifier,kind,sha(body),len(body),'zlib',sha(packed),packed))

        def register(identifier,rows,*,insert):
            for row in rows:
                if insert:
                    connection.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',tuple(row[k] for k in
                        ('match_id','event_kind','event_index','time_s','team','actor_hero_numeric','payload','context_sha256','time_basis')))
                connection.execute('INSERT INTO context_event_refs VALUES(?,?,?,?,?,?)',
                    (row['match_id'],row['event_kind'],row['event_index'],identifier,event_family(row),phase(row['match_id'],row['time_s'])))

        with connection:
            for path in api_contexts:
                body=read_file(path,4*1024**2);receipt=json.loads(body);data=receipt.get('data',{})
                mid=str(data.get('match_id'));digest=sha(body)
                if (mid not in matches or matches[mid]['context_sha256']!=digest
                        or receipt.get('schema_version')!=1
                        or receipt.get('projection')!='public_gameplay_fields_no_accounts_names_chat_credentials'
                        or receipt.get('source_url')!='https://api.opendota.com/api/matches/'+mid):
                    raise ValueError('context_store_api_receipt_binding')
                identifier=begin(mid,'api_projection',digest,{'context_status':matches[mid]['context_status'],
                    'source_url':receipt['source_url'],'fetched_at_utc':receipt.get('fetched_at_utc'),
                    'projection_sha256':digest,'coverage':'source_fields_reported_not_completeness_proof'})
                blob(identifier,'public_api_projection',body)
                rows=[dict(r) for r in connection.execute('SELECT * FROM events WHERE match_id=?',(mid,))]
                if any(r['context_sha256']!=digest for r in rows):raise ValueError('context_store_existing_api_event_digest')
                register(identifier,rows,insert=False)
            required={m['match_id'] for m in matches.values() if m['context_sha256'] is not None}
            if not required.issubset(imported):raise ValueError('context_store_existing_api_receipts_required')
            for folder in legacy_archives:
                body=read_file(Path(folder)/'manifest.json',1024**2);manifest=json.loads(body);mid=str(manifest.get('match_id'))
                if mid not in manifests:raise ValueError('context_store_legacy_match_missing')
                rows,provenance=load_archive(folder,manifests[mid])
                events_raw=read_file(Path(folder)/'events.jsonl',64*1024**2)
                if sha(body)!=provenance['manifest_sha256'] or sha(events_raw)!=provenance['events_sha256']:
                    raise ValueError('context_store_legacy_sources_changed')
                identifier=begin(mid,'legacy_archive',sha(body),provenance)
                blob(identifier,'legacy_manifest',body);blob(identifier,'legacy_events',events_raw)
                register(identifier,rows,insert=True)
            for path in captured_contexts:
                body=read_file(path,20*1024**2);frozen=json.loads(body)
                mid=json.loads(base64.b64decode(frozen['manifest_base64'],validate=True))['match_id']
                if mid not in manifests:raise ValueError('context_store_capture_match_missing')
                rows,packets,provenance=load_capture(path,manifests[mid],metric_clock_policy=captured_metric_clock_policy,
                                                    event_clock_policy=captured_event_clock_policy)
                if provenance['capture_sha256']!=sha(body):raise ValueError('context_store_capture_changed')
                identifier=begin(mid,'replay_capture',provenance['context_sha256'],provenance)
                blob(identifier,'frozen_capture',body);register(identifier,rows,insert=True)
                for index,packet in enumerate(packets):
                    raw=json.dumps(packet,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
                    connection.execute('INSERT INTO replay_metric_packets VALUES(?,?,?,?,?,?,?,?,?)',
                        (identifier,index,mid,packet['packet_tick'],packet['game_time_s'],phase(mid,packet['game_time_s']),
                         int(packet['resource_mapping_resolved']),sha(raw),raw))
                    for player in packet['players']:
                        for metric,value in player['metrics'].items():
                            connection.execute('INSERT INTO replay_player_metrics VALUES(?,?,?,?,?,?,?,?,?,?)',
                                (identifier,index,player['logical_player_id'],player['team'],player['team_slot'],
                                 player['resource_index'],player['hero_id'],metric,value,
                                 'reported_value' if value is not None else 'not_recorded'))
            total=connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]
            referenced=connection.execute('SELECT COUNT(*) FROM context_event_refs').fetchone()[0]
            if total!=referenced:raise ValueError('context_store_unreferenced_events')
            connection.executemany('INSERT INTO metadata VALUES(?,?)',[
                ('context_extension_version',EXTENSION),('baseline_database_sha256',baseline_digest),
                ('context_extension_created_at_utc',datetime.now(timezone.utc).isoformat()),
                ('context_source_summaries',json.dumps(summaries))])
        if sha(bounded(database,128*1024**2))!=baseline_digest or any(sha(bounded(p,limit))!=digest for p,limit,digest in files_used):
            raise ValueError('context_store_source_changed')
        integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=connection.execute('PRAGMA foreign_key_check').fetchall()
        if integrity!='ok' or foreign:raise ValueError('context_store_integrity_failed')
        counts={table:connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
            ('matches','wards','events','context_sources','context_source_blobs','context_event_refs','replay_metric_packets','replay_player_metrics')}
        packet_phases=dict(connection.execute('SELECT phase,COUNT(*) FROM replay_metric_packets GROUP BY phase'))
        family_counts=[dict(r) for r in connection.execute('''SELECT s.source_kind,e.family,e.phase,COUNT(*) records,
            COUNT(DISTINCT e.match_id) matches FROM context_event_refs e JOIN context_sources s USING(source_id)
            GROUP BY s.source_kind,e.family,e.phase''')]
    finally:
        source.close()
        if connection is not None:connection.close()
    receipt={'schema_version':EXTENSION,'status':'complete','baseline_database_sha256':baseline_digest,
        'database_sha256':sha(bounded(output/'facts.sqlite',128*1024**2)),'counts':counts,
        'packet_phases':packet_phases,'event_counts_by_source_family_phase':family_counts,
        'integrity_check':integrity,'foreign_key_errors':[],
        'policy':['Sources are exclusive per match; no API/legacy/capture duplicate event pooling.',
            'Base facts schema stays v1; context tables are an explicitly versioned additive extension.',
            'Compressed original bytes retain raw digests; normalized rows retain their source_id.',
            'DEM logical IDs/team slots remain separate from API player_slot.',
            'Pregame/end-boundary events and postgame metrics stay stored and explicitly phased.',
            'Coverage and parser provenance do not establish tactical quality or completeness.']}
    (output/'import-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return receipt
