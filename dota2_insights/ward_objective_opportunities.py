"""Recorded-objective opportunities with disjoint symmetric ward windows."""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter,defaultdict
import json
import math
from pathlib import Path
import sqlite3
from .postgame_metadata_store import context_sources as resolved_context_sources

from .postgame_store import VERSION,bounded,sha
from .postgame_event_types import TARGET_FAMILIES
from .ward_patterns import CELL,COORDINATES,PHASE

from .postgame_context_cohort import context_key,VERSION as COHORT_VERSION,FIELDS as COHORT_FIELDS

SCHEMA='recorded_objective_ward_opportunities_v2'
SPAN=90.0
MIN_RADIUS=30.0
EPS=1e-7


def relation(owner,team):
    return 'own_building' if owner==team else 'opponent_building' if owner in (2,3) else 'mixed_or_unknown'


def analyze(database,output):
    database,output=Path(database).resolve(),Path(output).resolve()
    if output.exists():raise ValueError('objective_opportunities_new_output_required')
    digest=sha(bounded(database,128*1024**2))
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        meta=dict(c.execute('SELECT key,value FROM metadata'))
        if meta.get('schema_version')!=VERSION or meta.get('context_extension_version')!='postgame_context_extension_v1':
            raise ValueError('objective_opportunities_context_facts_required')
        matches={r['match_id']:dict(r) for r in c.execute('SELECT * FROM matches')}
        sources=resolved_context_sources(c)
        events=[dict(r) for r in c.execute('''SELECT e.*,r.source_id,r.family FROM events e
            JOIN context_event_refs r USING(match_id,event_kind,event_index)
            WHERE r.phase='in_game' ORDER BY e.match_id,e.time_s,e.event_kind,e.event_index''')
            if r['family'] in TARGET_FAMILIES]
        wards=[dict(r) for r in c.execute("SELECT match_id,ward_index,ward_type,team,x,y,placed_s FROM wards WHERE placement_status='in_game' AND coordinate_basis=? ORDER BY match_id,placed_s,ward_index",(COORDINATES,))]
    finally:c.close()
    if len(matches)>256 or len(events)>10_000 or len(wards)>100_000:
        raise ValueError('objective_opportunities_row_budget')
    output.mkdir(parents=True)
    protocol={'schema_version':SCHEMA,'cohort_schema_version':COHORT_VERSION,'cohort_fields':list(COHORT_FIELDS),'resolved_metadata_field_sources':{mid:s['metadata_field_sources'] for mid,s in sources.items()},'database_sha256':digest,'fixed_radius_s':SPAN,
        'minimum_each_side_exposure_s_for_summary':MIN_RADIUS,'clock_shifts_s':[-2,0,2],
        'grouping':'same_source_match_exact_recorded_timestamp_not_claimed_simultaneous_in_game',
        'balanced_radius':'min(90,time_from_start,time_to_end,half_previous_gap,half_next_gap)',
        'allocation':'unique_nearest_recorded_target_group_within_open_symmetric_window',
        'comparison_epsilon_s':EPS,
        'ties':'equal_distance_same_clock_and_outer_boundaries_excluded_and_preserved',
        'summary':'per_match_rates_then_equal_match_macro_mean_not_independent_event_sample',
        'opportunity':'recorded_target_group_with_at_least30s_each_side_including_zero_ward_records',
        'target_families':sorted(TARGET_FAMILIES),'phase_basis':'target_group_center_not_placement_time'}
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8',newline='\n')
    by_match_events=defaultdict(list);by_match_wards=defaultdict(list)
    for row in events:by_match_events[row['match_id']].append(row)
    for row in wards:by_match_wards[row['match_id']].append(row)
    all_groups={};coverage=[]
    for mid,match in matches.items():
        by_time=defaultdict(list)
        for event in by_match_events[mid]:by_time[event['time_s']].append(event)
        groups=[]
        for stamp,rows in sorted(by_time.items()):
            owners=[]
            for row in rows:
                payload=json.loads(row['payload']);name=str(payload.get('key',payload.get('target','')))
                owners.append(2 if name.startswith('npc_dota_goodguys_') else 3 if name.startswith('npc_dota_badguys_') else None)
            groups.append({'group_id':sha((mid+':'+rows[0]['source_id']+':'+float(stamp).hex()).encode()),
                'match_id':mid,'recorded_time_s':stamp,'source_id':rows[0]['source_id'],
                'source_kind':sources[mid]['source_kind'],'families':sorted({r['family'] for r in rows}),
                'owner_team_if_unambiguous':owners[0] if owners and owners[0] in (2,3) and len(set(owners))==1 else None,
                'event_refs':[{'event_kind':r['event_kind'],'event_index':r['event_index'],'family':r['family'],
                    'time_basis':r['time_basis'],'payload':json.loads(r['payload'])} for r in rows]})
        if groups:all_groups[mid]=groups
        coverage.append({'match_id':mid,'source_kind':sources[mid]['source_kind'] if mid in sources else None,
            'recorded_target_events':len(by_match_events[mid]),'timestamp_groups':len(groups),
            'status':'recorded_targets_available_not_complete' if groups else
                'context_has_no_recorded_targets_not_proof_of_no_targets' if mid in sources else 'context_not_recorded'})

    def allocate(shift):
        opportunities=[];decisions=[];summary=Counter();fixed_refs=Counter()
        for mid,original in all_groups.items():
            end=matches[mid]['duration_s'];groups=[]
            times=[r['recorded_time_s']+shift for r in original]
            for index,row in enumerate(original):
                stamp=times[index]
                left=(stamp-times[index-1])/2 if index else math.inf
                right=(times[index+1]-stamp)/2 if index+1<len(times) else math.inf
                radius=max(0,min(SPAN,stamp,end-stamp,left,right))
                groups.append({**row,'shift_s':shift,'center_s':stamp,'balanced_radius_s':radius,
                    'summary_eligible':radius+EPS>=MIN_RADIUS,'before_refs':[],'after_refs':[],
                    'fixed_window_neighbor_groups':sum(j!=index and abs(t-stamp)<2*SPAN for j,t in enumerate(times))})
            for ward in by_match_wards[mid]:
                stamp=ward['placed_s'];fixed=[i for i,t in enumerate(times) if abs(t-stamp)<=SPAN+EPS]
                if fixed:
                    fixed_refs['ward_records_in_any_fixed_window']+=1
                    fixed_refs['fixed_timestamp_group_links']+=len(fixed)
                    fixed_refs['fixed_event_links']+=sum(len(groups[i]['event_refs']) for i in fixed)
                    fixed_refs['ward_records_shared_by_timestamp_windows']+=len(fixed)>1
                    fixed_refs['ward_records_shared_by_event_windows']+=sum(len(groups[i]['event_refs']) for i in fixed)>1
                pos=bisect_left(times,stamp);candidates=[i for i in (pos-1,pos) if 0<=i<len(times)]
                nearest=min((abs(times[i]-stamp) for i in candidates),default=math.inf)
                best=[i for i in candidates if abs(abs(times[i]-stamp)-nearest)<=EPS]
                group=groups[best[0]] if len(best)==1 else None
                reason=('outside_fixed_window' if nearest>SPAN+EPS else 'equal_distance_groups' if len(best)!=1 else
                    'same_clock' if nearest<=EPS else 'balanced_outer_boundary' if abs(nearest-group['balanced_radius_s'])<=EPS else
                    'outside_balanced_window' if nearest>group['balanced_radius_s'] else 'assigned')
                decision={'match_id':mid,'ward_index':ward['ward_index'],'time_s':stamp,
                    'team':ward['team'],'ward_type':ward['ward_type'],'x':ward['x'],'y':ward['y'],
                    'fixed_group_count':len(fixed),'decision':reason,
                    'nearest_group_ids':[groups[i]['group_id'] for i in best] if nearest<=SPAN+EPS else [],
                    'assigned_group_id':group['group_id'] if reason=='assigned' else None}
                if reason=='assigned':
                    side='before' if stamp<group['center_s'] else 'after'
                    group[side+'_refs'].append(ward['ward_index']);decision['side']=side
                    decision['summary_eligible']=group['summary_eligible']
                    summary['assigned_to_summary_eligible_groups' if group['summary_eligible'] else 'assigned_to_short_groups_not_summarized']+=1
                summary[reason]+=1;decisions.append(decision)
            opportunities.extend(groups)
        return opportunities,decisions,{'shift_s':shift,**dict(summary),**dict(fixed_refs)}

    primary_groups,primary_decisions,primary_summary=allocate(0)
    alternatives=[];base={(r['match_id'],r['ward_index']):(r['assigned_group_id'],r.get('side'),r.get('summary_eligible')) for r in primary_decisions}
    for shift in (-2,2):
        groups,decisions,summary=allocate(shift)
        changed=[{'match_id':r['match_id'],'ward_index':r['ward_index'],'decision':r['decision']}
            for r in decisions if base[r['match_id'],r['ward_index']]!=(r['assigned_group_id'],r.get('side'),r.get('summary_eligible'))]
        alternatives.append({'summary':summary,'assignment_changed_count':len(changed),'changed_refs':changed,
            'group_exposure':[{'group_id':r['group_id'],'radius_s':r['balanced_radius_s'],'summary_eligible':r['summary_eligible']} for r in groups]})
    lookup={(r['match_id'],r['ward_index']):r for r in wards}
    per_match=defaultdict(lambda:{'before':0,'after':0,'exposure_each_side_s':0,'groups':[],
                                 'zero_placement_groups':0,'before_cells':set(),'after_cells':set()})
    for group in primary_groups:
        if not group['summary_eligible']:continue
        mid=group['match_id'];cohort=context_key(matches[mid],sources[mid]);phase=int(group['center_s']//PHASE)
        for team in (2,3):
            for kind in ('observer','sentry'):
                bucket=(cohort,phase,team,kind,relation(group['owner_team_if_unambiguous'],team),mid)
                totals=per_match[bucket];totals['exposure_each_side_s']+=group['balanced_radius_s'];totals['groups'].append(group['group_id'])
                counts={}
                for side in ('before','after'):
                    rows=[lookup[mid,index] for index in group[side+'_refs'] if lookup[mid,index]['team']==team and lookup[mid,index]['ward_type']==kind]
                    totals[side]+=len(rows);counts[side]=len(rows)
                    totals[side+'_cells'].update((math.floor(r['x']/CELL),math.floor(r['y']/CELL)) for r in rows)
                totals['zero_placement_groups']+=counts['before']==counts['after']==0
    rows=[]
    for (cohort,phase,team,kind,relative,mid),total in sorted(per_match.items()):
        rows.append({'cohort':list(cohort),'phase':phase,'team':team,'ward_type':kind,'target_relation':relative,'match_id':mid,
            **{k:v for k,v in total.items() if not isinstance(v,set)},
            'before_cells':sorted(total['before_cells']),'after_cells':sorted(total['after_cells']),
            'before_recorded_placements_per_minute':total['before']/total['exposure_each_side_s']*60,
            'after_recorded_placements_per_minute':total['after']/total['exposure_each_side_s']*60})
    buckets=defaultdict(list)
    for row in rows:buckets[(tuple(row['cohort']),row['phase'],row['team'],row['ward_type'],row['target_relation'])].append(row)
    macro=[]
    for bucket,values in sorted(buckets.items()):
        cohort,phase,team,kind,relative=bucket
        macro.append({'cohort':list(cohort),'phase':phase,'team':team,'ward_type':kind,'target_relation':relative,
            'distinct_matches':len(values),'recorded_target_groups':sum(len(v['groups']) for v in values),
            'before_macro_rate':sum(v['before_recorded_placements_per_minute'] for v in values)/len(values),
            'after_macro_rate':sum(v['after_recorded_placements_per_minute'] for v in values)/len(values),
            'match_refs':[v['match_id'] for v in values]})
    if sha(bounded(database,128*1024**2))!=digest:raise ValueError('objective_opportunities_source_changed')
    result={'schema_version':SCHEMA,'database_sha256':digest,'protocol':protocol,'source_coverage':coverage,
        'recorded_target_events':len(events),'timestamp_groups':len(primary_groups),
        'summary_eligible_groups':sum(r['summary_eligible'] for r in primary_groups),
        'primary_summary':primary_summary,'clock_sensitivity':alternatives,
        'groups':primary_groups,'ward_decisions':primary_decisions,'per_match_rates':rows,'macro_rates':macro,
        'limitations':['Recorded targets are incomplete in some sources; no unseen event isolation is asserted.',
            'Same recorded timestamp does not prove true simultaneity; clocks remain source-specific.',
            'Removing overlap also changes exposure; differences are not only duplicate removal.',
            'Minimum radius selects more isolated recorded targets; dense pushes can have no eligible groups.',
            'Rates are recorded placement density, not active vision, ward quality or tactical causation.',
            'Source/parser/map/mode groups remain separate; phase follows target time, not placement time.',
            'Global +/-2s is a sensitivity parameter, not a verified clock-error bound.']}
    (output/'opportunities.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return result


def render(result,output,map_path):
    output=Path(output);raw=bounded(map_path,12*1024**2)
    if sha(raw)!='9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('objective_opportunities_pinned_map_required')
    (output/'map.jpg').write_bytes(raw)
    payload=json.dumps(result,ensure_ascii=False).replace('<','\\u003c')
    page=r'''<!doctype html><meta charset="utf-8"><title>目标窗口重叠与放眼机会</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}a{color:#8cd8c3}select{font:inherit;padding:5px;margin:6px;max-width:95vw}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #52677b}pre{white-space:pre-wrap;overflow-wrap:anywhere}.warn{color:#f1cc87}.scroll{max-height:65vh;overflow:auto}svg{width:100%;min-width:950px}</style>
<h1>目标事件前后的放眼：重叠与机会分母</h1>
<p id="totals"></p><p class="warn">固定窗口关联次数不是独立放眼数。新口径也缩短了观察时长，不能把两份数量之差全称为重复。这里记录放置密度，不代表实际视野、眼位质量或目标因果。</p>
<p>按同一来源、同一比赛、完全相同记录时间分组；相同记录时间不保证游戏内同时发生。每组前后等长，半径取90秒、距开局/结束、前后相邻目标间距的一半中的最小值。边界、等距和同刻记录排除并保留；每侧不足30秒不进汇总。未知/未导出目标无法排除。30秒门槛会偏向间隔较大的目标，连续推塔可能整局没有足长机会，不能推广到所有目标事件。</p>
<label>比赛<select id="match"></select></label><label>目标组<select id="group"></select></label><label>放置方<select id="team"><option value="all">双方</option><option value="2">天辉</option><option value="3">夜魇</option></select></label><label>眼型<select id="kind"><option value="all">全部</option><option value="observer">假眼</option><option value="sentry">真眼</option></select></label>
<p id="info"></p><p id="count"></p><p>蓝圈=事件前，金圈=事件后；假眼实线、真眼虚线，数字为该局 ward_index。没有圈只表示此窗口没有相应留存放眼。底图为示意，未逐局校准地形。</p>
<div class="scroll"><svg viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/><g id="points"></g></svg></div>
<details><summary>本组事件与分配/排除记录</summary><pre id="refs"></pre></details>
<h2>选中比赛的等长机会统计</h2><p>每行先累计本局同一时段、眼型、放置方和建筑归属的记录及暴露时长。零放眼机会仍进分母，目标时刻决定时段；事件前后可以跨10分钟边界。原始事件缺失不补成零目标。</p>
<table><thead><tr><th>时段/放置方/眼型/目标归属</th><th>组数/双侧零记录组</th><th>每侧暴露秒</th><th>前/后放置数</th><th>前/后每分钟</th></tr></thead><tbody id="rates"></tbody></table>
<h2>同来源、同提取批次与版本的逐局等权汇总</h2><p class="warn">当前最多只有少量比赛。以下保留所有零值、较低结果和来源差异，只作数据浏览，不得据此判定哪个时段应放眼。</p>
<table><thead><tr><th>来源/批次/补丁/地图/模式</th><th>时段/阵营/眼型/归属</th><th>不同比赛/目标组</th><th>前/后每分钟（逐局均值）</th><th>比赛引用</th></tr></thead><tbody id="macro"></tbody></table>
<h2>时钟敏感性</h2><p id="clock"></p><p>±2秒只是一组预设扰动，不是已经证实的时钟误差范围；完整归属与暴露变化见JSON。</p>
<details><summary>107场上下文覆盖</summary><pre id="coverage"></pre></details>
<p><a href="opportunities.json">完整结果与逐条引用</a> · <a href="protocol.json">固定口径</a></p>
<script>const D=PAYLOAD,M=document.getElementById('match'),G=document.getElementById('group'),T=document.getElementById('team'),K=document.getElementById('kind'),ns='http://www.w3.org/2000/svg';
const sources={legacy_archive:'旧DEM（过滤覆盖）',api_projection:'API投影',replay_capture:'同遍采集'},teams={2:'天辉',3:'夜魇'},kinds={observer:'假眼',sentry:'真眼'},rel={own_building:'本方建筑',opponent_building:'对方建筑',mixed_or_unknown:'混合/归属未知'};
const families={tower_destroyed:'防御塔摧毁',barracks_destroyed:'兵营摧毁',ancient_destroyed:'遗迹摧毁',other_building:'其他建筑',roshan_kill:'肉山击杀记录',roshan_death_record:'肉山死亡记录'};
function el(t,s){const e=document.createElement(t);if(s!==undefined)e.textContent=s;return e}function node(t,a){const e=document.createElementNS(ns,t);for(const[k,v]of Object.entries(a))e.setAttribute(k,v);return e}function time(t){return Math.floor(t/60)+':'+(t%60).toFixed(1).padStart(4,'0')}function tr(body,values){const r=el('tr');for(const v of values)r.append(el('td',v));body.append(r)}
const S=D.primary_summary;document.getElementById('totals').textContent=D.recorded_target_events+'条目标事件 / '+D.timestamp_groups+'个记录时间组；固定窗口'+S.fixed_event_links+'次关联涉及'+S.ward_records_in_any_fixed_window+'条放眼，其中'+S.ward_records_shared_by_event_windows+'条被多个事件覆盖。对称机会达到每侧30秒：'+D.summary_eligible_groups+'组，分配'+S.assigned_to_summary_eligible_groups+'条放眼。';
for(const mid of [...new Set(D.groups.map(g=>g.match_id))]){const o=el('option',mid+' / '+sources[D.groups.find(g=>g.match_id===mid).source_kind]);o.value=mid;M.append(o)}
function groups(){G.replaceChildren();for(const g of D.groups.filter(g=>g.match_id===M.value)){const o=el('option',time(g.center_s)+' / '+g.families.map(f=>families[f]||f).join(',')+' / 每侧'+g.balanced_radius_s.toFixed(1)+'秒'+(g.summary_eligible?'':'（短窗不汇总）'));o.value=g.group_id;G.append(o)}draw()}
function label(r){return r.phase*10+'～'+(r.phase+1)*10+'分 / '+teams[r.team]+' / '+kinds[r.ward_type]+' / '+rel[r.target_relation]}
function draw(){const g=D.groups.find(x=>x.group_id===G.value),points=document.getElementById('points');points.replaceChildren();document.getElementById('info').textContent='比赛'+g.match_id+'；'+sources[g.source_kind]+'；目标'+time(g.center_s)+'，每侧'+g.balanced_radius_s.toFixed(3)+'秒；'+(g.summary_eligible?'进入等长机会汇总':'短窗口保留明细，不进入汇总')+'；固定90秒窗相邻目标组'+g.fixed_window_neighbor_groups+'个。';
const rows=D.ward_decisions.filter(r=>r.assigned_group_id===g.group_id&&(T.value==='all'||r.team===+T.value)&&(K.value==='all'||r.ward_type===K.value));document.getElementById('count').textContent='当前筛选：前'+rows.filter(r=>r.side==='before').length+'条，后'+rows.filter(r=>r.side==='after').length+'条。';
for(const r of rows){const x=((8878-8356)/2+(r.x-7487)/18322*8356)*1600/8878,y=(1-(r.y-7693)/18322)*8356*1600/8878,color=r.side==='before'?'#6bbaff':'#ffce65';const c=node('circle',{cx:x,cy:y,r:11,fill:'#18232f',stroke:color,'stroke-width':4});if(r.ward_type==='sentry')c.setAttribute('stroke-dasharray','5 3');const title=node('title',{});title.textContent=teams[r.team]+' '+kinds[r.ward_type]+' '+time(r.time_s)+' / '+r.side+' / #'+r.ward_index;c.append(title);points.append(c);const text=node('text',{x:x+14,y:y+5,fill:color,'font-size':20,'paint-order':'stroke',stroke:'#101820','stroke-width':3});text.textContent=r.ward_index;points.append(text)}
document.getElementById('refs').textContent=JSON.stringify({group:g,ward_decisions:D.ward_decisions.filter(r=>r.nearest_group_ids.includes(g.group_id))},null,2);const body=document.getElementById('rates');body.replaceChildren();for(const r of D.per_match_rates.filter(x=>x.match_id===M.value&&(T.value==='all'||x.team===+T.value)&&(K.value==='all'||x.ward_type===K.value)))tr(body,[label(r),r.groups.length+' / '+r.zero_placement_groups,r.exposure_each_side_s.toFixed(3),r.before+' / '+r.after,r.before_recorded_placements_per_minute.toFixed(3)+' / '+r.after_recorded_placements_per_minute.toFixed(3)]);if(!body.children.length)tr(body,['本局没有符合当前筛选的足长机会','—','—','—','—'])}
const macro=document.getElementById('macro');for(const r of D.macro_rates)tr(macro,[sources[r.cohort[5]]+' / '+r.cohort[0]+' / '+r.cohort[1].slice(0,8)+' / '+r.cohort.slice(2,5).join(' / ')+' / 构建'+r.cohort[6]+' / 采集'+r.cohort[7].slice(0,10)+' / 上下文模式'+r.cohort[8],label(r),r.distinct_matches+' / '+r.recorded_target_groups,r.before_macro_rate.toFixed(3)+' / '+r.after_macro_rate.toFixed(3),r.match_refs.join(',')]);
document.getElementById('clock').textContent=D.clock_sensitivity.map(x=>'目标时间'+(x.summary.shift_s>0?'+':'')+x.summary.shift_s+'秒：'+x.assignment_changed_count+'条归属/前后侧/汇总资格变化，足长组中分配'+x.summary.assigned_to_summary_eligible_groups+'条').join('；');document.getElementById('coverage').textContent=D.source_coverage.map(x=>x.match_id+' / '+(sources[x.source_kind]||'没有上下文')+' / '+x.recorded_target_events+'条已记录目标 / '+x.status).join('\n');M.addEventListener('change',groups);for(const e of[G,T,K])e.addEventListener('change',draw);groups();</script>'''
    (output/'report.html').write_text(page.replace('PAYLOAD',payload),encoding='utf-8',newline='\n')
