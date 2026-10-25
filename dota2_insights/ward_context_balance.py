"""Retrospective time-distance sensitivity and pre-window DEM counter balance."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3

from .postgame_store import bounded, sha
from .ward_time_controls import analyze as controls, render as render_controls

SCHEMA = 'ward_pre_window_context_balance_v1'
LAG_LIMITS = (None, 60, 120, 300)
MAX_AGE = 90


def checkpoint(rows, source, start):
    """Last entire source packet strictly before the window, with no fallback."""
    result = {'requested_window_start_s': start, 'source_id': source['source_id'],
              'status': 'net_worth_not_recorded_for_source', 'packet_ref': None,
              'raw_team_counter_sums': None, 'radiant_minus_dire_raw': None,
              'players': []}
    if source['source_kind'] != 'replay_capture':
        return result
    candidates = [r for r in rows if r['time_s'] < start and r['phase'] == 'in_game']
    if not candidates:
        result['status'] = 'no_prior_in_game_packet'
        return result
    row = max(candidates, key=lambda r: (r['time_s'], r['packet_tick'], r['packet_index']))
    if sha(row['packet_json']) != row['packet_sha256']:
        raise ValueError('balance_packet_digest_mismatch')
    packet = json.loads(row['packet_json'])
    if (packet['packet_tick'] != row['packet_tick'] or packet['game_time_s'] != row['time_s']
            or row['source_id'] != source['source_id']):
        raise ValueError('balance_packet_source_or_clock_mismatch')
    age = start - row['time_s']
    result['packet_ref'] = {k: row[k] for k in
                            ('source_id', 'packet_index', 'packet_tick', 'time_s', 'packet_sha256')}
    result['sample_age_s'] = age
    if age > MAX_AGE:
        result['status'] = 'prior_packet_too_old'
        return result
    players = packet['players']
    valid = (row['mapping_resolved'] == 1 and packet['resource_mapping_resolved'] is True
             and len(players) == 10 and {p['logical_player_id'] for p in players} == set(range(10))
             and {(p['team'], p['team_slot']) for p in players}
                 == {(t, slot) for t in (2, 3) for slot in range(5)}
             and len({p['resource_index'] for p in players}) == 10
             and all(type(p['resource_index']) is int and p['resource_index'] >= 0 for p in players))
    if not valid:
        result['status'] = 'player_resource_mapping_incomplete'
        return result
    result['players'] = [{k: p[k] for k in
                          ('logical_player_id', 'team', 'team_slot', 'resource_index', 'hero_id')}
                         | {'net_worth_raw': p['metrics'].get('net_worth')} for p in players]
    if any(type(p['net_worth_raw']) is not int or p['net_worth_raw'] < 0 for p in result['players']):
        result['status'] = 'net_worth_field_missing_or_invalid'
        return result
    sums = {str(t): sum(p['net_worth_raw'] for p in result['players'] if p['team'] == t) for t in (2, 3)}
    result.update(status='reported_raw_team_counters', raw_team_counter_sums=sums,
                  radiant_minus_dire_raw=sums['2']-sums['3'])
    return result


def compare(left, right):
    if left['status'] != 'reported_raw_team_counters' or right['status'] != 'reported_raw_team_counters':
        return {'status': 'missing_or_unusable_checkpoint', 'raw_gap_difference': None}
    identity = lambda r: sorted(tuple(p[k] for k in ('logical_player_id', 'team', 'team_slot', 'resource_index', 'hero_id'))
                                for p in r['players'])
    if (left['source_id'] != right['source_id'] or identity(left) != identity(right)
            or any(p['hero_id'] is None for r in (left, right) for p in r['players'])):
        return {'status': 'player_identity_not_comparable', 'raw_gap_difference': None}
    return {'status': 'same_source_same_mapping_raw_counter_comparison',
            'raw_gap_difference': left['radiant_minus_dire_raw'] - right['radiant_minus_dire_raw'],
            'same_packet_used_for_both_windows': left['packet_ref']['packet_index'] == right['packet_ref']['packet_index'],
            'team_sum_differences': {str(t): left['raw_team_counter_sums'][str(t)]-right['raw_team_counter_sums'][str(t)]
                                     for t in (2, 3)}}


def analyze(database, opportunities_path, output):
    database, opportunities_path, output = map(lambda p: Path(p).resolve(), (database, opportunities_path, output))
    if output.exists():
        raise ValueError('context_balance_new_output_required')
    digest = sha(bounded(database, 128*1024**2))
    output.mkdir(parents=True)
    # Freeze all four allocations before inspecting economic values. The hash
    # preference stays the same; capped runs reallocate rather than drop pairs.
    runs = []
    for limit in LAG_LIMITS:
        directory = output/('lag-unlimited' if limit is None else f'lag-{limit}')
        result = controls(database, opportunities_path, directory, max_center_lag_s=limit)
        render_controls(result, directory)
        runs.append(result)
    plan = {'schema_version': SCHEMA, 'database_sha256': digest,
            'lag_limits_s': list(LAG_LIMITS), 'maximum_prior_packet_age_s': MAX_AGE,
            'checkpoint': 'latest_entire_in_game_packet_strictly_before_each_window_start_no_interpolation_or_invalid_fallback',
            'net_worth_basis': 'sum_of_five_m_iNetWorth_raw_team_counters_not_API_gold_t_or_GSI_revalidation',
            'selection': 'four_time_only_allocations_frozen_before_economic_reads_in_this_run_previous_data_explored',
            'same_time_packets': 'larger_packet_tick_then_packet_index',
            'economic_matching': False,
            'allocations': [{'limit_s': limit, 'selection_sha256': run['selection_sha256']}
                            for limit, run in zip(LAG_LIMITS, runs)]}
    (output/'protocol.json').write_text(json.dumps(plan, indent=2), encoding='utf-8', newline='\n')
    c = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        sources = {r['match_id']: dict(r) for r in c.execute('SELECT * FROM context_sources')}
        packets = defaultdict(list)
        rows = c.execute('''SELECT p.* FROM replay_metric_packets p JOIN context_sources s USING(source_id)
                            WHERE s.source_kind='replay_capture' ORDER BY p.source_id,p.time_s,p.packet_tick,p.packet_index''').fetchall()
        if len(rows) > 100_000:
            raise ValueError('context_balance_metric_budget')
        for row in rows:
            packets[row['source_id']].append(dict(row))
    finally:
        c.close()
    records, coverage = [], []
    baseline = {(r['group_id'],r['buffer_s']):r for r in runs[0]['allocation']}
    for limit, run in zip(LAG_LIMITS, runs):
        for row in run['allocation']:
            if row['status'] == 'short_target_not_eligible':
                continue
            source = sources[row['match_id']]
            reference = baseline[row['group_id'],row['buffer_s']]
            changed = ('same_match_same_center' if row['status']=='matched' and reference['status']=='matched'
                        and row['control_center_s']==reference['control_center_s'] else
                       'same_target_different_control' if row['status']=='matched' and reference['status']=='matched' else
                       'gained_matched_target' if row['status']=='matched' else
                       'lost_matched_target' if reference['status']=='matched' else 'both_unmatched')
            target = checkpoint(packets[source['source_id']], source, row['target_center_s']-row['radius_s'])
            control = (checkpoint(packets[source['source_id']], source, row['control_center_s']-row['radius_s'])
                       if row['status'] == 'matched' else None)
            records.append({**row, 'maximum_center_lag_s': limit,
                            'unlimited_status':reference['status'],
                            'unlimited_control_center_s':reference['control_center_s'],
                            'change_from_unlimited':changed,
                            'control_window_starts_after_target_event':
                                row['control_center_s']-row['radius_s']>row['target_center_s'] if control is not None else None,
                            'control_sample_at_or_after_target_event':
                                control['packet_ref']['time_s']>=row['target_center_s']
                                if control is not None and control['packet_ref'] is not None else None,
                            'target_checkpoint': target, 'control_checkpoint': control,
                            'economic_comparison': compare(target, control) if control is not None else
                                {'status': 'no_matched_control', 'raw_gap_difference': None}})
        for buffer in (0, 30, 90):
            selected = [r for r in records if r['maximum_center_lag_s'] == limit and r['buffer_s'] == buffer]
            base = next(v for v in run['coverage'] if v['buffer_s'] == buffer)
            coverage.append({'maximum_center_lag_s': limit, **base,
                             'changes_from_unlimited':dict(Counter(r['change_from_unlimited'] for r in selected)),
                             'target_checkpoint_statuses': dict(Counter(r['target_checkpoint']['status'] for r in selected)),
                             'matched_comparison_statuses': dict(Counter(r['economic_comparison']['status'] for r in selected if r['status']=='matched'))})
    if sha(bounded(database, 128*1024**2)) != digest:
        raise ValueError('context_balance_source_changed')
    result = {'schema_version': SCHEMA, 'database_sha256': digest, 'protocol': plan,
              'coverage': coverage, 'records': records,
              'limitations': ['Time caps use the same explored games and are sensitivity parameters, not new validation.',
                              'No economic balancing, causal estimate or tactical verdict; raw team counters need wider semantic checks.',
                              'Missing API/legacy net worth is not zero; gold_t is deliberately not substituted.',
                              'Last pre-window packet can be up to90s old; two windows can select the same packet.',
                              'Later controls can include the target event aftermath in their pre-window counters; not pre-treatment covariates.',
                              'Recorded target exclusions do not remove all fights, map pressure or unrecorded objectives.',
                              'More restrictive caps can leave no disjoint controls by construction; all failures retained.']}
    (output/'context-balance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    return result


def render(result, output):
    payload = json.dumps(result, ensure_ascii=False).replace('<', '\\u003c')
    page = r'''<!doctype html><meta charset="utf-8"><title>时间间隔与窗口前经济记录</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}a{color:#8cd8c3}select{font:inherit;padding:6px;margin:5px}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #52677b;text-align:left}.warn{color:#f1cc87}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<h1>时间间隔与窗口前经济记录</h1><p>相同比赛、10分钟阶段、窗口时长、固定分配顺序；分别限制目标与对照中心相隔60、120、300秒或不限定，每档重新分配。窗口之间及已记录目标的保护要求保持。时间相近不等于其他条件相同。</p>
<p class="warn">不依据眼位或经济选配对。经济只列各窗口开始前、最多90秒旧的同源原始m_iNetWorth计数；五人齐全才相加。API金币曲线不是此字段，旧来源缺失不补零。全知录像数据仅供赛后分析，未作因果或战术评价。较晚对照的经济可能已经包含目标事件影响，不能当作目标发生前的条件平衡。</p>
<h2>限制更严后还剩多少可比较窗口</h2><table><thead><tr><th>中心间隔上限 / 保护秒</th><th>匹配/足长 / 比赛</th><th>初始无 / 间隔排除 / 分配无剩余</th><th>短窗不入分母</th><th>中位 / 最大中心差秒</th><th>相对未限距：保留原对照/换对照/新增/失去</th><th>匹配对中原始经济可比较</th></tr></thead><tbody id="coverage"></tbody></table>
<h2>逐组配对及缺失记录</h2><label>中心间隔<select id="lag"><option value="none">未限定</option><option>60</option><option>120</option><option>300</option></select></label><label>目标保护<select id="buffer"><option>0</option><option>30</option><option>90</option></select></label><label>比赛<select id="match"><option value="all">全部</option></select></label><p id="status"></p>
<table><thead><tr><th>比赛 / 构建</th><th>目标 / 对照中心</th><th>状态 / 单侧秒</th><th>目标窗前天辉-夜魇原始计数差 / 样本年龄</th><th>对照窗前差 / 样本年龄</th><th>两差相减 / 可比状态</th></tr></thead><tbody id="records"></tbody></table>
<details><summary>所选逐组原始值、采样包与来源引用</summary><pre id="refs"></pre></details><p><a href="context-balance.json">完整数据</a> · <a href="protocol.json">时间与字段口径</a></p>
<script>const D=PAYLOAD,L=document.getElementById('lag'),B=document.getElementById('buffer'),M=document.getElementById('match');const label=v=>v===null?'未限定':v+'秒',stat={matched:'已匹配',no_initial_candidates:'原先无候选',no_candidates_within_center_lag:'间隔限制无候选',no_disjoint_candidate_after_greedy:'分配后无剩余'},econ={reported_raw_team_counters:'已记录原计数',net_worth_not_recorded_for_source:'来源未采此字段',no_prior_in_game_packet:'窗口前无采样',prior_packet_too_old:'窗口前采样超龄',player_resource_mapping_incomplete:'映射不完整',net_worth_field_missing_or_invalid:'五人值不全',missing_or_unusable_checkpoint:'一侧或两侧数据缺失',player_identity_not_comparable:'英雄身份不可比较',no_matched_control:'没有匹配对照',same_source_same_mapping_raw_counter_comparison:'同源同映射原计数比较'};function tr(id,values){const r=document.createElement('tr');for(const v of values){const c=document.createElement('td');c.textContent=v;r.append(c)}document.getElementById(id).append(r)}const time=v=>v===null?'—':Math.floor(v/60)+':'+(v%60).toFixed(1).padStart(4,'0');const num=v=>v===null||v===undefined?'—':v.toFixed(1);function cp(v){return v===null?'无对照':v.status==='reported_raw_team_counters'?v.radiant_minus_dire_raw+' / '+v.sample_age_s.toFixed(1)+'秒':econ[v.status]||v.status}
for(const c of D.coverage)tr('coverage',[label(c.maximum_center_lag_s)+' / '+c.buffer_s,(c.statuses.matched||0)+'/'+c.eligible_target_groups+' / '+c.matched_distinct_matches,(c.statuses.no_initial_candidates||0)+' / '+(c.statuses.no_candidates_within_center_lag||0)+' / '+(c.statuses.no_disjoint_candidate_after_greedy||0),c.statuses.short_target_not_eligible||0,num(c.median_abs_center_lag_s)+' / '+num(c.max_abs_center_lag_s),['same_match_same_center','same_target_different_control','gained_matched_target','lost_matched_target'].map(k=>c.changes_from_unlimited[k]||0).join(' / '),c.matched_comparison_statuses.same_source_same_mapping_raw_counter_comparison||0]);for(const mid of [...new Set(D.records.map(r=>r.match_id))].sort()){const o=document.createElement('option');o.value=mid;o.textContent=mid;M.append(o)}function draw(){const limit=L.value==='none'?null:+L.value,rows=D.records.filter(r=>r.maximum_center_lag_s===limit&&r.buffer_s===+B.value&&(M.value==='all'||r.match_id===M.value));document.getElementById('records').replaceChildren();document.getElementById('status').textContent=rows.length+'个足长目标组，匹配'+rows.filter(r=>r.status==='matched').length+'组；不匹配及缺经济均保留，未做经济平衡。';for(const r of rows)tr('records',[r.match_id+' / '+r.cohort[6],time(r.target_center_s)+' / '+time(r.control_center_s),(stat[r.status]||r.status)+' / '+r.radius_s.toFixed(3),cp(r.target_checkpoint),cp(r.control_checkpoint),String(r.economic_comparison.raw_gap_difference??'—')+' / '+econ[r.economic_comparison.status]]);document.getElementById('refs').textContent=JSON.stringify(rows,null,2)}for(const s of [L,B,M])s.addEventListener('change',draw);draw();</script>'''
    (Path(output)/'report.html').write_text(page.replace('PAYLOAD', payload), encoding='utf-8', newline='\n')
