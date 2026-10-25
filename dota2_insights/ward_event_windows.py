"""Traceable offline placement windows around reported match events."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import base64
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sqlite3

from .postgame_store import VERSION, bounded, sha
from .ward_patterns import CELL, COORDINATES
from .postgame_event_types import event_family

VERSION_WINDOWS = 'ward_event_windows_v1'


def summarize(rows):
    return [{'team': team, 'ward_type': kind,
             'placement_count': sum(r['team'] == team and r['ward_type'] == kind for r in rows),
             'placement_cells': sorted({(math.floor(r['x'] / CELL), math.floor(r['y'] / CELL))
                                      for r in rows if r['team'] == team and r['ward_type'] == kind})}
            for team in (2, 3) for kind in ('observer', 'sentry')]


def analyze(database, output, legacy_archives=(),captured_contexts=()):
    database, output = Path(database).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('ward_event_windows_new_output_required')
    digest = sha(bounded(database, 128 * 1024**2))
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        meta = dict(connection.execute('SELECT key,value FROM metadata'))
        if meta.get('schema_version') != VERSION:
            raise ValueError('ward_event_windows_schema')
        matches = {r['match_id']: dict(r) for r in connection.execute('SELECT * FROM matches')}
        raw_wards = [dict(r) for r in connection.execute('SELECT * FROM wards ORDER BY match_id,placed_s,ward_index')]
        raw_events = [dict(r) for r in connection.execute('SELECT * FROM events ORDER BY match_id,time_s,event_kind,event_index')]
        series = [dict(r) for r in connection.execute('SELECT * FROM player_series ORDER BY match_id,player_slot,metric,time_s,sample_index')]
        coverage = [dict(r) for r in connection.execute('SELECT * FROM context_coverage')]
        player_summaries = [dict(r) for r in connection.execute('SELECT * FROM player_summaries')]
        ward_manifests={r['match_id']:json.loads(r['content']) for r in connection.execute("SELECT match_id,content FROM sources WHERE kind='manifest.json'")}
        extension_sources=[];extension_packets=[]
        if meta.get('context_extension_version'):
            if meta['context_extension_version']!='postgame_context_extension_v1':
                raise ValueError('ward_event_windows_context_extension_version')
            extension_sources=[dict(r) for r in connection.execute('SELECT * FROM context_sources')]
            extension_packets=[dict(r) for r in connection.execute('SELECT * FROM replay_metric_packets ORDER BY source_id,packet_index')]
    finally:
        connection.close()
    if len(matches) > 256 or len(raw_wards) > 100_000 or len(raw_events) > 100_000 or len(series) > 1_000_000:
        raise ValueError('ward_event_windows_row_budget')
    if len(legacy_archives)>16:raise ValueError('legacy_context_archive_budget')
    from .postgame_legacy_context import load_archive
    if len(extension_sources)>256 or len(extension_packets)>100_000:raise ValueError('ward_event_windows_extension_budget')
    legacy_sources=[];captured_sources=[];captured_metrics=defaultdict(list);loaded=set()
    for row in extension_sources:
        provenance=json.loads(row['provenance_json'])
        if row['source_kind']=='legacy_archive':legacy_sources.append(provenance);loaded.add(row['match_id'])
        elif row['source_kind']=='replay_capture':captured_sources.append(provenance);loaded.add(row['match_id'])
        elif row['source_kind']!='api_projection':raise ValueError('ward_event_windows_extension_source_kind')
    for row in extension_packets:
        if sha(row['packet_json'])!=row['packet_sha256']:raise ValueError('ward_event_windows_packet_digest')
        captured_metrics[row['match_id']].append(json.loads(row['packet_json']))
    for folder in legacy_archives:
        raw=bounded(Path(folder)/'manifest.json',1024**2);manifest=json.loads(raw);identifier=str(manifest.get('match_id'))
        if identifier not in matches or identifier in loaded or any(r['match_id']==identifier for r in raw_events):
            raise ValueError('legacy_context_missing_match_duplicate_or_mixed_source')
        records,provenance=load_archive(folder,ward_manifests[identifier])
        raw_events.extend(records);loaded.add(identifier);legacy_sources.append(provenance)
    if len(captured_contexts)>16:raise ValueError('captured_context_budget')
    from .postgame_replay_context import load_capture
    for path in captured_contexts:
        frozen=json.loads(bounded(path,20*1024**2))
        identifier=json.loads(base64.b64decode(frozen['manifest_base64'],validate=True))['match_id']
        if identifier not in matches or identifier in loaded or any(r['match_id']==identifier for r in raw_events):
            raise ValueError('captured_context_missing_match_duplicate_or_mixed_source')
        records,packets,provenance=load_capture(path,ward_manifests[identifier])
        raw_events.extend(records);loaded.add(identifier);captured_sources.append(provenance);captured_metrics[identifier]=packets
    wards = defaultdict(list)
    exclusions = Counter()
    for row in raw_wards:
        if row['placement_status'] != 'in_game':
            exclusions[row['placement_status']] += 1
        elif row['coordinate_basis'] != COORDINATES:
            exclusions['unsupported_coordinate_basis'] += 1
        else:
            wards[row['match_id']].append({k: row[k] for k in
                ('ward_index','ward_type','team','placed_s','x','y','ended_s','end_kind','lifetime_status')})
    ward_times = {mid: [r['placed_s'] for r in rows] for mid, rows in wards.items()}
    grouped_series = defaultdict(list)
    for row in series:
        grouped_series[(row['match_id'], row['player_slot'], row['metric'])].append(row)
    series_times = {key: [r['time_s'] for r in rows] for key, rows in grouped_series.items()}
    slots = (*range(5), *range(128, 133))

    def metric_checkpoint(identifier, stamp):
        if not any(k[0]==identifier for k in grouped_series):
            return {'requested_time_s':stamp,'samples':[],'coverage_status':'source_series_not_recorded'}
        values = []
        for metric in ('gold_t', 'xp_t', 'lh_t', 'dn_t'):
            for slot in slots:
                key = (identifier, slot, metric)
                position = bisect_right(series_times.get(key, []), stamp) - 1
                row = grouped_series[key][position] if position >= 0 else None
                valid = (row is not None and row['status'] == 'reported_value'
                         and row['value'] is not None and 0 <= stamp - row['time_s'] <= 90)
                values.append({'player_slot': slot, 'metric': metric,
                    'status': 'reported_recent_sample' if valid else 'no_recent_reported_sample',
                    'value': row['value'] if valid else None,
                    'sample_time_s': row['time_s'] if row else None,
                    'sample_age_s': stamp - row['time_s'] if row else None,
                    'source_sample_index': row['sample_index'] if row else None})
        return {'requested_time_s': stamp, 'samples': values,'coverage_status':'source_series_reported_per_field_status_retained',
                'basis': 'last_explicit_source_timestamp_at_or_before_request_max_age90s_no_interpolation',
                'gold_semantics': 'source_gold_t_not_renamed_net_worth'}

    selected, event_exclusions = [], Counter()
    for event in raw_events:
        family = event_family(event)
        if family is None:
            event_exclusions['not_selected_event_family'] += 1
            continue
        identifier = event['match_id']
        if event['time_s'] is None or not 0 <= event['time_s'] < matches[identifier]['duration_s']:
            event_exclusions['pregame_or_end_boundary'] += 1
            continue
        selected.append({**event, 'family': family})
    if len(selected) > 10_000:
        raise ValueError('ward_event_windows_selected_event_budget')
    by_match_events = defaultdict(list)
    for event in selected:
        by_match_events[event['match_id']].append(event)
    details, primary_comparisons = [], []
    for event in selected:
        identifier, stamp = event['match_id'], event['time_s']
        match = matches[identifier]
        rows, times = wards[identifier], ward_times.get(identifier, [])
        payload = json.loads(event['payload'])
        building_key = str(payload.get('key',payload.get('target','')))
        # Building owner comes only from its entity key. It is deliberately
        # separate from the killer: a denied building does not prove an enemy kill.
        owner = (2 if building_key.startswith('npc_dota_goodguys_') else
                 3 if building_key.startswith('npc_dota_badguys_') else None)
        item = {k: event[k] for k in ('match_id','event_kind','event_index','time_s',
            'team','actor_hero_numeric','context_sha256','time_basis','family')}
        item.update(payload=payload, building_owner_from_entity_key=owner,
            acting_team_inferred=False,
            nearby_event_refs=[{'event_kind': e['event_kind'], 'event_index': e['event_index'],
                'time_s': e['time_s'], 'family': e['family']} for e in by_match_events[identifier]
                if abs(e['time_s'] - stamp) <= 180 and
                (e['event_kind'],e['event_index']) != (event['event_kind'],event['event_index'])],
            metric_checkpoints=[metric_checkpoint(identifier, t) for t in (max(0,stamp-180),stamp,min(match['duration_s'],stamp+180))],
            windows=[])
        if identifier in captured_metrics:
            checkpoints=[]
            for stamp_at in (max(0,stamp-180),stamp,min(match['duration_s'],stamp+180)):
                eligible=[p for p in captured_metrics[identifier] if 0<=p['game_time_s']<match['duration_s'] and p['game_time_s']<=stamp_at and stamp_at-p['game_time_s']<=90]
                packet=max(eligible,key=lambda p:(p['game_time_s'],p['packet_tick'])) if eligible else None
                checkpoints.append({'requested_time_s':stamp_at,'packet':packet,
                    'slot_basis':'DEM_logical_player_id_and_team_slot_not_merged_with_API_player_slot',
                    'status':'reported_recent_packet_semantics_pending' if packet else 'no_recent_packet'})
            item['replay_metric_checkpoints']=checkpoints
        for span in (60, 180, 300):
            for shift in (-2, 0, 2):
                center = stamp + shift
                left, right = center - span, center + span
                pre = rows[bisect_left(times, max(0,left)):bisect_left(times, center)] if center >= 0 else []
                post = rows[bisect_right(times, center):bisect_right(times, min(match['duration_s'],right))] if center < match['duration_s'] else []
                equal = rows[bisect_left(times,center):bisect_right(times,center)]
                paired = left >= 0 and right <= match['duration_s']
                pre_stats, post_stats = summarize(pre), summarize(post)
                counts = []
                for before, after in zip(pre_stats, post_stats):
                    old, new = set(map(tuple,before['placement_cells'])), set(map(tuple,after['placement_cells']))
                    counts.append({'team': before['team'], 'ward_type': before['ward_type'],
                        'observed_before_count': before['placement_count'],
                        'observed_after_count': after['placement_count'],
                        'count_difference_if_both_full': after['placement_count']-before['placement_count'] if paired else None,
                        'before_placement_cells': sorted(old), 'after_placement_cells': sorted(new),
                        'only_in_after_placement_cells': sorted(new-old),
                        'only_in_before_placement_cells': sorted(old-new)})
                window = {'span_each_side_s': span, 'center_shift_s': shift,
                    'center_s': center, 'before_full': left >= 0 and center <= match['duration_s'],
                    'after_full': center >= 0 and right <= match['duration_s'],
                    'both_full': paired,
                    'before_refs': [r['ward_index'] for r in pre],
                    'after_refs': [r['ward_index'] for r in post],
                    'equal_center_refs_excluded_from_before_after': [r['ward_index'] for r in equal],
                    'counts': counts}
                item['windows'].append(window)
                if span == 180 and shift == 0:
                    primary_comparisons.append({'match_id': identifier, 'family': event['family'],
                        'event_kind': event['event_kind'], 'event_index': event['event_index'],
                        'time_s': stamp, 'both_full': paired, 'counts': counts})
        details.append(item)
    event_counts = Counter(e['match_id'] for e in selected)
    match_coverage = []
    for match in matches.values():
        entry = {k: match[k] for k in ('match_id','source_group','producer_cohort','patch_key',
            'map_key','mode_key','duration_s','context_status','context_sha256')}
        entry.update(selected_events=event_counts[match['match_id']],
            objective_coverage=[r['status'] for r in coverage if r['match_id']==match['match_id'] and r['field']=='objectives'],
            in_game_ward_records=len(wards[match['match_id']]))
        kill_coverage = []
        for player in player_summaries:
            if player['match_id'] != match['match_id']:
                continue
            slot = player['player_slot']
            statuses = [r['status'] for r in coverage if r['match_id']==match['match_id']
                        and r['field']=='kills_log:'+str(slot)]
            reported = statuses and statuses[0] in ('reported_empty','reported_values')
            count = sum(e['match_id']==match['match_id'] and e['event_kind']=='hero_kill:'+str(slot)
                        for e in raw_events) if reported else None
            summary_count = json.loads(player['summary_json']).get('kills')
            kill_coverage.append({'player_slot':slot, 'summary_kills':summary_count,
                'logged_kill_records_including_pregame':count,
                'comparison':'not_comparable_missing_source' if count is None or summary_count is None
                else 'counts_equal_not_proof_of_completeness' if count==summary_count else 'counts_differ_cause_unverified'})
        entry['kill_log_summary_cross_check'] = kill_coverage
        if match['match_id'] in loaded:
            legacy=next((r for r in legacy_sources if r['match_id']==match['match_id']),None)
            if legacy:entry['legacy_source']=legacy
            captured=next((r for r in captured_sources if r['match_id']==match['match_id']),None)
            if captured:entry['captured_source']=captured
        match_coverage.append(entry)
    if sha(bounded(database,128*1024**2)) != digest:
        raise ValueError('ward_event_windows_source_changed')
    result = {'schema_version': VERSION_WINDOWS, 'database_sha256': digest,
        'snapshot_sha256': meta['snapshot_sha256'], 'match_coverage': match_coverage,
        'selected_event_count': len(selected), 'distinct_matches_with_selected_events': len(event_counts),
        'event_family_counts': dict(Counter(e['family'] for e in selected)),
        'event_exclusions': dict(event_exclusions), 'ward_exclusions': dict(exclusions),
        'events': details, 'wards_by_match': dict(wards),
        'legacy_sources':legacy_sources,
        'captured_sources':captured_sources,
        'context_extension_version':meta.get('context_extension_version'),
        'policy': {'windows_s': [60,180,300], 'clock_perturbations_s': [-2,0,2],
            'before_interval': '[event+shift-span,event+shift)',
            'after_interval': '(event+shift,event+shift+span]',
            'paired_difference': 'only_when_both_windows_fit_inside_match',
            'coordinate_cell_world_units': CELL},
        'limitations': ['Association candidates only; no causal, effectiveness or tactical verdict.',
            'Cross-source clocks remain unverified; +/-2s perturbation is not a certified clock-error bound.',
            'Missing context is not zero events. Multiple event windows from one match are not independent matches.',
            'Cells describe placements, not currently active ward coverage or terrain-aware vision.',
            'A building owner is not its killer; denies and nonhero last hits remain possible.',
            'Metric samples retain source names; gold_t is not asserted to be net worth.',
            'Window edges and nearby events overlap; a single placement can appear in several windows.',
            'No goal, lane or Roshan spatial proximity is inferred from time-only links.']}
    output.mkdir(parents=True)
    (output/'event-windows.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    with (output/'primary-comparisons.jsonl').open('x',encoding='utf-8',newline='\n') as stream:
        for row in primary_comparisons:
            stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    return result


def render(result, output, map_path):
    output = Path(output)
    raw = bounded(map_path, 12*1024**2)
    if sha(raw) != '9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('ward_event_windows_pinned_map_required')
    (output/'map.jpg').write_bytes(raw)
    identifiers = {r['match_id'] for r in result['events']}
    display = {k: result[k] for k in ('events','match_coverage','selected_event_count','distinct_matches_with_selected_events')}
    display['wards_by_match'] = {mid: rows for mid,rows in result['wards_by_match'].items() if mid in identifiers}
    payload = json.dumps(display,ensure_ascii=False).replace('<','\\u003c')
    page = r'''<!doctype html><meta charset="utf-8"><title>比赛事件与眼位时间窗口</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:22px}select{font:inherit;margin:6px;padding:5px;max-width:90vw}a{color:#8cd8c3}.warn{color:#f1cc87;max-width:1200px}td,th{padding:9px;border-bottom:1px solid #52677b}table{border-collapse:collapse;width:100%}.scroll{max-height:65vh;overflow:auto}svg{width:100%;min-width:950px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<h1>事件前后：眼位放置发生在哪里？</h1>
<p class="warn">先看数据，不评好坏。跨来源时钟未校准，±2秒只是参数扰动，不是误差保证；地图未逐场校准。只显示放置位置，不代表眼仍存活、实际视野或目标附近。</p>
<p id="coverage"></p><label>比赛<select id="match"></select></label><label>事件类型<select id="family"></select></label><br>
<label>具体事件<select id="event"></select></label><label>前后各<select id="span"><option value="180">3分钟</option><option value="60">1分钟</option><option value="300">5分钟</option></select></label><label>时钟扰动<select id="shift"><option value="0">0秒</option><option value="-2">-2秒</option><option value="2">+2秒</option></select></label>
<p id="status"></p><pre id="source"></pre>
<p>天辉绿、夜魇红；事件前为空心圆，事件后为实心方块。只表示时间关联，同一点可能有多条记录。地图标号对应下方的放置时刻/索引。</p>
<div class="scroll"><svg viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/><g id="points"></g></svg></div>
<table><thead><tr><th>阵营/眼型</th><th>前窗放置</th><th>后窗放置</th><th>数量差（双窗完整）</th><th>前/后放置格数</th><th>仅后/仅前出现格数</th></tr></thead><tbody id="counts"></tbody></table>
<details><summary>放眼逐条记录</summary><pre id="placements"></pre></details>
<details><summary>附近其他事件（原始事件±3分钟）</summary><pre id="nearby"></pre></details>
<details><summary>明确时间戳的指标样本（固定原始事件±3分钟）</summary><p>使用各请求时刻之前最近样本，最多90秒，不插值。gold_t保留原字段，不命名为净资产；样本采样时刻不同于事件时刻。</p><pre id="metrics"></pre></details>
<details><summary>比赛上下文覆盖</summary><pre id="missing"></pre></details>
<p><a href="event-windows.json">完整JSON</a> · <a href="primary-comparisons.jsonl">原始事件前后3分钟对照</a>。多个事件及窗口可以重叠，不能当成独立比赛或因果证据。</p>
<script>const D=PAYLOAD,ns='http://www.w3.org/2000/svg',M=document.getElementById('match'),F=document.getElementById('family'),E=document.getElementById('event'),S=document.getElementById('span'),C=document.getElementById('shift');
const names={tower_destroyed:'防御塔倒下',barracks_destroyed:'兵营倒下',ancient_destroyed:'遗迹倒下',other_building:'其他建筑',roshan_kill:'肉山被击杀',roshan_death_record:'肉山死亡记录',hero_kill_log:'英雄击杀日志',hero_death_candidate:'英雄死亡候选（标记待核对）',buyback:'买活'};
document.getElementById('coverage').insertAdjacentText('afterend',' \u65e7\u5bfc\u51fa\u7684\u76ee\u6807\u8bb0\u5f55\u53d7\u82f1\u96c4\u53c2\u4e0e\u8fc7\u6ee4\u5f71\u54cd\uff1b\u82f1\u96c4\u6b7b\u4ea1\u5019\u9009\u4e0d\u5f53\u4f5c\u5b8c\u6574KDA\u3002');
function el(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e}function clock(t){const q=Math.round(t*10);return Math.floor(q/600)+':'+((q%600)/10).toFixed(1).padStart(4,'0')}function svg(tag,attrs){const e=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);return e}
document.getElementById('coverage').textContent='事实库'+D.match_coverage.length+'场；'+D.distinct_matches_with_selected_events+'场有已选事件，共'+D.selected_event_count+'条。其余缺上下文，不按0事件计；有解析结果也不保证事件齐全。';document.getElementById('missing').textContent=D.match_coverage.map(r=>r.match_id+' / '+r.context_status+' / 已选事件'+r.selected_events+' / objectives='+r.objective_coverage.join(',')+(r.legacy_source?' / '+r.legacy_source.coverage:'')+(r.captured_source?' / complete_isolated_capture / build='+r.captured_source.replay_metadata.game_build:'')+' / 补丁'+r.patch_key+' / 地图'+r.map_key+'\n'+r.kill_log_summary_cross_check.map(k=>'槽'+k.player_slot+' / 摘要击杀'+k.summary_kills+' / 逐次记录'+(k.logged_kill_records_including_pregame===null?'未记录':k.logged_kill_records_including_pregame)+' / '+k.comparison).join('\n')).join('\n');
for(const mid of [...new Set(D.events.map(r=>r.match_id))]){const o=el('option',mid);o.value=mid;M.append(o)}
function families(){F.replaceChildren();const kinds=new Set(D.events.filter(r=>r.match_id===M.value).map(r=>r.family));for(const k of Object.keys(names))if(kinds.has(k)){const o=el('option',names[k]);o.value=k;F.append(o)}events()}
function events(){E.replaceChildren();D.events.forEach((r,i)=>{if(r.match_id===M.value&&r.family===F.value){const o=el('option',clock(r.time_s)+' / '+(r.payload.key||r.payload.target||r.payload.type||r.family)+' / #'+r.event_index);o.value=i;E.append(o)}});draw()}
function draw(){const e=D.events[+E.value];if(!e){document.getElementById('status').textContent='当前没有可查看的事件，不能理解为比赛中没发生事件。';return}const w=e.windows.find(r=>r.span_each_side_s===+S.value&&r.center_shift_s===+C.value),body=document.getElementById('counts');body.replaceChildren();for(const r of w.counts){const tr=el('tr');for(const v of [(r.team===2?'天辉':'夜魇')+' / '+(r.ward_type==='observer'?'假眼':'真眼'),r.observed_before_count,r.observed_after_count,r.count_difference_if_both_full===null?'窗口不完整':r.count_difference_if_both_full,r.before_placement_cells.length+' / '+r.after_placement_cells.length,r.only_in_after_placement_cells.length+' / '+r.only_in_before_placement_cells.length])tr.append(el('td',v));body.append(tr)}
document.getElementById('status').textContent='事件'+clock(e.time_s)+' / 扰动后中心'+clock(w.center_s)+' / 前窗'+(w.before_full?'完整':'不足')+' / 后窗'+(w.after_full?'完整':'不足')+' / 与中心同刻的放眼'+w.equal_center_refs_excluded_from_before_after.length+'条单列，不放入前后窗。';document.getElementById('source').textContent='事件来源 '+e.event_kind+' / index='+e.event_index+' / '+e.time_basis+'\n'+JSON.stringify(e.payload)+'\n建筑归属（名称）：'+(e.building_owner_from_entity_key===2?'天辉':e.building_owner_from_entity_key===3?'夜魇':'未确定')+'；未按建筑归属推断击杀方。';
const rows=D.wards_by_match[e.match_id]||[],byIndex=new Map(rows.map(r=>[r.ward_index,r])),points=document.getElementById('points'),lines=[];points.replaceChildren();for(const[side,refs]of [['前',w.before_refs],['后',w.after_refs],['同刻',w.equal_center_refs_excluded_from_before_after]])for(const index of refs){const r=byIndex.get(index);if(!r)continue;const n=lines.length+1;lines.push(n+' / '+side+' / '+clock(r.placed_s)+' / #'+index+' / '+(r.team===2?'天辉':'夜魇')+' / '+r.ward_type);const x=((8878-8356)/2+(r.x-7487)/18322*8356)/8878*1600,y=(1-(r.y-7693)/18322)*8356/8878*1600,color=r.team===2?'#70d59e':'#ee8888';points.append(side==='前'?svg('circle',{cx:x,cy:y,r:10,fill:'none',stroke:color,'stroke-width':4}):svg('rect',{x:x-8,y:y-8,width:16,height:16,fill:color,stroke:'#101820','stroke-width':2}));const text=svg('text',{x:x+12,y:y+6,fill:'white',stroke:'black','stroke-width':3,'paint-order':'stroke','font-size':22});text.textContent=n;points.append(text)}document.getElementById('placements').textContent=lines.length?lines.join('\n'):'观察区间没有导出的放眼记录。';document.getElementById('nearby').textContent=e.nearby_event_refs.map(r=>clock(r.time_s)+' / '+names[r.family]+' / '+r.event_kind+' #'+r.event_index).join('\n')||'没有其他已选事件记录。';document.getElementById('metrics').textContent=e.metric_checkpoints.map(c=>'请求'+clock(c.requested_time_s)+' / '+c.coverage_status+'\n'+c.samples.map(r=>'槽'+r.player_slot+' / '+r.metric+' / '+(r.value===null?'未记录':r.value)+' / 样本'+(r.sample_time_s===null?'未记录':clock(r.sample_time_s))+' / '+r.status).join('\n')).join('\n\n')}
const originalDraw=draw;draw=function(){originalDraw();const e=D.events[+E.value];if(e&&e.replay_metric_checkpoints){const coverage=D.match_coverage.find(s=>s.match_id===e.match_id),source=coverage&&coverage.captured_source,projection=source&&source.metric_clock_projection;document.getElementById('metrics').textContent+='\n\nDEM原始计数 / 逻辑玩家ID，不与API槽位混合。'+(projection?' 本场'+projection.changed_packets.length+'个暂停采样包的时间按原tick与最终游戏时钟明确投影；原始时间见来源证据，计数未改。':'')+' 同游戏时刻多包时取较大tick。\n'+JSON.stringify(e.replay_metric_checkpoints,null,2)}};
M.onchange=families;F.onchange=events;E.onchange=S.onchange=C.onchange=draw;families();</script>'''.replace('PAYLOAD',payload)
    (output/'report.html').write_text(page,encoding='utf-8',newline='\n')
