"""Descriptive ward-location/time associations; no effectiveness or advice model."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import csv
import html
import json
import math
from pathlib import Path
import sqlite3
import time

from .postgame_store import VERSION, bounded, sha
from .postgame_event_types import TARGET_FAMILIES,event_family

PATTERN_VERSION = 'ward_space_time_descriptive_v2'
COORDINATES = 'gem_cell128_plus_vec_raw_coordinates'
CELL = 512
PHASE = 600


def key(match):
    return tuple(match[k] for k in ('source_group','producer_cohort','patch_key','map_key','mode_key'))


def describe(database, output):
    database, output = Path(database).resolve(), Path(output).resolve()
    if output.exists() or database.stat().st_size > 128 * 1024**2:
        raise ValueError('ward_patterns_new_bounded_output_required')
    started = time.monotonic()
    database_sha = sha(bounded(database, 128 * 1024**2))
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        meta = dict(connection.execute('SELECT key,value FROM metadata'))
        if meta.get('schema_version') != VERSION:
            raise ValueError('ward_patterns_fact_schema_mismatch')
        matches = {r['match_id']: dict(r) for r in connection.execute('SELECT * FROM matches')}
        wards = [dict(r) for r in connection.execute('SELECT * FROM wards ORDER BY match_id,ward_index')]
        context_events = [dict(r) for r in connection.execute('SELECT * FROM events ORDER BY match_id,time_s')]
    finally:
        connection.close()
    if len(matches) > 256 or len(wards) > 100_000 or len(context_events) > 100_000:
        raise ValueError('ward_patterns_row_budget')
    cohorts = defaultdict(list)
    for match in matches.values():
        cohorts[key(match)].append(match)
    groups, labels = {}, {}
    for index, (cohort, rows) in enumerate(sorted(cohorts.items()), 1):
        label = 'cohort_' + str(index)
        labels[cohort] = label
        groups[label] = {'source_group': cohort[0], 'producer_cohort': cohort[1],
            'patch_key': cohort[2], 'map_key': cohort[3], 'mode_key': cohort[4],
            'match_count': len(rows), 'match_ids': sorted(r['match_id'] for r in rows),
            'producer': json.loads(rows[0]['producer_json']), 'phase_exposure': []}
    exposure = {}
    for cohort, rows in cohorts.items():
        for phase in range(math.ceil(max(r['duration_s'] for r in rows) / PHASE)):
            full = {r['match_id'] for r in rows if r['duration_s'] >= (phase + 1) * PHASE}
            partial = {r['match_id'] for r in rows if phase * PHASE < r['duration_s'] < (phase + 1) * PHASE}
            exposure[(cohort, phase)] = full
            groups[labels[cohort]]['phase_exposure'].append({'phase': phase,
                'full_interval_matches': len(full), 'partial_interval_matches': len(partial),
                'ended_before_interval': len(rows)-len(full)-len(partial)})
    buckets, exclusions, eligible_wards, partial_wards = defaultdict(list), Counter(), [], []
    for ward in wards:
        if ward['placement_status'] != 'in_game':
            exclusions[ward['placement_status']] += 1
            continue
        if ward['coordinate_basis'] != COORDINATES:
            exclusions['unsupported_coordinate_basis'] += 1
            continue
        match = matches[ward['match_id']]
        cohort, phase = key(match), int(ward['placed_s'] // PHASE)
        if ward['match_id'] not in exposure[(cohort, phase)]:
            partial_wards.append(ward)
            continue
        eligible_wards.append(ward)
        for offset in (0, CELL // 2):
            gx, gy = math.floor((ward['x']-offset)/CELL), math.floor((ward['y']-offset)/CELL)
            buckets[(cohort,phase,ward['ward_type'],ward['team'],offset,gx,gy)].append(ward)
    patterns, by_key = [], {}
    for bucket, rows in sorted(buckets.items()):
        cohort, phase, kind, team, offset, gx, gy = bucket
        ids = sorted({r['match_id'] for r in rows})
        full = exposure[(cohort, phase)]
        center = (offset+(gx+.5)*CELL, offset+(gy+.5)*CELL)
        representative = min(rows, key=lambda r: (math.dist(center,(r['x'],r['y'])),r['match_id'],r['ward_index']))
        item = {'cohort': labels[cohort], 'phase': phase, 'ward_type': kind, 'team': team,
            'grid_offset': offset, 'grid_x': gx, 'grid_y': gy,
            'distinct_match_count': len(ids), 'eligible_full_interval_matches': len(full),
            'match_fraction': len(ids)/len(full), 'placement_count': len(rows),
            'support_match_ids': ids,
            'placement_refs': [{'match_id':r['match_id'],'ward_index':r['ward_index']} for r in rows],
            'representative': {'x':representative['x'],'y':representative['y'],
                'match_id':representative['match_id'],'ward_index':representative['ward_index'],
                'selection':'actual_placement_nearest_fixed_cell_center_not_recommended_spot'},
            'candidate_eligible': len(ids) >= 3,
            'semantics':'match_frequency_of_placement_not_vision_or_deward_success'}
        patterns.append(item)
        by_key[bucket] = item
    # The half-cell shift is a sensitivity description, not independent data.
    for bucket, rows in buckets.items():
        cohort, phase, kind, team, offset, gx, gy = bucket
        if offset:
            continue
        candidates = defaultdict(set)
        for row in rows:
            moved = (math.floor((row['x']-CELL/2)/CELL), math.floor((row['y']-CELL/2)/CELL))
            candidates[moved].add(row['match_id'])
        winner, support = max(candidates.items(), key=lambda r:(len(r[1]),r[0]))
        item = by_key[bucket]
        item['offset_sensitivity'] = {'half_shift_cell': list(winner),
            'largest_retained_distinct_match_count': len(support),
            'retained_support_fraction': len(support)/item['distinct_match_count'],
            'meaning':'same_observations_regrouped_not_replication_or_accuracy'}
    overlaps = []
    for bucket, item in by_key.items():
        cohort, phase, kind, team, offset, gx, gy = bucket
        if team != 2 or offset or not item['candidate_eligible']:
            continue
        peer = by_key.get((cohort,phase,kind,3,0,gx,gy))
        if not peer or not peer['candidate_eligible']:
            continue
        radiant, dire = set(item['support_match_ids']), set(peer['support_match_ids'])
        full = exposure[(cohort, phase)]
        overlaps.append({'cohort': labels[cohort], 'phase': phase, 'ward_type': kind,
            'grid_x': gx, 'grid_y': gy, 'radiant_matches': len(radiant), 'dire_matches': len(dire),
            'both_in_same_match': len(radiant&dire), 'radiant_only': len(radiant-dire),
            'dire_only': len(dire-radiant), 'neither': len(full-(radiant|dire)),
            'eligible_full_interval_matches': len(full),
            'meaning':'both_teams_use_same_cell_not_simultaneous_visibility_or_preference_cause'})
    transitions = []
    for bucket, item in by_key.items():
        cohort, phase, kind, team, offset, gx, gy = bucket
        if offset or not item['candidate_eligible']:
            continue
        for before, after in ((phase-1,phase),(phase,phase+1)):
            common = exposure.get((cohort, after), set())
            if before < 0 or not common:
                continue
            a = by_key.get((cohort,before,kind,team,0,gx,gy))
            b = by_key.get((cohort,after,kind,team,0,gx,gy))
            left = set(a['support_match_ids']) & common if a else set()
            right = set(b['support_match_ids']) & common if b else set()
            transitions.append({'cohort': labels[cohort], 'from_phase': before, 'to_phase': after,
                'ward_type': kind,'team':team,'grid_x':gx,'grid_y':gy,
                'same_eligible_match_count':len(common), 'early_matches':len(left),'late_matches':len(right),
                'both':len(left&right),'early_only':len(left-right),'late_only':len(right-left),
                'neither':len(common-(left|right)),
                'matched_frequency_change':(len(right)-len(left))/len(common),
                'review_priority_eligible':len(common)>=10 and max(len(left),len(right))>=3,
                'meaning':'paired_surviving_cohort_descriptive_change_not_causal_or_significance'})
    unique_transitions = {}
    for row in transitions:
        k = tuple(row[n] for n in ('cohort','from_phase','ward_type','team','grid_x','grid_y'))
        unique_transitions[k] = row
    transitions = list(unique_transitions.values())
    # Spatial/time proximity is retained for later review, never converted into
    # "a sentry saw this observer" or a successful deward label.
    by_match = defaultdict(list)
    for ward in wards:
        if ward['placement_status']=='in_game' and ward['coordinate_basis']==COORDINATES:
            by_match[ward['match_id']].append(ward)
    associations, comparisons, truncated = [], 0, False
    for identifier, rows in by_match.items():
        sentries = sorted((r for r in rows if r['ward_type']=='sentry'),key=lambda r:r['placed_s'])
        times = [r['placed_s'] for r in sentries]
        for observer in (r for r in rows if r['ward_type']=='observer'):
            first, last = bisect_left(times,observer['placed_s']-90), bisect_right(times,observer['placed_s']+90)
            for sentry in sentries[first:last]:
                comparisons += 1
                if comparisons>1_000_000 or len(associations)>=50_000 or time.monotonic()-started>60:
                    truncated = True
                    break
                if sentry['team']==observer['team']:
                    continue
                distance = math.hypot(observer['x']-sentry['x'],observer['y']-sentry['y'])
                if distance>700:
                    continue
                associations.append({'match_id':identifier,'cohort':labels[key(matches[identifier])],
                    'observer_index':observer['ward_index'],
                    'sentry_index':sentry['ward_index'],'observer_team':observer['team'],
                    'delta_sentry_minus_observer_s':sentry['placed_s']-observer['placed_s'],
                    'distance_world_units':round(distance,3), 'observer_lifetime_status':observer['lifetime_status'],
                    'observer_exported_end_s':observer['ended_s'], 'observer_placed_s':observer['placed_s'],
                    'sentry_placed_s':sentry['placed_s'], 'deward_outcome':'not_inferred',
                    'scope':'offline_time_space_proximity_not_detection_or_terrain_visibility'})
            if truncated:
                break
        if truncated:
            break
    context_pairs, context_truncated = [], False
    ordered_wards={mid:sorted(rows,key=lambda r:r['placed_s']) for mid,rows in by_match.items()}
    ordered_times={mid:[r['placed_s'] for r in rows] for mid,rows in ordered_wards.items()}
    for event in context_events:
        family=event_family(event)
        if family not in TARGET_FAMILIES or event['time_s'] is None or not 0<=event['time_s']<matches[event['match_id']]['duration_s']:
            continue
        rows=ordered_wards.get(event['match_id'],[])
        times=ordered_times.get(event['match_id'],[])
        first,last=bisect_left(times,event['time_s']-90),bisect_right(times,event['time_s']+90)
        for ward in rows[first:last]:
            if len(context_pairs)>=50_000 or time.monotonic()-started>60:
                context_truncated=True
                break
            context_pairs.append({'match_id':event['match_id'],'ward_index':ward['ward_index'],
                    'event_kind':event['event_kind'],'event_index':event['event_index'],
                    'event_family':family,'source_context_sha256':event['context_sha256'],
                    'delta_ward_minus_event_s':ward['placed_s']-event['time_s'],
                    'event_payload':json.loads(event['payload']),
                    'clock_alignment':event['time_basis'],
                    'spatial_relation':'not_known_time_only','causal_verdict':'not_inferred'})
        if context_truncated:
            break
    result = {'schema_version': PATTERN_VERSION, 'database_sha256':database_sha,
        'snapshot_sha256':meta['snapshot_sha256'],'frozen_at_utc':meta['frozen_at_utc'],
        'policy':{'phase_seconds':PHASE,'cell_world_units':CELL,'offsets':[0,CELL//2],
            'time_context_event_families':sorted(TARGET_FAMILIES),
            'minimum_distinct_matches_for_candidate':3,'denominator':'whole_interval_surviving_matches_in_same_cohort',
            'cohort_fields':['source_group','producer_cohort','patch_key','map_key','mode_key'],
            'counts':'one_vote_per_match_per_team_type_cell_interval_even_if_repeatedly_replaced'},
        'matches':len(matches),'ward_records':len(wards),'full_interval_placements':len(eligible_wards),
        'partial_interval_placements_preserved_not_in_frequency':len(partial_wards),
        'exclusions':dict(exclusions),'cohorts':groups,'patterns':patterns,
        'both_team_cells':overlaps,'paired_time_changes':transitions,
        'observer_enemy_sentry_proximity_count':len(associations),'proximity_truncated':truncated,
        'time_only_context_pair_count':len(context_pairs),
        'time_only_context_truncated':context_truncated,
        'limitations':['Unknown patch/map/mode cohorts are provisional, not verified map-compatible populations.',
            'Sampling is selected D2PT matches and personal games, not a random population of all players.',
            'Cell boundaries can split cliffs or combine different terrain; no optimal ward location is inferred.',
            'Later intervals condition on survival; paired comparisons still do not remove all selection bias.',
            'No significance testing, causal verdict, player grade, advice or live warning is produced.']}
    if sha(bounded(database,128*1024**2))!=database_sha:
        raise ValueError('ward_pattern_database_changed')
    output.mkdir(parents=True)
    (output/'patterns.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    for name,rows in (('observer-sentry-proximity.jsonl',associations),('time-only-context-pairs.jsonl',context_pairs)):
        with (output/name).open('x',encoding='utf-8',newline='\n') as stream:
            for row in rows:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    columns = ('cohort','phase','ward_type','team','grid_offset','grid_x','grid_y','distinct_match_count',
               'eligible_full_interval_matches','match_fraction','placement_count','candidate_eligible')
    with (output/'cell-frequency.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        writer.writerows({k:r[k] for k in columns} for r in patterns)
    return result


def render(result, output, map_path):
    """Browsable evidence on the existing illustrative map; no map truth claim."""
    output, map_path = Path(output), Path(map_path)
    raw=bounded(map_path,12*1024**2)
    if sha(raw)!='9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('ward_pattern_pinned_map_required')
    (output/'map.jpg').write_bytes(raw)
    # All numeric results remain in patterns.json/CSV. Keep the interactive
    # display bounded to the strongest 20 distinct-match supports per group.
    candidates=defaultdict(list)
    for row in result['patterns']:
        if row['grid_offset']==0 and row['candidate_eligible']:
            candidates[(row['cohort'],row['phase'],row['ward_type'],row['team'])].append(row)
    displayed=[]
    for rows in candidates.values():
        displayed.extend(sorted(rows,key=lambda r:(-r['distinct_match_count'],r['grid_x'],r['grid_y']))[:20])
    payload=json.dumps({'cohorts':result['cohorts'],'rows':displayed,'both':result['both_team_cells'],
        'shifts':result['paired_time_changes'],
        'matches':result['matches'],'ward_records':result['ward_records']},ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><meta charset="utf-8"><title>按时间与阵营观察眼位</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:22px}select{font:inherit;padding:5px;margin:6px}a{color:#8cd8c3}.warn{max-width:1150px;color:#f1cc87}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #52677b}.scroll{max-height:72vh;overflow:auto}svg{width:100%;min-width:1000px}tr:target{background:#435b69}</style>
<h1>眼位时间与位置：描述性数据浏览</h1><p class="warn">先看记录，不评好坏。补丁、地图、模式未知会明确显示，不能当作已确认兼容。格子可能跨高低差；底图仅帮助理解坐标，未逐场核验。次数按不同比赛计，不是反眼成功率。</p>
<label>数据组<select id="cohort"></select></label><label>时段<select id="phase"></select></label>
<label>眼型<select id="kind"><option value="observer">假眼</option><option value="sentry">真眼</option></select></label>
<label>放置方<select id="team"><option value="2">天辉</option><option value="3">夜魇</option></select></label>
<p id="exposure"></p><p>每组最多显示20格，至少3场不同比赛；完整数据在 <a href="patterns.json">JSON</a> 和 <a href="cell-frequency.csv">CSV</a>。</p>
<p>方框是512单位区域，圆点是其中一条真实记录的位置，不表示所有眼都精确放在那个点。</p>
<div class="scroll"><svg id="map" viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/><g id="dots"></g></svg></div>
<table><thead><tr><th>标号</th><th>不同比赛 / 分母</th><th>记录数</th><th>格子</th><th>半格偏移留存</th><th>原始比赛与眼位序号</th></tr></thead><tbody id="rows"></tbody></table>
<h2>双方都使用的区域格</h2><p>同一时间段的同一格都出现放眼，不表示同时有眼、看见对方或因果关系。</p><div id="both"></div>
<h2>同一批比赛的前后变化</h2><p>下一时段仍未结束的同一批比赛重新计数；至少10场、至少一个时段有3场支持才列入下方。所有小样本仍保存在JSON；这里不是显著性或因果检验。</p><div id="changes"></div>
<script>const D=PAYLOAD,ns='http://www.w3.org/2000/svg',cohort=document.getElementById('cohort'),phase=document.getElementById('phase'),kind=document.getElementById('kind'),team=document.getElementById('team');
const entries=Object.entries(D.cohorts).sort((a,b)=>(b[1].source_group==='d2pt')-(a[1].source_group==='d2pt')||b[1].match_count-a[1].match_count);for(const[k,c]of entries){const o=document.createElement('option');o.value=k;o.textContent=({own:'本人',d2pt:'D2PT',tier1:'Tier 1 职业'}[c.source_group]||c.source_group)+' '+c.match_count+'场 · 提取器'+c.producer_cohort.slice(0,8)+' · 补丁'+c.patch_key+' · 模式'+c.mode_key;cohort.append(o)}
function options(){phase.replaceChildren();for(const e of D.cohorts[cohort.value].phase_exposure){const o=document.createElement('option');o.value=e.phase;o.textContent=e.phase*10+'～'+(e.phase+1)*10+'分钟';phase.append(o)}draw()}
function svg(tag,attrs){const e=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);return e}
function draw(){const c=D.cohorts[cohort.value],p=+phase.value,e=c.phase_exposure.find(r=>r.phase===p);document.getElementById('exposure').textContent='完整覆盖这个10分钟段：'+e.full_interval_matches+'场；中途结束：'+e.partial_interval_matches+'场（记录保留，未放进完整段分母）；此前结束：'+e.ended_before_interval+'场。地图版本：'+c.map_key;
const rows=D.rows.filter(r=>r.cohort===cohort.value&&r.phase===p&&r.ward_type===kind.value&&r.team===+team.value).sort((a,b)=>b.distinct_match_count-a.distinct_match_count),body=document.getElementById('rows'),dots=document.getElementById('dots');body.replaceChildren();dots.replaceChildren();
rows.forEach((r,i)=>{const a=r.representative,x=((8878-8356)/2+(a.x-7487)/18322*8356)/8878*1600,y=(1-(a.y-7693)/18322)*8356/8878*1600,g=svg('a',{href:'#cell-'+i});const side=512/18322*8356/8878*1600,bx=((8878-8356)/2+(r.grid_x*512-7487)/18322*8356)/8878*1600,by=(1-((r.grid_y+1)*512-7693)/18322)*8356/8878*1600;g.append(svg('rect',{x:bx,y:by,width:side,height:side,fill:r.team===2?'#70d59e':'#ee8888','fill-opacity':.12,stroke:r.team===2?'#70d59e':'#ee8888','stroke-width':2}));g.append(svg('circle',{cx:x,cy:y,r:10,fill:r.team===2?'#70d59e':'#ee8888',stroke:'#101820','stroke-width':3}));const text=svg('text',{x:x+13,y:y+7,fill:'white',stroke:'black','stroke-width':3,'paint-order':'stroke','font-size':24});text.textContent=i+1;g.append(text);dots.append(g);const tr=document.createElement('tr');tr.id='cell-'+i;const vals=[i+1,r.distinct_match_count+'/'+r.eligible_full_interval_matches+' ('+(r.match_fraction*100).toFixed(1)+'%)',r.placement_count,r.grid_x+','+r.grid_y,r.offset_sensitivity.largest_retained_distinct_match_count+'/'+r.distinct_match_count];for(const v of vals){const td=document.createElement('td');td.textContent=v;tr.append(td)}const td=document.createElement('td'),details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='查看 '+r.placement_refs.length+' 条来源';details.append(summary);for(const s of r.placement_refs){const line=document.createElement('div');line.textContent=s.match_id+' / ward_index='+s.ward_index;details.append(line)}td.append(details);tr.append(td);body.append(tr)});
if(!rows.length){const tr=document.createElement('tr');tr.textContent='没有达到3场门槛的格子，不能理解为该时段没有值得观察的位置。';body.append(tr)}const shared=D.both.filter(r=>r.cohort===cohort.value&&r.phase===p&&r.ward_type===kind.value);document.getElementById('both').textContent=shared.length?shared.map(r=>'格('+r.grid_x+','+r.grid_y+')：天辉'+r.radiant_matches+'场、夜魇'+r.dire_matches+'场，同场双方都放过'+r.both_in_same_match+'场 / 分母'+r.eligible_full_interval_matches).join('； '):'当前分组没有双方都达到3场门槛的区域格。';const shifts=D.shifts.filter(r=>r.cohort===cohort.value&&r.from_phase===p&&r.ward_type===kind.value&&r.team===+team.value&&r.review_priority_eligible).sort((a,b)=>Math.abs(b.matched_frequency_change)-Math.abs(a.matched_frequency_change)).slice(0,12),changes=document.getElementById('changes');changes.replaceChildren();for(const r of shifts){const line=document.createElement('p');line.textContent='格('+r.grid_x+','+r.grid_y+')：前段'+r.early_matches+'场 → 后段'+r.late_matches+'场 / 同一批'+r.same_eligible_match_count+'场；两段都出现'+r.both+'场，频次变化'+(r.matched_frequency_change*100).toFixed(1)+'个百分点。';changes.append(line)}if(!shifts.length)changes.textContent='没有达到展示门槛的同批时间变化。'}
cohort.onchange=options;phase.onchange=kind.onchange=team.onchange=draw;options();</script>'''.replace('PAYLOAD',payload)
    (output/'report.html').write_text(page,encoding='utf-8',newline='\n')
