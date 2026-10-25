"""Offline same-region ward revisits with explicit follow-up opportunities."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import sqlite3
import statistics

from .postgame_store import VERSION, bounded, sha
from .ward_patterns import CELL, COORDINATES, PHASE, key

VERSION_REVISITS = 'ward_region_revisits_v1'
HORIZONS = (300, 600, 900)


def analyze(database, output):
    """Read frozen facts only; do not reparse, call APIs, or modify the source."""
    database, output = Path(database).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('ward_revisits_new_output_required')
    source_digest = sha(bounded(database, 128 * 1024**2))
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        meta = dict(connection.execute('SELECT key,value FROM metadata'))
        if meta.get('schema_version') != VERSION:
            raise ValueError('ward_revisits_facts_schema')
        matches = {r['match_id']: dict(r) for r in connection.execute('SELECT * FROM matches')}
        raw = [dict(r) for r in connection.execute('SELECT * FROM wards ORDER BY match_id,placed_s,ward_index')]
    finally:
        connection.close()
    if len(matches) > 256 or len(raw) > 100_000:
        raise ValueError('ward_revisits_row_budget')
    exclusions = Counter()
    wards = []
    for ward in raw:
        if ward['placement_status'] != 'in_game':
            exclusions[ward['placement_status']] += 1
        elif ward['coordinate_basis'] != COORDINATES:
            exclusions['unsupported_coordinate_basis'] += 1
        else:
            wards.append(ward)
    cohorts = {}
    for cohort in sorted({key(m) for m in matches.values()}):
        label = 'cohort_' + str(len(cohorts) + 1)
        rows = [m for m in matches.values() if key(m) == cohort]
        cohorts[cohort] = {'id': label, **dict(zip(
            ('source_group', 'producer_cohort', 'patch_key', 'map_key', 'mode_key'), cohort)),
            'match_ids': sorted(m['match_id'] for m in rows), 'match_count': len(rows)}
    summaries = defaultdict(list)
    primary_anchors = []
    for offset in (0, CELL // 2):
        regions = defaultdict(list)
        for ward in wards:
            regions[(ward['match_id'], ward['ward_type'],
                     math.floor((ward['x'] - offset) / CELL),
                     math.floor((ward['y'] - offset) / CELL))].append(ward)
        for (identifier, kind, gx, gy), rows in sorted(regions.items()):
            match = matches[identifier]
            cohort = cohorts[key(match)]['id']
            teams = {team: [r for r in rows if r['team'] == team] for team in (2, 3)}
            times = {team: [r['placed_s'] for r in group] for team, group in teams.items()}
            at_time = {team: defaultdict(list) for team in (2, 3)}
            for team, group in teams.items():
                for row in group:
                    at_time[team][row['placed_s']].append(row['ward_index'])
            for team, group in teams.items():
                for anchor in group:
                    start = anchor['placed_s']
                    own_pos = bisect_right(times[team], start)
                    enemy_pos = bisect_right(times[5 - team], start)
                    next_own = group[own_pos] if own_pos < len(group) else None
                    enemy_rows = teams[5 - team]
                    next_enemy = enemy_rows[enemy_pos] if enemy_pos < len(enemy_rows) else None
                    same_clock_own = [index for index in at_time[team][start] if index != anchor['ward_index']]
                    same_clock_enemy = at_time[5 - team].get(start, [])
                    # An observed expiry/death is required before calling an interval
                    # a gap or overlap. Match-end-censored wards have no known end.
                    observed_end = (anchor['lifetime_status'] == 'observed_end'
                                    and anchor['ended_s'] is not None)
                    delta_end = (next_own['placed_s'] - anchor['ended_s']
                                 if observed_end and next_own else None)
                    relation = ('unknown_end_or_no_later_placement' if delta_end is None
                                else 'overlap' if delta_end < 0 else 'after_or_at_observed_end')
                    for horizon in HORIZONS:
                        full = match['duration_s'] - start >= horizon
                        own_delta = next_own['placed_s'] - start if next_own else None
                        enemy_delta = next_enemy['placed_s'] - start if next_enemy else None
                        record = {'match_id': identifier, 'ward_index': anchor['ward_index'],
                            'cohort': cohort, 'phase': int(start // PHASE), 'ward_type': kind,
                            'team': team, 'grid_offset': offset, 'grid_x': gx, 'grid_y': gy,
                            'horizon_s': horizon, 'placed_s': start,
                            'available_followup_s': match['duration_s'] - start,
                            'full_horizon': full, 'own_later_within_horizon': own_delta is not None and own_delta <= horizon,
                            'opponent_later_within_horizon': enemy_delta is not None and enemy_delta <= horizon,
                            'next_own_index': next_own['ward_index'] if next_own else None,
                            'next_own_placed_s': next_own['placed_s'] if next_own else None,
                            'next_own_same_clock_indices': at_time[team][next_own['placed_s']] if next_own else [],
                            'next_own_delta_s': own_delta,
                            'next_opponent_index': next_enemy['ward_index'] if next_enemy else None,
                            'next_opponent_placed_s': next_enemy['placed_s'] if next_enemy else None,
                            'next_opponent_same_clock_indices': at_time[5 - team][next_enemy['placed_s']] if next_enemy else [],
                            'next_opponent_delta_s': enemy_delta,
                            'next_own_distance_world_units': math.dist((anchor['x'], anchor['y']),
                                (next_own['x'], next_own['y'])) if next_own else None,
                            'observed_end_s': anchor['ended_s'] if observed_end else None,
                            'end_kind': anchor['end_kind'], 'next_own_vs_end': relation,
                            'next_own_minus_observed_end_s': delta_end,
                            'same_clock_own_indices': same_clock_own,
                            'same_clock_opponent_indices': same_clock_enemy}
                        bucket = (cohort, record['phase'], kind, team, offset, gx, gy, horizon)
                        summaries[bucket].append(record)
                        if offset == 0 and horizon == 600:
                            primary_anchors.append(record)
    aggregates = []
    for bucket, records in sorted(summaries.items()):
        cohort, phase, kind, team, offset, gx, gy, horizon = bucket
        # One first placement per match/phase/region/team, selected before
        # inspecting outcomes. Late successes must not bypass censoring.
        seeds = {}
        for row in records:
            seeds.setdefault(row['match_id'], row)
        eligible = [r for r in seeds.values() if r['full_horizon']]
        censored = [r for r in seeds.values() if not r['full_horizon']]
        own = [r for r in eligible if r['own_later_within_horizon']]
        enemy = [r for r in eligible if r['opponent_later_within_horizon']]
        both = sum(r['opponent_later_within_horizon'] for r in own)
        item = {'cohort': cohort, 'phase': phase, 'ward_type': kind, 'team': team,
            'grid_offset': offset, 'grid_x': gx, 'grid_y': gy, 'horizon_s': horizon,
            'seed_matches': len(seeds), 'full_followup_matches': len(eligible),
            'censored_matches_excluded_from_denominator': len(censored),
            'censored_with_observed_own_revisit': sum(r['own_later_within_horizon'] for r in censored),
            'own_revisit_matches': len(own), 'opponent_later_matches': len(enemy),
            'both_later_matches': both,
            'neither_later_matches': len(eligible) - len(own) - len(enemy) + both,
            'own_revisit_fraction': len(own) / len(eligible) if eligible else None,
            'opponent_later_fraction': len(enemy) / len(eligible) if eligible else None,
            'own_revisit_median_delay_s_conditional_on_observed': statistics.median(
                r['next_own_delta_s'] for r in own) if own else None,
            'own_overlap_matches': sum(r['next_own_vs_end'] == 'overlap' for r in own),
            'own_after_observed_end_matches': sum(r['next_own_vs_end'] == 'after_or_at_observed_end' for r in own),
            'own_unknown_end_matches': sum(r['next_own_vs_end'] == 'unknown_end_or_no_later_placement' for r in own),
            'same_clock_ambiguous_seed_matches': sum(bool(r['same_clock_own_indices'] or
                r['same_clock_opponent_indices']) for r in seeds.values()),
            'review_candidate': len(eligible) >= 10 and max(len(own), len(enemy)) >= 3,
            'seed_refs': [{k: r[k] for k in ('match_id', 'ward_index', 'full_horizon',
                'placed_s', 'available_followup_s', 'next_own_index', 'next_own_placed_s',
                'next_own_delta_s', 'next_opponent_index', 'next_opponent_placed_s',
                'next_opponent_delta_s', 'next_own_vs_end', 'observed_end_s')}
                for r in seeds.values()]}
        aggregates.append(item)
    if sha(bounded(database, 128 * 1024**2)) != source_digest:
        raise ValueError('ward_revisits_source_changed')
    result = {'schema_version': VERSION_REVISITS, 'database_sha256': source_digest,
        'snapshot_sha256': meta['snapshot_sha256'], 'matches': len(matches),
        'input_wards': len(raw), 'in_game_coordinate_wards': len(wards), 'exclusions': dict(exclusions),
        'cohorts': list(cohorts.values()), 'aggregates': aggregates,
        'policy': {'cell_world_units': CELL, 'offsets': [0, CELL // 2], 'horizons_s': HORIZONS,
            'seed': 'first_in_game_placement_in_match_phase_team_type_cell_before_outcome_selection',
            'denominator': 'seed_matches_with_full_horizon_including_no_revisit',
            'time_order': 'strictly_later_only_same_clock_preserved_as_ambiguous',
            'partial_followup': 'retain_even_observed_success_but_exclude_from_both_numerator_and_denominator'},
        'limitations': ['Same cell is a coarse region, not the same exact point or terrain visibility.',
            'Opponent later placement is not takeover, detection, or deward success.',
            'Full follow-up selects surviving matches; no causal or general-population inference.',
            'Median delay is conditional on an observed revisit, not expected waiting time.',
            'Unknown patch/map remains provisional; offsets/horizons are sensitivity, not independent replications.',
            'No observations before game time zero are used as seeds or revisit outcomes.']}
    output.mkdir(parents=True)
    (output / 'revisits.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    with (output / 'anchors-600s-offset0.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
        for row in primary_anchors:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    fields = [k for k in aggregates[0] if k != 'seed_refs'] if aggregates else ['cohort']
    with (output / 'revisits.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: r[k] for k in fields} for r in aggregates)
    return result


def render(result, output, map_path):
    """Render conditional counts and traceable seeds on a pinned illustrative map."""
    output = Path(output)
    raw = bounded(map_path, 12 * 1024**2)
    if sha(raw) != '9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('ward_revisits_pinned_map_required')
    (output / 'map.jpg').write_bytes(raw)
    # Keep the display payload bounded; full six-parameter sensitivity data stays
    # in the machine-readable exports. Primary display is offset0, horizon600.
    displayed = [r for r in result['aggregates'] if r['grid_offset'] == 0 and r['horizon_s'] == 600]
    payload = json.dumps({'rows': displayed, 'cohorts': result['cohorts']}, ensure_ascii=False).replace('<', '\\u003c')
    page = '''<!doctype html><meta charset="utf-8"><title>同区域眼位再次使用</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:22px}select{font:inherit;padding:5px;margin:6px}a{color:#8cd8c3}.warn{color:#f1cc87;max-width:1200px}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #52677b}.scroll{max-height:65vh;overflow:auto}svg{width:100%;min-width:900px}tr:target{background:#435b69}summary{cursor:pointer}.quiet{color:#adbbc9}</style>
<h1>同一区域：多久后再次放眼？</h1>
<p class="warn">描述记录，不评眼位好坏。512单位格可能跨地形；底图仅帮助理解坐标，未知补丁/地图尚未逐局校验。对方随后放眼不等于排眼、接管或真实可见。</p>
<label>数据组<select id="cohort"></select></label><label>起始放眼时段<select id="phase"></select></label>
<label>眼型<select id="kind"><option value="observer">假眼</option><option value="sentry">真眼</option></select></label>
<label>起始方<select id="team"><option value="2">天辉</option><option value="3">夜魇</option></select></label>
<p>每格每场取所选时段的第一次放眼，观察其后10分钟，允许跨到下一时段。完整分母仅含起点后仍打满10分钟的比赛；不足10分钟即使已补眼也单列。仅比较已经来这里放过眼的比赛，不代表所有比赛。</p>
<p class="quiet">同刻记录不判断先后；本方与对方再次放置可以同场都发生。间隔中位数仅针对已经再次放置的样本。图表显示最多40格，优先展示有10场完整随访且任一方有3场再次放置的候选。小样本仍列出并保留完整导出。</p>
<p id="info"></p><div class="scroll"><svg viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/><g id="cells"></g></svg></div>
<table><thead><tr><th>格子/标号</th><th>完整/不足10分钟</th><th>本方再放</th><th>对方随后放</th><th>本方间隔中位数</th><th>本方与原眼结束关系</th><th>来源</th></tr></thead><tbody id="rows"></tbody></table>
<p>所有网格偏移与5/10/15分钟随访数据：<a href="revisits.json">JSON</a>、<a href="revisits.csv">CSV</a>；每次放眼的10分钟观察明细：<a href="anchors-600s-offset0.jsonl">JSONL</a>。</p>
<script>const D=PAYLOAD,ns='http://www.w3.org/2000/svg',C=document.getElementById('cohort'),P=document.getElementById('phase'),K=document.getElementById('kind'),T=document.getElementById('team');
function elem(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e}function svg(tag,attrs){const e=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);return e}
function clock(t){if(t===null)return '未观察到';const q=Math.round(t*10);return Math.floor(q/600)+':'+((q%600)/10).toFixed(1).padStart(4,'0')}function next(t,d,i){return t===null?'未观察到':clock(t)+' / #'+i+' / 间隔'+d.toFixed(1)+'秒'+(d>600?'（超过10分钟）':'')}
for(const c of D.cohorts){const o=elem('option',(c.source_group==='d2pt'?'D2PT':'本人')+' '+c.match_count+'场 · 提取器'+c.producer_cohort.slice(0,8)+' · 补丁'+c.patch_key+' · 模式'+c.mode_key);o.value=c.id;C.append(o)}
function phases(){P.replaceChildren();const ps=[...new Set(D.rows.filter(r=>r.cohort===C.value).map(r=>r.phase))].sort((a,b)=>a-b);for(const p of ps){const o=elem('option',p*10+'～'+(p+1)*10+'分钟');o.value=p;P.append(o)}draw()}
function draw(){const all=D.rows.filter(r=>r.cohort===C.value&&r.phase===+P.value&&r.team===+T.value&&r.ward_type===K.value),rs=all.sort((a,b)=>Number(b.review_candidate)-Number(a.review_candidate)||b.full_followup_matches-a.full_followup_matches||b.own_revisit_matches-a.own_revisit_matches||a.grid_x-b.grid_x||a.grid_y-b.grid_y).slice(0,40);const body=document.getElementById('rows'),cells=document.getElementById('cells');body.replaceChildren();cells.replaceChildren();document.getElementById('info').textContent='当前有'+all.length+'个观察格，其中'+all.filter(r=>r.review_candidate).length+'个达到浏览候选门槛。分母是每格有起点且能完整随访的比赛，不是该时段所有比赛。';
rs.forEach((r,i)=>{const tr=elem('tr');tr.id='region-'+i;const n=r.full_followup_matches,vals=['('+r.grid_x+','+r.grid_y+') / '+(i+1)+(r.review_candidate?' 候选':''),n+' / '+r.censored_matches_excluded_from_denominator,n?r.own_revisit_matches+'/'+n:'无完整随访',n?r.opponent_later_matches+'/'+n:'无完整随访',r.own_revisit_median_delay_s_conditional_on_observed===null?'未观察到':r.own_revisit_median_delay_s_conditional_on_observed.toFixed(1)+'秒','原眼仍在'+r.own_overlap_matches+' / 原眼结束后'+r.own_after_observed_end_matches+' / 结束未知'+r.own_unknown_end_matches];for(const v of vals)tr.append(elem('td',v));const td=elem('td'),detail=elem('details'),summary=elem('summary','查看'+r.seed_refs.length+'场起点');detail.append(summary);for(const ref of r.seed_refs)detail.append(elem('div',ref.match_id+' / ward_index='+ref.ward_index+' / '+clock(ref.placed_s)+' / '+(ref.full_horizon?'\u5b8c\u6574':'\u4e0d\u8db310\u5206\u949f')+' / \u672c\u65b9\u4e0b\u6b21 '+next(ref.next_own_placed_s,ref.next_own_delta_s,ref.next_own_index)+' / \u5bf9\u65b9\u4e0b\u6b21 '+next(ref.next_opponent_placed_s,ref.next_opponent_delta_s,ref.next_opponent_index)));td.append(detail);tr.append(td);body.append(tr);const bx=((8878-8356)/2+(r.grid_x*512-7487)/18322*8356)/8878*1600,by=(1-((r.grid_y+1)*512-7693)/18322)*8356/8878*1600,side=512/18322*8356/8878*1600,g=svg('a',{href:'#region-'+i});g.append(svg('rect',{x:bx,y:by,width:side,height:side,fill:r.review_candidate?'#f0c375':'#89acc0','fill-opacity':.25,stroke:r.team===2?'#70d59e':'#ee8888','stroke-width':2}));const text=svg('text',{x:bx+side/2,y:by+side/2,fill:'white',stroke:'black','stroke-width':3,'paint-order':'stroke','font-size':22});text.textContent=i+1;g.append(text);cells.append(g)});if(!rs.length)body.append(elem('tr','当前组合没有起始放眼记录。'))}
C.onchange=phases;P.onchange=K.onchange=T.onchange=draw;phases();</script>'''.replace('PAYLOAD', payload)
    (output / 'report.html').write_text(page, encoding='utf-8', newline='\n')
