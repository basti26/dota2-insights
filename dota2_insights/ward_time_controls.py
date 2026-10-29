"""Same-match/phase windows away from recorded targets, without causal claims."""
from __future__ import annotations

from collections import Counter,defaultdict
import json
from pathlib import Path
import sqlite3
from .postgame_metadata_store import context_sources as resolved_context_sources
from statistics import median

from .postgame_store import bounded,sha,number
from .ward_objective_opportunities import SCHEMA as OPPORTUNITIES,EPS,MIN_RADIUS
from .ward_patterns import COORDINATES,PHASE

from .postgame_context_cohort import context_key,VERSION as COHORT_VERSION,FIELDS as COHORT_FIELDS

SCHEMA='recorded_target_time_controls_v3'
SEED='recorded-target-time-controls-20261021-v1'
BUFFERS=(0,30,90)
STEP=15
CLOCK_GUARD=2


def analyze(database,opportunities_path,output,*,max_center_lag_s=None):
    if max_center_lag_s is not None and (not number(max_center_lag_s) or not 0<max_center_lag_s<=PHASE):
        raise ValueError('time_controls_center_lag_must_be_positive_at_most_one_phase')
    database,opportunities_path,output=map(lambda p:Path(p).resolve(),(database,opportunities_path,output))
    if output.exists():raise ValueError('time_controls_new_output_required')
    digest=sha(bounded(database,128*1024**2));raw=bounded(opportunities_path,16*1024**2);op=json.loads(raw)
    if (op.get('schema_version')!=OPPORTUNITIES or op.get('database_sha256')!=digest
            or op.get('protocol',{}).get('cohort_schema_version')!=COHORT_VERSION
            or op.get('protocol',{}).get('cohort_fields')!=list(COHORT_FIELDS)):
        raise ValueError('time_controls_frozen_opportunities_required')
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        matches={r['match_id']:dict(r) for r in c.execute('SELECT * FROM matches')}
        sources=resolved_context_sources(c)
        groups=op['groups'];by_match=defaultdict(list)
        if len(groups)>10_000 or len(matches)>256:raise ValueError('time_controls_input_budget')
        seen_groups=set()
        for g in groups:
            s=sources.get(g['match_id'])
            r,t=g['balanced_radius_s'],g['center_s']
            if (s is None or s['source_id']!=g['source_id'] or s['source_kind']!=g['source_kind']
                    or not number(r) or not number(t) or not 0<=r<=90+EPS
                    or g['summary_eligible']!=(r+EPS>=MIN_RADIUS) or g['shift_s']!=0 or t!=g['recorded_time_s']
                    or t-r< -EPS or t+r>matches[g['match_id']]['duration_s']+EPS
                    or g['group_id'] in seen_groups
                    or g['group_id']!=sha((g['match_id']+':'+s['source_id']+':'+float(t).hex()).encode())):
                raise ValueError('time_controls_primary_source_or_window_binding')
            seen_groups.add(g['group_id'])
            by_match[g['match_id']].append(g)
        if sum(len(v)*sum(g['summary_eligible'] for g in v) for v in by_match.values())*120>10_000_000:
            raise ValueError('time_controls_work_budget')
        output.mkdir(parents=True)
        protocol={'schema_version':SCHEMA,'cohort_schema_version':COHORT_VERSION,'cohort_fields':list(COHORT_FIELDS),'resolved_metadata_field_sources':{mid:s['metadata_field_sources'] for mid,s in sources.items()},'database_sha256':digest,'opportunities_sha256':sha(raw),'seed':SEED,
            'candidate_step_seconds':STEP,'recorded_target_buffer_seconds':list(BUFFERS),'clock_guard_seconds':CLOCK_GUARD,
            'maximum_abs_center_lag_s':max_center_lag_s,
            'eligible_target':'inherited_each_side_at_least30s','control':'same_match_same_target_center_10min_phase_exact_same_radius',
            'exclusion':'abs(control_center-target_center)>control_radius+max(target_balanced_radius,buffer)+2s_for_all_recorded_in_game_target_groups',
            'allocation':'fixed_hash_anchor_order_then_fixed_hash_candidate_order_greedy_disjoint_control_intervals_per_match_per_buffer',
            'unmatched':'no_initial_candidates_and_no_remaining_disjoint_candidate_are_distinct_not_proof_of_optimal_matching',
            'source_order':'persist_full_selection_before_reading_ward_rows_in_this_run_past_data_was_explored',
            'meaning':'away_from_recorded_targets_not_proof_of_event_free_play_or_causal_counterfactual'}
        (output/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8',newline='\n')
        selection=[]
        for buffer in BUFFERS:
            allocated=defaultdict(list)
            order=sorted(groups,key=lambda g:(sha((SEED+':anchor:'+g['group_id']).encode()),g['group_id']))
            for g in order:
                mid=g['match_id'];r=g['balanced_radius_s'];t=g['center_s'];phase=int(t//PHASE)
                row={'match_id':mid,'group_id':g['group_id'],'source_id':g['source_id'],'source_kind':g['source_kind'],
                    'source_group':matches[mid]['source_group'],'buffer_s':buffer,'phase':phase,'target_center_s':t,'radius_s':r,
                    'cohort':list(context_key(matches[mid],sources[mid])),'target_families':g['families'],
                    'target_owner_team_if_unambiguous':g['owner_team_if_unambiguous'],
                    'target_before_refs':g['before_refs'],'target_after_refs':g['after_refs'],'candidate_centers_s':[],
                    'control_center_s':None,'status':'short_target_not_eligible'}
                if g['summary_eligible']:
                    candidates=[]
                    for center in range(phase*PHASE,(phase+1)*PHASE,STEP):
                        if center-r<0 or center+r>matches[mid]['duration_s']:continue
                        if any(abs(center-other['center_s'])<=r+max(other['balanced_radius_s'],buffer)+CLOCK_GUARD+EPS for other in by_match[mid]):continue
                        candidates.append(float(center))
                    before_lag_limit=len(candidates)
                    if max_center_lag_s is not None:
                        candidates=[center for center in candidates if abs(center-t)<=max_center_lag_s+EPS]
                    candidates.sort(key=lambda center:(sha((SEED+':candidate:'+g['group_id']+':'+center.hex()).encode()),center))
                    remaining=[center for center in candidates if all(center+r<left-EPS or center-r>right+EPS for left,right,_ in allocated[mid])]
                    row.update(candidate_centers_s=candidates,remaining_disjoint_candidate_count=len(remaining),
                        status='no_candidates_within_center_lag' if not candidates and before_lag_limit else
                            'no_initial_candidates' if not candidates else 'no_disjoint_candidate_after_greedy' if not remaining else 'matched')
                    if max_center_lag_s is not None:
                        row['candidate_count_before_center_lag_limit']=before_lag_limit
                    if remaining:
                        center=remaining[0];row['control_center_s']=center;allocated[mid].append((center-r,center+r,g['group_id']))
                selection.append(row)
        path=output/'frozen-control-selection.json';path.write_text(json.dumps(selection,indent=2),encoding='utf-8',newline='\n');selection_digest=sha(path.read_bytes())
        # Positions and placement counts never influence the candidate allocation.
        wards=[dict(r) for r in c.execute("SELECT match_id,ward_index,team,ward_type,placed_s FROM wards WHERE placement_status='in_game' AND coordinate_basis=? ORDER BY match_id,placed_s,ward_index",(COORDINATES,))]
        if len(wards)>100_000:raise ValueError('time_controls_ward_budget')
    finally:c.close()
    lookup={(r['match_id'],r['ward_index']):r for r in wards};pairs=[]
    for s in selection:
        if s['status']!='matched':continue
        mid=s['match_id'];t=s['control_center_s'];radius=s['radius_s']
        control_before=[r for r in wards if r['match_id']==mid and t-radius+EPS<r['placed_s']<t-EPS]
        control_after=[r for r in wards if r['match_id']==mid and t+EPS<r['placed_s']<t+radius-EPS]
        equal=[r for r in wards if r['match_id']==mid and abs(r['placed_s']-t)<=EPS]
        target_before=[lookup[mid,index] for index in s['target_before_refs']]
        target_after=[lookup[mid,index] for index in s['target_after_refs']]
        slices=[]
        for team in (2,3):
            for kind in ('observer','sentry'):
                counts={name:sum(r['team']==team and r['ward_type']==kind for r in values) for name,values in
                    (('target_before',target_before),('target_after',target_after),('control_before',control_before),('control_after',control_after))}
                target_change=counts['target_after']-counts['target_before'];control_change=counts['control_after']-counts['control_before']
                slices.append({'team':team,'ward_type':kind,**counts,'target_count_change':target_change,'control_count_change':control_change,
                    'paired_change_difference':target_change-control_change,'paired_change_difference_per_minute':(target_change-control_change)*60/radius})
        pairs.append({**s,'control_lag_s':t-s['target_center_s'],'control_before_refs':[r['ward_index'] for r in control_before],'control_after_refs':[r['ward_index'] for r in control_after],
            'control_same_clock_refs_excluded':[r['ward_index'] for r in equal],'slices':slices})
    coverage=[]
    for buffer in BUFFERS:
        rows=[s for s in selection if s['buffer_s']==buffer];counts=Counter(r['status'] for r in rows)
        matched=[r for r in rows if r['status']=='matched']
        lags=[abs(r['control_center_s']-r['target_center_s']) for r in matched]
        coverage.append({'buffer_s':buffer,'eligible_target_groups':sum(r['status']!='short_target_not_eligible' for r in rows),
            'statuses':dict(counts),'matched_distinct_matches':len({r['match_id'] for r in matched}),
            'matched_matches_by_origin':dict(Counter(matches[mid]['source_group'] for mid in sorted({r['match_id'] for r in matched}))),
            'median_abs_center_lag_s':median(lags) if lags else None,'max_abs_center_lag_s':max(lags) if lags else None,
            'matched_target_group_refs':[r['group_id'] for r in matched]})
    if sha(bounded(database,128*1024**2))!=digest or sha(bounded(opportunities_path,16*1024**2))!=sha(raw) or sha(path.read_bytes())!=selection_digest:
        raise ValueError('time_controls_source_or_selection_changed')
    result={'schema_version':SCHEMA,'database_sha256':digest,'opportunities_sha256':sha(raw),'selection_sha256':selection_digest,
        'protocol':protocol,'coverage':coverage,'pairs':pairs,'allocation':selection,
        'limitations':['Exploratory controls use previously explored games, not independent validation.',
            'Targets can be missing from filtered sources; no event-free or calm-play claim is made.',
            'Same phase and duration do not balance gold, heroes, towers alive, kills, ward stock or player decisions.',
            'Same10min phase can still permit nearly10min center separation; lag is reported, not treated as balanced time.',
            'Greedy interval allocation is order-dependent and not a maximum feasible matching proof.',
            'Matched targets can differ from unmatched; all three buffers reuse the same games.',
            'Paired count-change differences are descriptive arithmetic, not treatment effects or tactical quality.']}
    (output/'time-controls.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return result


def render(result,output):
    payload=json.dumps(result,ensure_ascii=False).replace('<','\\u003c')
    page=r'''<!doctype html><meta charset="utf-8"><title>目标事件的同阶段时间对照</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}a{color:#8cd8c3}select{font:inherit;padding:5px;margin:6px}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #52677b;text-align:left}.warn{color:#f1cc87}.wrap{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<h1>同一局、同一阶段的时间对照</h1><p>为目标机会寻找同场、中心同属10分钟阶段、前后半径完全相同的对照窗口。候选中心每15秒一格，避开所有已记录游戏内目标窗口；对照之间也不重叠。先固定分配，再读放眼数量，本批资料此前已参与探索。</p>
<p class="warn">“避开已记录目标”不保证实际无目标或无交战。旧来源有过滤；经济、兵线、英雄、存活建筑、眼库存等条件没有匹配。以下只是配对计数变化，不能解释为目标带来的效果或战术质量。</p>
<label>额外目标保护范围<select id="buffer"><option value="0">0秒</option><option value="30">30秒</option><option value="90">90秒</option></select></label><label>比赛<select id="match"><option value="all">全部</option></select></label>
<p>保护半径取原目标窗口半径和上述范围的较大者，另加2秒时钟扰动参数；2秒不是已验证的误差上界。三个参数使用相同锚点顺序及候选偏好，仍是同一批比赛，不能当独立复现。</p>
<p id="coverage"></p><p>不匹配的目标不填0差异，也不与匹配成功目标混算。贪心分配后的无剩余窗口不证明数学上无法配对；未更换种子追求更多或更有利的结果。</p>
<h2>实际匹配的窗口</h2><table><thead><tr><th>比赛/来源</th><th>目标中心</th><th>对照中心</th><th>中心间隔秒（负=较早）</th><th>单侧秒数</th><th>目标类型</th></tr></thead><tbody id="pairs"></tbody></table>
<h2>同一组的原始放眼数</h2><p>“变化差”=(目标后−目标前)−(对照后−对照前)；每分钟值除以该组单侧分钟数。保留正、负、零，不作显著性或收益判断。</p><div class="wrap"><table><thead><tr><th>比赛/目标中心</th><th>阵营/眼型</th><th>目标前/后</th><th>对照前/后</th><th>变化差</th><th>变化差/分钟</th></tr></thead><tbody id="counts"></tbody></table></div>
<h2>未匹配的足长机会</h2><table><thead><tr><th>比赛/目标中心</th><th>单侧秒数</th><th>初始可用候选</th><th>保留原因</th></tr></thead><tbody id="unmatched"></tbody></table>
<details><summary>所选参数/比赛的分配、原始索引与来源</summary><pre id="refs"></pre></details>
<p><a href="frozen-control-selection.json">放眼读取前固定的全部分配</a> · <a href="time-controls.json">完整结果</a> · <a href="protocol.json">口径</a></p>
<script>const D=PAYLOAD,B=document.getElementById('buffer'),M=document.getElementById('match'),fam={tower_destroyed:'塔',barracks_destroyed:'兵营',ancient_destroyed:'遗迹',roshan_kill:'肉山记录',roshan_death_record:'肉山记录',other_building:'其他建筑'};function el(t,v){const e=document.createElement(t);if(v!==undefined)e.textContent=v;return e}function tr(body,values){const row=el('tr');for(const v of values)row.append(el('td',v));body.append(row)}function time(t){return Math.floor(t/60)+':'+(t%60).toFixed(1).padStart(4,'0')}
for(const mid of [...new Set(D.allocation.map(r=>r.match_id))].sort()){const o=el('option',mid);o.value=mid;M.append(o)}
function draw(){const eligible=D.allocation.filter(r=>r.buffer_s===+B.value&&(M.value==='all'||r.match_id===M.value)),pairs=D.pairs.filter(r=>r.buffer_s===+B.value&&(M.value==='all'||r.match_id===M.value));const n=eligible.filter(r=>r.status!=='short_target_not_eligible').length,short=eligible.length-n,none=eligible.filter(r=>r.status==='no_initial_candidates').length,allocated=eligible.filter(r=>r.status==='no_disjoint_candidate_after_greedy').length;const lags=pairs.map(p=>Math.abs(p.control_lag_s)).sort((a,b)=>a-b),med=lags.length?(lags[Math.floor((lags.length-1)/2)]+lags[Math.floor(lags.length/2)])/2:null;const lagExcluded=eligible.filter(r=>r.status==='no_candidates_within_center_lag').length;document.getElementById('coverage').textContent='中心间隔上限'+(D.protocol.maximum_abs_center_lag_s===null?'未限定':D.protocol.maximum_abs_center_lag_s+'秒')+'；匹配'+pairs.length+'/'+n+'个足长目标组，涉及'+new Set(pairs.map(r=>r.match_id)).size+'场；初始无候选'+none+'，仅中心间隔限制排除'+lagExcluded+'，分配后无剩余'+allocated+'；另有短窗'+short+'组不进分母。'+(n===0?'没有足长目标机会，不显示成零发生率。':'')+(lags.length?'中心绝对间隔中位'+med.toFixed(1)+'秒、最大'+lags[lags.length-1].toFixed(1)+'秒；阶段匹配仍很粗，不当成同时段条件相同。':'没有匹配，不计算中心间隔。');for(const id of['pairs','counts','unmatched'])document.getElementById(id).replaceChildren();for(const p of pairs){tr(document.getElementById('pairs'),[p.match_id+' / '+({own:'本人',d2pt:'高分来源',tier1:'Tier 1 职业'}[p.source_group]||p.source_group)+' / '+p.source_kind,time(p.target_center_s),time(p.control_center_s),p.control_lag_s.toFixed(1),p.radius_s.toFixed(3),p.target_families.map(f=>fam[f]||f).join(',')]);for(const s of p.slices)tr(document.getElementById('counts'),[p.match_id+' / '+time(p.target_center_s),(s.team===2?'天辉':'夜魇')+' / '+(s.ward_type==='observer'?'假眼':'真眼'),s.target_before+' / '+s.target_after,s.control_before+' / '+s.control_after,s.paired_change_difference,s.paired_change_difference_per_minute.toFixed(3)])}for(const r of eligible.filter(r=>r.status==='no_initial_candidates'||r.status==='no_candidates_within_center_lag'||r.status==='no_disjoint_candidate_after_greedy'))tr(document.getElementById('unmatched'),[r.match_id+' / '+time(r.target_center_s),r.radius_s.toFixed(3),r.candidate_centers_s.length,r.status==='no_initial_candidates'?'初始没有候选':r.status==='no_candidates_within_center_lag'?'中心间隔限制后无候选':'按固定顺序分配后无剩余']);document.getElementById('refs').textContent=JSON.stringify({allocation:eligible,pairs},null,2)}B.addEventListener('change',draw);M.addEventListener('change',draw);draw();</script>'''
    (Path(output)/'report.html').write_text(page.replace('PAYLOAD',payload),encoding='utf-8',newline='\n')
