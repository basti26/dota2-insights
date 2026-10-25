"""Additive replay metadata evidence; explicit selection leaves facts intact."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import html
import json
from pathlib import Path
import re
import shutil
import sqlite3

from .postgame_store import bounded,sha,number,VERSION as FACTS
from .replay_metadata_probe import (SCHEMA as PROBE,MAX_SOURCE,PREFIX_BYTES,FRAME_BYTES,
    EXPANDED_BYTES,FOOTER_BYTES,MAX_FRAMES,MAX_INNER,SECONDS)

VERSION='postgame_replay_metadata_evidence_v1'
FIELDS=('game_build','game_mode')
HEX=re.compile('[a-f0-9]{64}')
VERIFIED=('verified_bounded_metadata','verified_partial_metadata')
ALLOWED={'schema_version','match_id','expected_source_sha256','observed_at_utc','status','game_build','game_mode',
    'source_sha256','source_sha256_after_read','source_stat_unchanged','footer','prefix','limits','scope',
    'raw_names_paths_accounts_chat_or_server_strings_exported','gem_version','source_bytes','elapsed_s',
    'probe_source_sha256','pilot_role','error_code'}


def validate_receipt(raw,match):
    value=json.loads(raw)
    if (not isinstance(value,dict) or not set(value)<=ALLOWED or value.get('schema_version')!=PROBE
            or value.get('match_id')!=match['match_id'] or value.get('expected_source_sha256')!=match['replay_sha256']
            or value.get('raw_names_paths_accounts_chat_or_server_strings_exported') is not False
            or not HEX.fullmatch(str(value.get('probe_source_sha256','')))):
        raise ValueError('metadata_receipt_identity_or_whitelist')
    if value.get('pilot_role') not in (None,'known_complete_capture_control','first_hash_ranked_unknown_build',
                                      'known_source_mismatch_control','fixed_unknown_build_batch2',
                                      'fixed_unknown_build_expansion_20261022'):
        raise ValueError('metadata_receipt_role')
    stamp=datetime.fromisoformat(value['observed_at_utc'])
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds()!=0:raise ValueError('metadata_receipt_UTC_required')
    limits={'source_bytes':MAX_SOURCE,'prefix_bytes':PREFIX_BYTES,'encoded_frame_bytes':FRAME_BYTES,
        'expanded_frame_bytes':EXPANDED_BYTES,'footer_bytes':FOOTER_BYTES,'outer_frames':MAX_FRAMES,'inner_messages':MAX_INNER,'wall_seconds':SECONDS}
    if value.get('limits')!=limits or value.get('scope')!='bounded_prefix_and_indexed_footer_only_full_source_hash_not_entity_parse_or_replay_integrity_proof':
        raise ValueError('metadata_receipt_scope_or_limits')
    if value.get('status') not in (*VERIFIED,'rejected_source_sha256_mismatch','rejected_metadata_probe'):
        raise ValueError('metadata_receipt_status')
    if (type(value.get('source_stat_unchanged')) is not bool or value.get('gem_version') not in (None,'0.13.0')
            or not number(value.get('elapsed_s')) or value['elapsed_s']<0
            or value.get('source_bytes') is not None and (type(value['source_bytes']) is not int or not 0<=value['source_bytes']<=MAX_SOURCE)):
        raise ValueError('metadata_receipt_scalar_fields')
    for field in ('source_sha256','source_sha256_after_read'):
        if value.get(field) is not None and not HEX.fullmatch(value[field]):raise ValueError('metadata_receipt_source_hash')
    for field in FIELDS:
        if value.get(field) is not None and (type(value[field]) is not int or value[field]<=0):raise ValueError('metadata_receipt_positive_fields')
    if value.get('error_code') is not None and not re.fullmatch('[a-zA-Z_][a-zA-Z_0-9]{0,100}',value['error_code']):
        raise ValueError('metadata_receipt_error_whitelist')
    footer=value.get('footer')
    if footer is not None:
        if (set(footer)!={'offset','tick_raw','encoded_bytes','decoded_payload_sha256','match_id','game_mode'}
                or footer['match_id']!=match['match_id'] or not HEX.fullmatch(footer['decoded_payload_sha256'])
                or type(footer['offset']) is not int or not 16<=footer['offset']<value['source_bytes']
                or type(footer['tick_raw']) is not int or not 0<=footer['tick_raw']<=0xffffffff
                or type(footer['encoded_bytes']) is not int or not 0<footer['encoded_bytes']<=FOOTER_BYTES
                or footer['game_mode'] is not None and (type(footer['game_mode']) is not int or footer['game_mode']<=0)):
            raise ValueError('metadata_receipt_footer_fields')
    prefix=value.get('prefix');builds=set()
    if prefix is not None:
        if (set(prefix)!={'outer_frames_examined','packet_envelopes_decoded','inner_messages_examined','prefix_end_offset','stop_reason','server_info_records'}
                or any(type(prefix[k]) is not int for k in ('outer_frames_examined','packet_envelopes_decoded','inner_messages_examined','prefix_end_offset'))
                or not 0<=prefix['outer_frames_examined']<=MAX_FRAMES
                or not 0<=prefix['packet_envelopes_decoded']<=prefix['outer_frames_examined']
                or not 0<=prefix['inner_messages_examined']<=MAX_INNER
                or not 16<=prefix['prefix_end_offset']<=PREFIX_BYTES
                or prefix['stop_reason'] not in ('outer_frame_limit','prefix_byte_limit','encoded_frame_limit','first_build_packet_completed')
                or len(prefix['server_info_records'])>8):raise ValueError('metadata_receipt_prefix_bounds')
        for row in prefix['server_info_records']:
            build=row.get('game_build')
            if (set(row)!={'outer_offset','outer_tick_raw','inner_type_id','payload_sha256','game_build','build_component'}
                    or row['inner_type_id']!=40 or not HEX.fullmatch(row['payload_sha256'])
                    or type(row['outer_offset']) is not int or type(row['outer_tick_raw']) is not int or not 0<=row['outer_tick_raw']<=0xffffffff
                    or not 16<=row['outer_offset']<prefix['prefix_end_offset']
                    or row['build_component']!=('dota_v'+str(build) if build is not None else None)
                    or build is not None and (type(build) is not int or build<=0)):
                raise ValueError('metadata_receipt_server_info_fields')
            if build is not None:builds.add(build)
    if value['status'] in VERIFIED:
        if (value.get('gem_version')!='0.13.0' or value.get('source_sha256')!=match['replay_sha256']
                or value.get('source_sha256_after_read')!=match['replay_sha256'] or value.get('source_stat_unchanged') is not True
                or not 16<=value.get('source_bytes',0)<=MAX_SOURCE or footer is None or prefix is None
                or len(builds)>1 or value['game_build']!=(next(iter(builds)) if builds else None)
                or value['game_mode']!=footer['game_mode'] or not any(value[f] is not None for f in FIELDS)
                or (value['status']=='verified_bounded_metadata')!=(all(value[f] is not None for f in FIELDS))):
            raise ValueError('metadata_receipt_verified_binding')
    elif any(value.get(f) is not None for f in FIELDS):
        raise ValueError('metadata_receipt_rejected_must_not_supply_fields')
    return value


def resolved_metadata(c):
    """Per-field provenance, selected probes only; original context JSON stays raw."""
    result={r['match_id']:{'game_build':None,'game_mode':None,'field_sources':{f:[] for f in FIELDS}} for r in c.execute('SELECT match_id FROM matches')}
    for row in c.execute('''SELECT m.match_id,m.mode_key,m.context_sha256,s.source_kind,s.primary_sha256
                            FROM matches m LEFT JOIN context_sources s USING(match_id)'''):
        if row['mode_key']!='not_recorded':
            if not re.fullmatch('[0-9]+',row['mode_key']) or int(row['mode_key'])<=0 or not HEX.fullmatch(str(row['context_sha256'])):
                raise ValueError('metadata_original_mode_field_invalid')
            # The typed old mode key can outlive an event-source switch. Use
            # its API provenance only while that exact projection is selected.
            if row['source_kind']!='api_projection' or row['primary_sha256']!=row['context_sha256']:
                continue
            target=result[row['match_id']];target['game_mode']=int(row['mode_key'])
            target['field_sources']['game_mode'].append({'kind':'original_public_projection','source_id':row['context_sha256']})
    for row in c.execute('SELECT match_id,source_id,provenance_json FROM context_sources'):
        md=json.loads(row['provenance_json']).get('replay_metadata',{})
        for field in FIELDS:
            if md.get(field) is not None:
                if type(md[field]) is not int or md[field]<=0:raise ValueError('metadata_context_field_invalid')
                if result[row['match_id']][field] is not None and result[row['match_id']][field]!=md[field]:raise ValueError('metadata_original_sources_conflict')
                result[row['match_id']][field]=md[field]
                result[row['match_id']]['field_sources'][field].append({'kind':'context_source','source_id':row['source_id']})
    extension=dict(c.execute('SELECT key,value FROM metadata')).get('replay_metadata_evidence_version')
    if extension is not None:
        if extension!=VERSION:raise ValueError('unsupported_replay_metadata_evidence_version')
        for row in c.execute('''SELECT p.*,s.match_id selected_match_id FROM replay_metadata_selection s JOIN replay_metadata_probes p USING(probe_id)'''):
            if row['selected_match_id']!=row['match_id']:raise ValueError('metadata_selection_match_mismatch')
            match=dict(c.execute('SELECT * FROM matches WHERE match_id=?',(row['match_id'],)).fetchone())
            if sha(row['receipt_bytes'])!=row['probe_id']:raise ValueError('metadata_selected_receipt_sha')
            value=validate_receipt(row['receipt_bytes'],match)
            if value['status'] not in VERIFIED:raise ValueError('metadata_rejected_probe_selected')
            if any(row[f]!=value[f] for f in (*FIELDS,'status')):raise ValueError('metadata_stored_field_mismatch')
            target=result[row['match_id']]
            for field in FIELDS:
                if value[field] is None:continue
                if target[field] is not None and target[field]!=value[field]:raise ValueError('metadata_selected_field_conflict')
                target[field]=value[field]
                target['field_sources'][field].append({'kind':'bounded_replay_probe','source_id':row['probe_id']})
    return result


def context_sources(c):
    resolved=resolved_metadata(c)
    return {r['match_id']:{**dict(r),'resolved_replay_metadata':{f:resolved[r['match_id']][f] for f in FIELDS},
                          'metadata_field_sources':resolved[r['match_id']]['field_sources']}
            for r in c.execute('SELECT * FROM context_sources')}


def build(database,output,probe_paths,selection,*,append=False):
    database,output=Path(database).resolve(),Path(output).resolve()
    if output.exists() or not 1<=len(probe_paths)<=256:raise ValueError('metadata_new_output_and_bounded_probes')
    digest=sha(bounded(database,128*1024**2));old=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);old.row_factory=sqlite3.Row
    try:
        meta=dict(old.execute('SELECT key,value FROM metadata'))
        extension=meta.get('replay_metadata_evidence_version')
        if (meta.get('schema_version')!=FACTS or (extension!=VERSION if append else extension is not None)
                or old.execute('PRAGMA journal_mode').fetchone()[0]=='wal'):
            raise ValueError('metadata_base_snapshot_required')
        matches={r['match_id']:dict(r) for r in old.execute('SELECT * FROM matches')}
        previous=resolved_metadata(old)
        if append and old.execute('SELECT COUNT(*) FROM replay_metadata_probes').fetchone()[0]>256:
            raise ValueError('metadata_existing_receipt_budget')
        existing_probes={r['probe_id']:dict(r) for r in old.execute('SELECT * FROM replay_metadata_probes')} if append else {}
        existing_selection=dict(old.execute('SELECT match_id,probe_id FROM replay_metadata_selection')) if append else {}
        for sid,row in existing_probes.items():
            value=validate_receipt(row['receipt_bytes'],matches[row['match_id']])
            if (sha(row['receipt_bytes'])!=sid or row['status']!=value['status']
                    or row['game_build']!=value['game_build'] or row['game_mode']!=value['game_mode']):
                raise ValueError('metadata_existing_probe_binding')
    finally:old.close()
    loaded={}
    for path in probe_paths:
        raw=bounded(path,128*1024);value=json.loads(raw);mid=value.get('match_id')
        if mid not in matches:raise ValueError('metadata_probe_missing_match')
        value=validate_receipt(raw,matches[mid]);sid=sha(raw)
        if sid in loaded:raise ValueError('metadata_duplicate_receipt')
        conflict=any(previous[mid][f] is not None and value[f] is not None and previous[mid][f]!=value[f] for f in FIELDS)
        loaded[sid]=(raw,value,conflict)
    if len(set(existing_probes)|set(loaded))>256:raise ValueError('metadata_total_receipt_budget')
    for mid,sid in selection.items():
        if sid not in loaded or loaded[sid][1]['match_id']!=mid or loaded[sid][1]['status'] not in VERIFIED or loaded[sid][2]:
            raise ValueError('metadata_explicit_selection_requires_verified_nonconflicting_probe')
        if mid in existing_selection and existing_selection[mid]!=sid:
            raise ValueError('metadata_append_must_preserve_existing_selection')
    output.mkdir(parents=True);target=output/'facts.sqlite';shutil.copyfile(database,target)
    c=sqlite3.connect(target);c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA foreign_keys=ON')
        if not append:c.executescript('''CREATE TABLE replay_metadata_probes(probe_id TEXT PRIMARY KEY,match_id TEXT NOT NULL REFERENCES matches(match_id),
            status TEXT NOT NULL,game_build INTEGER,game_mode INTEGER,conflicts_with_context INTEGER NOT NULL,receipt_bytes BLOB NOT NULL);
            CREATE TABLE replay_metadata_selection(match_id TEXT PRIMARY KEY REFERENCES matches(match_id),probe_id TEXT NOT NULL UNIQUE REFERENCES replay_metadata_probes(probe_id));''')
        for sid,(raw,value,conflict) in loaded.items():
            if sid in existing_probes:
                if existing_probes[sid]['receipt_bytes']!=raw:
                    raise ValueError('metadata_existing_probe_bytes_changed')
                continue
            c.execute('INSERT INTO replay_metadata_probes VALUES(?,?,?,?,?,?,?)',(sid,value['match_id'],value['status'],value['game_build'],value['game_mode'],int(conflict),raw))
        for mid,sid in sorted(selection.items()):
            if mid not in existing_selection:c.execute('INSERT INTO replay_metadata_selection VALUES(?,?)',(mid,sid))
        if append:
            c.execute("UPDATE metadata SET value=? WHERE key='replay_metadata_parent_database_sha256'",(digest,))
        else:c.executemany('INSERT INTO metadata VALUES(?,?)',[('replay_metadata_evidence_version',VERSION),('replay_metadata_parent_database_sha256',digest)])
        resolved_metadata(c);c.commit()
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('metadata_database_integrity')
    finally:c.close()
    if sha(bounded(database,128*1024**2))!=digest:raise ValueError('metadata_parent_changed')
    receipt={'status':'complete','parent_database_sha256':digest,'database_sha256':sha(bounded(target,128*1024**2)),
        'probe_count':len(loaded),'selected_count':len(selection),'probe_statuses':dict(Counter(v[1]['status'] for v in loaded.values())),
        'mode':'append_preserving_existing_metadata' if append else 'initial_metadata_extension',
        'previous_probe_count':len(existing_probes),'previous_selection_count':len(existing_selection),
        'scope':'metadata_only_original_context_events_metrics_wards_not_rewritten'}
    (output/'run-summary.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8',newline='\n')
    return receipt


def inventory(database):
    database=Path(database).resolve();c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        resolved=resolved_metadata(c);meta=dict(c.execute('SELECT key,value FROM metadata'));probes=[]
        coverage={}
        for row in c.execute('SELECT match_id,source_group FROM matches ORDER BY match_id'):
            group=coverage.setdefault(row['source_group'],{'matches':0,'known_build':0,'known_mode':0})
            group['matches']+=1
            group['known_build']+=int(resolved[row['match_id']]['game_build'] is not None)
            group['known_mode']+=int(resolved[row['match_id']]['game_mode'] is not None)
        if meta.get('replay_metadata_evidence_version'):
            for row in c.execute('SELECT p.*,s.match_id selected_mid FROM replay_metadata_probes p LEFT JOIN replay_metadata_selection s USING(probe_id) ORDER BY p.match_id,p.probe_id'):
                value=json.loads(row['receipt_bytes']);probes.append({'probe_id':row['probe_id'],'match_id':row['match_id'],'status':row['status'],
                    'game_build':row['game_build'],'game_mode':row['game_mode'],'conflicts_with_context':bool(row['conflicts_with_context']),
                    'selected':row['selected_mid'] is not None,'receipt':value})
    finally:c.close()
    return {'schema_version':VERSION,'database_sha256':sha(bounded(database,128*1024**2)),
        'resolved_matches':resolved,'probes':probes,'coverage_by_source_group':coverage,
        'selected_probes':sum(r['selected'] for r in probes),
        'matches_with_build':sum(r['game_build'] is not None for r in resolved.values()),
        'matches_with_mode':sum(r['game_mode'] is not None for r in resolved.values())}


def render(result,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'metadata-evidence.json').write_text(json.dumps(result,indent=2),encoding='utf-8',newline='\n')
    states={'verified_bounded_metadata':'有限读取通过','verified_partial_metadata':'部分字段通过','rejected_source_sha256_mismatch':'录像指纹不符','rejected_metadata_probe':'读取拒收'}
    rows=''.join('<tr>'+''.join('<td>'+html.escape('未记录' if v is None else str(v))+'</td>' for v in (r['match_id'],states[r['status']],r['game_build'],r['game_mode'],
        '已选用' if r['selected'] else '冲突待核对' if r['conflicts_with_context'] else '未选用',r['probe_id'][:12]))+'</tr>' for r in result['probes'])
    coverage_rows=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (name,row['matches'],row['known_build'],row['known_mode']))+'</tr>' for name,row in sorted(result['coverage_by_source_group'].items()))
    mismatch_rows=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (r['match_id'],r['receipt']['expected_source_sha256'],r['receipt'].get('source_sha256'),r['receipt'].get('source_bytes')))+'</tr>' for r in result['probes'] if r['status']=='rejected_source_sha256_mismatch')
    payload=json.dumps(result,ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><meta charset="utf-8"><title>录像元数据来源与选择</title><style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #52677b}td{overflow-wrap:anywhere;max-width:360px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#8cd8c3}</style><h1>录像构建号与模式：原来源、额外读取和明确选用</h1><p>只补元数据，不改眼位、事件、经济或原上下文字段。有限头部/索引尾部读取不代表整局完整；每份已选证据必须与原录像SHA相符并前后复核。构建号不冒充补丁名或地图版本。</p><p>PROBES份探测证据，SELECTED份已选用；BUILDS场有明确构建，MODES场有明确模式。未选/被拒数据不填入未知字段。原来源与新探测若冲突，拒绝选用，等待核对。</p><h2>历史比赛元数据覆盖</h2><table><tr><th>来源群体</th><th>比赛数</th><th>构建已知</th><th>模式已知</th></tr>COVERAGE</table><h2>读取与选择</h2><table><tr><th>比赛</th><th>状态</th><th>构建</th><th>模式</th><th>当前选择</th><th>原回执SHA</th></tr>ROWS</table><h2>当前录像与归档指纹不同</h2><p>以下当前文件不能与原归档来源拼接。指纹不符本身不证明录像损坏；原先完整归档的眼位记录仍保留，新读取字段不参与统计。完整SHA便于后续恢复来源。</p><table><tr><th>比赛</th><th>归档预期SHA</th><th>当前文件SHA</th><th>当前字节数</th></tr>MISMATCH</table><details><summary>全部原回执、字段来源与当前值</summary><pre id="raw"></pre></details><p><a href="metadata-evidence.json">完整元数据证据</a></p><script>document.getElementById('raw').textContent=JSON.stringify(PAYLOAD,null,2)</script>'''
    page=page.replace('PROBES',str(len(result['probes']))).replace('SELECTED',str(result['selected_probes'])).replace('BUILDS',str(result['matches_with_build'])).replace('MODES',str(result['matches_with_mode'])).replace('ROWS',rows).replace('COVERAGE',coverage_rows).replace('MISMATCH',mismatch_rows or '<tr><td colspan=4>无指纹不符回执</td></tr>').replace('PAYLOAD',payload)
    (output/'report.html').write_text(page,encoding='utf-8',newline='\n')
