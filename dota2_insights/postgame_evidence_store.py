"""Keep separate source versions and an explicit single-source projection."""
from __future__ import annotations

import base64
from collections import Counter,defaultdict
import json
from pathlib import Path
import sqlite3
import zlib

from .postgame_store import VERSION,bounded,sha
from .postgame_event_types import event_family
from .postgame_legacy_context import load_archive
from .replay_postgame_context import normalize

EVIDENCE_VERSION='postgame_evidence_registry_v1'
AUX=('player_summaries','player_series','context_coverage','public_ward_logs',
     'replay_metric_packets','replay_player_metrics')
EVENT_COLUMNS=('match_id','event_kind','event_index','time_s','team','actor_hero_numeric',
               'payload','context_sha256','time_basis')


def encoded(row):
    return json.dumps({k:{'bytes_base64':base64.b64encode(v).decode()} if isinstance(v,bytes) else v
        for k,v in row.items()},ensure_ascii=False,allow_nan=False,separators=(',',':'))


def decoded(body):
    return {k:base64.b64decode(v['bytes_base64'],validate=True) if isinstance(v,dict) and set(v)=={'bytes_base64'} else v
        for k,v in json.loads(body).items()}


def phase(stamp,end):
    return 'missing_time' if stamp is None else 'pregame' if stamp<0 else 'in_game' if stamp<end else 'postgame_or_end_boundary'


def import_context(c,target,*,inherit_selection=False):
    """Import previously validated context snapshots without pooling their rows."""
    meta=dict(c.execute('SELECT key,value FROM metadata'))
    if (meta.get('schema_version')!=VERSION or meta.get('context_extension_version')!='postgame_context_extension_v1'
            or c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'):
        raise ValueError('evidence_import_context_version_or_wal')
    imported=[]
    rows=c.execute('SELECT * FROM context_sources ORDER BY match_id').fetchall()
    if len(rows)>256:raise ValueError('evidence_import_source_budget')
    for source in rows:
        s=dict(source);mid=s['match_id'];sid=s['source_id']
        if s['source_kind'] not in ('api_projection','legacy_archive','replay_capture') or sid!=sha((mid+':'+s['source_kind']+':'+s['primary_sha256']).encode()):
            raise ValueError('evidence_import_source_identity')
        m=c.execute('SELECT * FROM matches WHERE match_id=?',(mid,)).fetchone()
        baseline=target.execute('SELECT * FROM matches WHERE match_id=?',(mid,)).fetchone()
        if baseline is None or any(m[k]!=baseline[k] for k in ('replay_sha256','wards_sha256','duration_s','producer_cohort')):
            raise ValueError('evidence_import_different_ward_baseline')
        blobs=c.execute('SELECT * FROM context_source_blobs WHERE source_id=?',(sid,)).fetchall()
        raw_blobs={}
        for b in blobs:
            if b['encoding']!='zlib' or sha(b['content'])!=b['stored_sha256'] or not 0<=b['raw_bytes']<=64*1024**2:
                raise ValueError('evidence_import_blob_metadata')
            decoder=zlib.decompressobj();body=decoder.decompress(b['content'],b['raw_bytes']+1)
            if len(body)!=b['raw_bytes'] or not decoder.eof or decoder.unused_data or sha(body)!=b['raw_sha256']:
                raise ValueError('evidence_import_blob_roundtrip')
            raw_blobs[b['kind']]=body
        if s['source_kind']=='api_projection':
            body=raw_blobs.get('public_api_projection',b'');receipt=json.loads(body)
            if (sha(body)!=s['primary_sha256'] or str(receipt.get('data',{}).get('match_id'))!=mid
                    or receipt.get('schema_version')!=1 or receipt.get('source_url')!='https://api.opendota.com/api/matches/'+mid
                    or receipt.get('projection')!='public_gameplay_fields_no_accounts_names_chat_credentials'):
                raise ValueError('evidence_import_api_binding')
        else:
            if s['source_kind']=='legacy_archive':
                body=raw_blobs.get('legacy_manifest',b'');manifest=json.loads(body)
                if sha(body)!=s['primary_sha256'] or str(manifest.get('parsed_match_id'))!=mid or manifest.get('truncated_at_tick') is not None:
                    raise ValueError('evidence_import_legacy_identity')
                events_body=raw_blobs.get('legacy_events',b'')
                if manifest.get('files',{}).get('events.jsonl',{}).get('sha256')!=sha(events_body):
                    raise ValueError('evidence_import_legacy_events_binding')
            else:
                frozen=json.loads(raw_blobs.get('frozen_capture',b''))
                manifest=json.loads(base64.b64decode(frozen['manifest_base64'],validate=True))
                context_raw=base64.b64decode(frozen['context_base64'],validate=True)
                if sha(context_raw)!=s['primary_sha256'] or manifest.get('complete') is not True:
                    raise ValueError('evidence_import_capture_binding')
            if (str(manifest.get('match_id'))!=mid or manifest.get('source_sha256')!=baseline['replay_sha256']
                    or manifest.get('source_unchanged_stat') is not True or manifest.get('stop_reason')!='replay_eof'
                    or manifest.get('parse_error')):
                raise ValueError('evidence_import_primary_requires_complete_dem_archive')
        events=c.execute('''SELECT e.*,r.family,r.phase FROM events e JOIN context_event_refs r
            USING(match_id,event_kind,event_index) WHERE r.source_id=? ORDER BY e.event_kind,e.event_index''',(sid,)).fetchall()
        if len(events)>100_000:raise ValueError('evidence_import_event_budget')
        existing=target.execute('SELECT * FROM evidence_sources WHERE source_id=?',(sid,)).fetchone()
        if existing:
            if any(existing[k]!=s[k] for k in s):raise ValueError('evidence_import_source_conflict')
            known=target.execute('SELECT '+','.join((*EVENT_COLUMNS,'family','phase'))+' FROM evidence_events WHERE source_id=? ORDER BY event_kind,event_index',(sid,)).fetchall()
            expected_blobs={b['kind']:b['raw_sha256'] for b in blobs}
            actual_blobs={r['kind']:r['raw_sha256'] for r in target.execute('SELECT * FROM evidence_blobs WHERE source_id=?',(sid,))}
            if [tuple(r) for r in events]!=[tuple(r) for r in known] or expected_blobs!=actual_blobs:
                raise ValueError('evidence_import_projection_conflict')
            for table in AUX:
                column='source_id' if table.startswith('replay_') else 'match_id';value=sid if column=='source_id' else mid
                incoming=Counter(encoded(dict(r)) for r in c.execute('SELECT * FROM '+table+' WHERE '+column+'=?',(value,)))
                saved=Counter(r[0] for r in target.execute('SELECT row_json FROM evidence_aux WHERE source_id=? AND table_name=?',(sid,table)))
                if incoming!=saved:raise ValueError('evidence_import_aux_projection_conflict')
            imported.append({'source_id':sid,'match_id':mid,'status':'identical_source_reused'})
            continue
        match_context=json.dumps({k:m[k] for k in ('context_status','context_sha256')})
        target.execute('INSERT INTO evidence_sources VALUES(?,?,?,?,?,?,?,?)',
            (*(s[k] for k in ('source_id','match_id','source_kind','primary_sha256','provenance_json')),
             1,'inherited_validated_context_not_completeness_proof',match_context))
        if inherit_selection:
            target.execute('INSERT INTO evidence_selection VALUES(?,?,?)',(mid,sid,'inherited_existing_primary'))
        for b in blobs:target.execute('INSERT INTO evidence_blobs VALUES(?,?,?,?,?,?,?)',tuple(b))
        for r in events:
            if r['match_id']!=mid or r['phase']!=phase(r['time_s'],baseline['duration_s']) or r['family']!=event_family(dict(r)):
                raise ValueError('evidence_import_event_identity_or_phase')
            target.execute('INSERT INTO evidence_events VALUES('+','.join('?'*12)+')',(sid,*tuple(r)))
        for table in AUX:
            column='source_id' if table.startswith('replay_') else 'match_id';value=sid if column=='source_id' else mid
            for index,r in enumerate(c.execute('SELECT * FROM '+table+' WHERE '+column+'=? ORDER BY rowid',(value,))):
                body=encoded(dict(r));target.execute('INSERT INTO evidence_aux VALUES(?,?,?,?,?)',(sid,table,index,body,sha(body.encode())))
        imported.append({'source_id':sid,'match_id':mid,'status':'new_separately_stored_version'})
    return imported


def build(database,output,*,legacy_archives=(),context_databases=(),selection=None):
    """Clone frozen facts; additions never implicitly replace a selected source."""
    database,output=Path(database).resolve(),Path(output).resolve()
    if output.exists() or len(legacy_archives)>16 or len(context_databases)>16:raise ValueError('evidence_new_output_and_bounded_sources_required')
    original_digest=sha(bounded(database,128*1024**2));files=[]
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    target=None
    try:
        meta=dict(c.execute('SELECT key,value FROM metadata'))
        if (meta.get('schema_version')!=VERSION or meta.get('context_extension_version')!='postgame_context_extension_v1'
                or c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'):
            raise ValueError('evidence_frozen_context_facts_required')
        if meta.get('evidence_registry_version') not in (None,EVIDENCE_VERSION):raise ValueError('evidence_registry_version')
        output.mkdir(parents=True);target=sqlite3.connect(output/'facts.sqlite');target.row_factory=sqlite3.Row
        c.backup(target);target.execute('PRAGMA foreign_keys=ON')
        if 'evidence_registry_version' not in meta:
            target.executescript('''
                CREATE TABLE evidence_sources(source_id TEXT PRIMARY KEY,match_id TEXT NOT NULL,source_kind TEXT NOT NULL,
                    primary_sha256 TEXT NOT NULL,provenance_json TEXT NOT NULL,primary_eligible INTEGER NOT NULL,
                    eligibility_basis TEXT NOT NULL,match_context_json TEXT NOT NULL,
                    UNIQUE(match_id,source_id),FOREIGN KEY(match_id) REFERENCES matches(match_id));
                CREATE TABLE evidence_blobs(source_id TEXT,kind TEXT,raw_sha256 TEXT,raw_bytes INTEGER,
                    encoding TEXT,stored_sha256 TEXT,content BLOB,PRIMARY KEY(source_id,kind),
                    FOREIGN KEY(source_id) REFERENCES evidence_sources(source_id));
                CREATE TABLE evidence_events(source_id TEXT,match_id TEXT,event_kind TEXT,event_index INTEGER,
                    time_s REAL,team INTEGER,actor_hero_numeric INTEGER,payload TEXT,context_sha256 TEXT,time_basis TEXT,
                    family TEXT,phase TEXT,PRIMARY KEY(source_id,event_kind,event_index),
                    FOREIGN KEY(match_id,source_id) REFERENCES evidence_sources(match_id,source_id));
                CREATE TABLE evidence_aux(source_id TEXT,table_name TEXT,row_index INTEGER,row_json TEXT,row_sha256 TEXT,
                    PRIMARY KEY(source_id,table_name,row_index),FOREIGN KEY(source_id) REFERENCES evidence_sources(source_id));
                CREATE TABLE evidence_selection(match_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,reason TEXT NOT NULL,
                    FOREIGN KEY(match_id,source_id) REFERENCES evidence_sources(match_id,source_id));
                CREATE INDEX evidence_events_by_source_time ON evidence_events(source_id,time_s);
            ''')
            import_context(c,target,inherit_selection=True)
            target.execute('INSERT INTO metadata VALUES(?,?)',('evidence_registry_version',EVIDENCE_VERSION))
        manifests={r['match_id']:json.loads(r['content']) for r in target.execute("SELECT * FROM sources WHERE kind='manifest.json'")}
        matches={r['match_id']:dict(r) for r in target.execute('SELECT * FROM matches')}
        additions=[]
        for path in context_databases:
            path=Path(path).resolve();body=bounded(path,128*1024**2);files.append((path,128*1024**2,sha(body)))
            other=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);other.row_factory=sqlite3.Row
            try:
                additions.extend({**row,'input_context_database_sha256':sha(body)} for row in import_context(other,target))
            finally:other.close()
        for folder in legacy_archives:
            folder=Path(folder).resolve();raw=bounded(folder/'manifest.json',1024**2);saved=json.loads(raw)
            mid=str(saved.get('match_id'));ward=manifests.get(mid)
            if not ward:raise ValueError('evidence_archive_match_missing')
            events=bounded(folder/'events.jsonl',64*1024**2);digest=sha(raw)
            files.extend([(folder/'manifest.json',1024**2,digest),(folder/'events.jsonl',64*1024**2,sha(events))])
            complete=saved.get('stop_reason')=='replay_eof'
            if complete:
                rows,provenance=load_archive(folder,ward)
            else:
                parser=saved.get('parser') or {};file_meta=saved.get('files',{}).get('events.jsonl',{})
                if (saved.get('kind')!='offline_replay_archive' or str(saved.get('parsed_match_id'))!=mid
                        or saved.get('source_sha256')!=ward['source_sha256'] or saved.get('source_unchanged_stat') is not True
                        or saved.get('stop_reason') not in ('output_size_limit','requested_game_window_end') or saved.get('parse_error')
                        or saved.get('game_clock')!=ward['game_clock'] or parser.get('version')!=ward['parser_version']
                        or parser.get('upstream_commit')!=ward['parser_commit']
                        or file_meta.get('sha256')!=sha(events) or file_meta.get('bytes')!=len(events)):
                    raise ValueError('evidence_partial_archive_binding')
                rows=[];line_count=0
                for line_count,line in enumerate(events.splitlines(),1):
                    if line_count>250_000 or len(line)>16*1024:raise ValueError('evidence_archive_line_budget')
                    record=json.loads(line)
                    if str(record.get('match_id'))!=mid:raise ValueError('evidence_archive_row_match')
                    row=normalize(record)
                    if row is not None:
                        rows.append(dict(zip(EVENT_COLUMNS,(mid,'legacy_combat',line_count,row['packet_game_time_s'],
                            row['attacker_team'],None,json.dumps(row,allow_nan=False),digest,
                            'same_DEM_packet_clock_partial_archive_event_clock_retained_separately'))))
                        if len(rows)>5000:raise ValueError('evidence_selected_event_budget')
                provenance={'match_id':mid,'manifest_sha256':digest,'events_sha256':sha(events),
                    'replay_sha256':ward['source_sha256'],'raw_event_lines':line_count,
                    'coverage':'partial_archive_not_primary_eligible','stop_reason':saved.get('stop_reason'),
                    'game_clock_equal_to_ward_export':True,'metrics':'not_imported_from_legacy_archive'}
            if len(rows)>5000:raise ValueError('evidence_selected_event_budget')
            sid=sha((mid+':legacy_archive:'+digest).encode())
            existing=target.execute('SELECT * FROM evidence_sources WHERE source_id=?',(sid,)).fetchone()
            if existing:
                known={r['kind']:r['raw_sha256'] for r in target.execute('SELECT * FROM evidence_blobs WHERE source_id=?',(sid,))}
                if known.get('legacy_manifest')!=digest or known.get('legacy_events')!=sha(events):
                    raise ValueError('evidence_existing_source_blob_conflict')
                stored=target.execute('SELECT '+','.join(EVENT_COLUMNS)+' FROM evidence_events WHERE source_id=? ORDER BY event_index',(sid,)).fetchall()
                if [tuple(r[k] for k in EVENT_COLUMNS) for r in sorted(rows,key=lambda r:r['event_index'])]!=[tuple(r) for r in stored]:
                    raise ValueError('evidence_existing_normalization_conflict')
                additions.append({'source_id':sid,'match_id':mid,'status':'identical_source_reused'})
                continue
            mc=json.dumps({'context_status':'legacy_archive_projection','context_sha256':digest})
            target.execute('INSERT INTO evidence_sources VALUES(?,?,?,?,?,?,?,?)',
                (sid,mid,'legacy_archive',digest,json.dumps(provenance,allow_nan=False),int(complete),
                 'validated_same_dem_complete_archive' if complete else 'partial_archive_reference_only',mc))
            for kind,body in (('legacy_manifest',raw),('legacy_events',events)):
                packed=zlib.compress(body,6)
                target.execute('INSERT INTO evidence_blobs VALUES(?,?,?,?,?,?,?)',(sid,kind,sha(body),len(body),'zlib',sha(packed),packed))
            for row in rows:
                target.execute('INSERT INTO evidence_events VALUES('+','.join('?'*12)+')',
                    (sid,*(row[k] for k in EVENT_COLUMNS),event_family(row),phase(row['time_s'],matches[mid]['duration_s'])))
            additions.append({'source_id':sid,'match_id':mid,'status':'new_separately_stored_version',
                'primary_eligible':complete,'normalized_events':len(rows)})
        for mid,sid in (selection or {}).items():
            chosen=target.execute('SELECT * FROM evidence_sources WHERE match_id=? AND source_id=?',(mid,sid)).fetchone()
            if chosen is None or not chosen['primary_eligible']:raise ValueError('evidence_primary_source_missing_or_ineligible')
            target.execute('INSERT INTO evidence_selection VALUES(?,?,?) ON CONFLICT(match_id) DO UPDATE SET source_id=excluded.source_id,reason=excluded.reason',
                (mid,sid,'explicit_source_selection'))
        if target.execute('SELECT COUNT(*) FROM evidence_sources').fetchone()[0]>512:
            raise ValueError('evidence_total_source_budget')
        project(target)
        target.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('evidence_parent_database_sha256',original_digest))
        target.commit()
        if sha(bounded(database,128*1024**2))!=original_digest or any(sha(bounded(p,n))!=h for p,n,h in files):
            raise ValueError('evidence_input_changed')
        integrity=target.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok' or target.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('evidence_integrity')
        counts={t:target.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in
            ('matches','wards','events','context_sources','evidence_sources','evidence_events','evidence_blobs','evidence_aux','evidence_selection')}
        selected=[dict(r) for r in target.execute('SELECT * FROM evidence_selection ORDER BY match_id')]
    finally:
        c.close()
        if target is not None:target.close()
    receipt={'schema_version':EVIDENCE_VERSION,'status':'complete','parent_database_sha256':original_digest,
        'database_sha256':sha(bounded(output/'facts.sqlite',128*1024**2)),'counts':counts,'additions':additions,
        'selection':selected,'integrity_check':integrity,'foreign_key_errors':[],
        'policy':['Additional evidence never implicitly replaces a primary source.',
            'Each match projects one selected source bundle atomically; no event or player namespace pooling.',
            'Partial archives remain reference-only and cannot be selected as primary.',
            'Match cohort/patch/map metadata remains inherited; source selection does not silently revise it.',
            'Same replay/parser source versions are not independent corroboration or tactical truth.']}
    (output/'import-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return receipt


def project(c):
    """Rebuild compatibility tables entirely from the explicit selected bundles."""
    for table in ('context_event_refs','replay_player_metrics','replay_metric_packets','context_source_blobs','context_sources',
                  'events','player_summaries','player_series','context_coverage','public_ward_logs'):
        c.execute('DELETE FROM '+table)
    for selection in c.execute('SELECT * FROM evidence_selection ORDER BY match_id').fetchall():
        sid=selection['source_id'];source=c.execute('SELECT * FROM evidence_sources WHERE source_id=?',(sid,)).fetchone()
        if source is None or not source['primary_eligible']:raise ValueError('evidence_projection_source_ineligible')
        c.execute('INSERT INTO context_sources VALUES(?,?,?,?,?)',tuple(source[k] for k in
            ('source_id','match_id','source_kind','primary_sha256','provenance_json')))
        mc=json.loads(source['match_context_json'])
        c.execute('UPDATE matches SET context_status=?,context_sha256=? WHERE match_id=?',
            (mc['context_status'],mc['context_sha256'],selection['match_id']))
        for row in c.execute('SELECT * FROM evidence_blobs WHERE source_id=?',(sid,)).fetchall():
            c.execute('INSERT INTO context_source_blobs VALUES(?,?,?,?,?,?,?)',tuple(row))
        for row in c.execute('SELECT * FROM evidence_events WHERE source_id=?',(sid,)).fetchall():
            c.execute('INSERT INTO events VALUES('+','.join('?'*9)+')',tuple(row[k] for k in EVENT_COLUMNS))
            c.execute('INSERT INTO context_event_refs VALUES(?,?,?,?,?,?)',tuple(row[k] for k in
                ('match_id','event_kind','event_index','source_id','family','phase')))
        for table in AUX:
            for row in c.execute('SELECT * FROM evidence_aux WHERE source_id=? AND table_name=? ORDER BY row_index',(sid,table)).fetchall():
                if sha(row['row_json'].encode())!=row['row_sha256']:raise ValueError('evidence_aux_digest')
                values=decoded(row['row_json']);columns=[r[1] for r in c.execute('PRAGMA table_info('+table+')')]
                if list(values)!=columns or values.get('source_id',sid)!=sid or values.get('match_id',selection['match_id'])!=selection['match_id']:
                    raise ValueError('evidence_aux_schema_or_identity')
                c.execute('INSERT INTO '+table+' VALUES('+','.join('?'*len(columns))+')',tuple(values.values()))
    if c.execute('SELECT COUNT(*) FROM events').fetchone()[0]!=c.execute('SELECT COUNT(*) FROM context_event_refs').fetchone()[0]:
        raise ValueError('evidence_projection_unreferenced_events')
    summaries=[dict(r) for r in c.execute('SELECT match_id,source_kind,source_id FROM context_sources ORDER BY match_id')]
    c.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('context_source_summaries',json.dumps(summaries)))


def inventory(database):
    """Source-specific coverage and exact signatures, never pooled event totals."""
    database=Path(database).resolve();digest=sha(bounded(database,128*1024**2))
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        if dict(c.execute('SELECT key,value FROM metadata')).get('evidence_registry_version')!=EVIDENCE_VERSION:
            raise ValueError('evidence_inventory_registry_required')
        sources=[];signatures={};signature_refs={}
        for row in c.execute('SELECT * FROM evidence_sources ORDER BY match_id,source_id'):
            s=dict(row);sid=s['source_id'];rows=[dict(r) for r in c.execute('SELECT * FROM evidence_events WHERE source_id=?',(sid,))]
            s['selected']=bool(c.execute('SELECT 1 FROM evidence_selection WHERE source_id=?',(sid,)).fetchone())
            s['event_count']=len(rows);s['in_game_events']=sum(r['phase']=='in_game' for r in rows)
            s['families']=dict(Counter(r['family'] or 'not_selected_family' for r in rows))
            s['recorded_time_range_s']=[min(r['time_s'] for r in rows if r['time_s'] is not None),max(r['time_s'] for r in rows if r['time_s'] is not None)] if any(r['time_s'] is not None for r in rows) else None
            s['raw_blobs']=[{k:r[k] for k in ('kind','raw_sha256','raw_bytes')} for r in c.execute('SELECT * FROM evidence_blobs WHERE source_id=?',(sid,))]
            sources.append(s)
            signatures[sid]=Counter();signature_refs[sid]=defaultdict(list)
            for r in rows:
                p=json.loads(r['payload'])
                signature=json.dumps([r['family'],r['time_s'],r['team'],r['actor_hero_numeric'],p],sort_keys=True,allow_nan=False)
                fingerprint=sha(signature.encode());signatures[sid][fingerprint]+=1
                signature_refs[sid][fingerprint].append({k:r[k] for k in ('event_kind','event_index','family','phase','time_s')})
        pairs=[]
        for i,a in enumerate(sources):
            for b in sources[i+1:]:
                if a['match_id']!=b['match_id']:continue
                ca,cb=signatures[a['source_id']],signatures[b['source_id']]
                pairs.append({'match_id':a['match_id'],'source_a':a['source_id'],'source_b':b['source_id'],
                    'exact_shared_normalized_records':sum((ca&cb).values()),'only_a_records':sum((ca-cb).values()),
                    'only_b_records':sum((cb-ca).values()),
                    'shared_signature_refs':[{'signature_sha256':h,'shared_multiplicity':min(ca[h],cb[h]),
                        'a_refs':signature_refs[a['source_id']][h],'b_refs':signature_refs[b['source_id']][h]}
                        for h in sorted(ca.keys()&cb.keys())],
                    'only_a_signature_refs':[{'signature_sha256':h,'unmatched_multiplicity':v,'candidate_refs':signature_refs[a['source_id']][h]} for h,v in sorted((ca-cb).items())],
                    'only_b_signature_refs':[{'signature_sha256':h,'unmatched_multiplicity':v,'candidate_refs':signature_refs[b['source_id']][h]} for h,v in sorted((cb-ca).items())],
                    'signature_basis':'family_record_time_team_actor_full_normalized_payload_excludes_local_line_index_source_sha_time_basis_label',
                    'meaning':'exact_source_record_overlap_not_accuracy_or_independent_validation_no_fuzzy_clock_merge'})
    finally:c.close()
    if sha(bounded(database,128*1024**2))!=digest:raise ValueError('evidence_inventory_source_changed')
    return {'schema_version':EVIDENCE_VERSION,'database_sha256':digest,'sources':sources,'same_match_pairs':pairs}


def render_inventory(result,output):
    output=Path(output)
    (output/'evidence-inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    payload=json.dumps(result,ensure_ascii=False).replace('<','\\u003c')
    page=r'''<!doctype html><meta charset="utf-8"><title>赛后证据来源与主分析选择</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}select{font:inherit;padding:6px;max-width:95vw;margin:6px}td,th{padding:10px;border-bottom:1px solid #52677b;text-align:left}table{border-collapse:collapse;width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#8cd8c3}.warn{color:#f1cc87}</style>
<h1>同一场比赛的资料：分别保存，明确选用</h1><p id="totals"></p>
<p>每场主分析只读取明确选中的一份来源包，事件、API指标或DEM指标跟随该来源一起切换。新增资料不会自动顶替主来源；截断档案仅供回看，不能选作主分析。玩家槽位和时钟保留各自定义，不自动对齐。</p>
<p class="warn">同一录像、同一解析器的两次导出不是两份独立真值。完全相同的规范化记录只证明导出重合；不同记录可能源于范围、过滤或时间口径，不能当成识别错误。未知补丁/地图仍未知。</p>
<label>比赛<select id="match"></select></label>
<table><thead><tr><th>来源/版本SHA</th><th>本次主分析</th><th>主分析资格/边界</th><th>规范化事件/游戏内</th><th>记录首末时刻（秒）</th></tr></thead><tbody id="sources"></tbody></table>
<p>首末时刻只是已留存规范化事件的最小/最大时间，不是连续覆盖边界；零条事件也不证明没有发生。此页面只浏览冻结结果；如需更换主来源，使用构建命令的 --select 比赛编号=完整来源ID，生成另一份快照后再分析。</p>
<label>来源明细<select id="source"></select></label><pre id="details"></pre>
<h2>同场来源的精确记录重合</h2><p id="pairs"></p><details><summary>重合与差异记录的原始索引</summary><pre id="refs"></pre></details>
<p><a href="evidence-inventory.json">完整来源状态、原始字节SHA与逐条引用</a></p>
<script>const D=PAYLOAD,M=document.getElementById('match'),S=document.getElementById('source'),labels={legacy_archive:'旧DEM导出',api_projection:'公开API投影',replay_capture:'同遍上下文采集'};function el(t,v){const e=document.createElement(t);if(v!==undefined)e.textContent=v;return e}
document.getElementById('totals').textContent=D.sources.length+'份来源 / '+new Set(D.sources.map(s=>s.match_id)).size+'场有上下文；'+D.sources.filter(s=>s.selected).length+'份主来源；'+D.sources.filter(s=>!s.primary_eligible).length+'份仅供回看。所有主来源分别计数，不把同场版本相加。';
for(const mid of [...new Set(D.sources.map(s=>s.match_id))]){const o=el('option',mid);o.value=mid;M.append(o)}
function details(){const s=D.sources.find(s=>s.source_id===S.value);document.getElementById('details').textContent=JSON.stringify({source_id:s.source_id,source_kind:s.source_kind,primary_eligible:!!s.primary_eligible,eligibility_basis:s.eligibility_basis,selected:s.selected,families:s.families,provenance:JSON.parse(s.provenance_json),raw_blobs:s.raw_blobs},null,2)}
function draw(){const rows=D.sources.filter(s=>s.match_id===M.value),body=document.getElementById('sources');body.replaceChildren();S.replaceChildren();for(const s of rows){const row=el('tr');for(const v of [labels[s.source_kind]+' / '+s.source_id.slice(0,12),s.selected?'使用中':'未选用',s.primary_eligible?'可选；不代表事件齐全':'截断/片段，仅回看',s.event_count+' / '+s.in_game_events,s.recorded_time_range_s?s.recorded_time_range_s.map(t=>t.toFixed(3)).join(' ～ '):'没有留存事件'])row.append(el('td',v));body.append(row);const o=el('option',labels[s.source_kind]+' / '+s.source_id.slice(0,12)+(s.selected?'（主来源）':'（未选用）'));o.value=s.source_id;S.append(o)}const pairs=D.same_match_pairs.filter(p=>p.match_id===M.value);document.getElementById('pairs').textContent=pairs.length?pairs.map(p=>p.source_a.slice(0,12)+' 与 '+p.source_b.slice(0,12)+'：完全重合'+p.exact_shared_normalized_records+'条；仅前者'+p.only_a_records+'条；仅后者'+p.only_b_records+'条。').join('\n'):'当前仅一份上下文，没有同场版本可比较。';document.getElementById('refs').textContent=JSON.stringify(pairs,null,2);details()}
M.addEventListener('change',draw);S.addEventListener('change',details);draw();</script>'''
    (output/'report.html').write_text(page.replace('PAYLOAD',payload),encoding='utf-8',newline='\n')
