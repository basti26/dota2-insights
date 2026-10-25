"""Recorded placement-cell changes around disjoint objective opportunities."""
from __future__ import annotations

from collections import Counter,defaultdict
import json
import math
from pathlib import Path
import sqlite3
from .postgame_metadata_store import context_sources as resolved_context_sources

from .postgame_store import bounded,sha
from .ward_patterns import CELL,COORDINATES,PHASE
from .ward_objective_opportunities import SCHEMA as OPPORTUNITIES,EPS,MIN_RADIUS,relation

from .postgame_context_cohort import context_key,VERSION as COHORT_VERSION,FIELDS as COHORT_FIELDS

SCHEMA='recorded_objective_region_changes_v2'
STATES=('both_empty','before_records_only','after_records_only','both_nonempty')
WIDTHS=(CELL,CELL*2,CELL*4)


def analyze(database,opportunities_path,output):
    database,opportunities_path,output=map(lambda p:Path(p).resolve(),(database,opportunities_path,output))
    if output.exists():raise ValueError('objective_regions_new_output_required')
    digest=sha(bounded(database,128*1024**2));raw=bounded(opportunities_path,16*1024**2);op=json.loads(raw)
    if (op.get('schema_version')!=OPPORTUNITIES or op.get('database_sha256')!=digest
            or op.get('protocol',{}).get('cohort_schema_version')!=COHORT_VERSION
            or op.get('protocol',{}).get('cohort_fields')!=list(COHORT_FIELDS)):
        raise ValueError('objective_regions_frozen_opportunities_binding')
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        matches={r['match_id']:dict(r) for r in c.execute('SELECT * FROM matches')}
        sources=resolved_context_sources(c)
        wards={(r['match_id'],r['ward_index']):dict(r) for r in c.execute("SELECT match_id,ward_index,ward_type,team,x,y,placed_s FROM wards WHERE placement_status='in_game' AND coordinate_basis=?",(COORDINATES,))}
    finally:c.close()
    if len(op['groups'])>10_000 or len(wards)>100_000 or len(matches)>256:
        raise ValueError('objective_regions_row_budget')
    output.mkdir(parents=True)
    protocol={'schema_version':SCHEMA,'cohort_schema_version':COHORT_VERSION,'cohort_fields':list(COHORT_FIELDS),'resolved_metadata_field_sources':{mid:s['metadata_field_sources'] for mid,s in sources.items()},'database_sha256':digest,'opportunities_sha256':sha(raw),
        'cell_widths_world_units':list(WIDTHS),'grid_offsets':'0_and_half_of_each_cell_width',
        'window':'inherited_disjoint_equal_duration_open_windows_no_new_event_selection',
        'summary_eligibility':'at_least30_seconds_each_side_zero_placement_opportunities_retained',
        'cohort':'source_group_ward_producer_patch_map_mode_context_kind_context_build_context_producer_phase_target_family_set_target_relation_team_kind_offset',
        'candidate_cells':'union_of_all_observed_before_after_cells_within_each_summary_stratum_descriptive_not_heldout',
        'paired_cell_statistics':'within_same_opportunity_not_across_different_events',
        'summary_weights':'within_match_group_fractions_then_equal_match_macro_means',
        'duration':'radii_vary30_to90_seconds_equal_before_after_only_within_each_opportunity_not_constant_duration_propensity',
        'overlap_jaccard':'only_when_both_sides_have_saved_placements_otherwise_null',
        'interpretation':'recorded_placement_cells_not_active_vision_or_physical_ward_movement'}
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8',newline='\n')
    records=[];seen=set()
    for g in op['groups']:
        mid=g['match_id'];radius=g['balanced_radius_s'];center=g['center_s'];source=sources.get(mid)
        if (source is None or source['source_id']!=g['source_id'] or source['source_kind']!=g['source_kind']
                or g['summary_eligible']!=(radius+EPS>=MIN_RADIUS) or radius<0
                or center-radius< -EPS or center+radius>matches[mid]['duration_s']+EPS):
            raise ValueError('objective_regions_group_source_or_bounds')
        rows={}
        for side in ('before','after'):
            rows[side]=[]
            for index in g[side+'_refs']:
                ref=(mid,index);w=wards.get(ref)
                if ref in seen or w is None or not EPS<abs(w['placed_s']-center)<radius-EPS or (w['placed_s']<center)!=(side=='before'):
                    raise ValueError('objective_regions_ward_assignment')
                seen.add(ref);rows[side].append(w)
        cohort=list(context_key(matches[mid],sources[mid]))
        for team in (2,3):
            for kind in ('observer','sentry'):
                selected={side:[w for w in values if w['team']==team and w['ward_type']==kind] for side,values in rows.items()}
                for width,offset in ((w,o) for w in WIDTHS for o in (0,w//2)):
                    cell_refs={side:defaultdict(list) for side in ('before','after')}
                    for side,values in selected.items():
                        for w in values:cell_refs[side][(math.floor((w['x']-offset)/width),math.floor((w['y']-offset)/width))].append(w['ward_index'])
                    before,after=map(lambda side:set(cell_refs[side]),('before','after'));union=before|after
                    state='both_nonempty' if before and after else 'before_records_only' if before else 'after_records_only' if after else 'both_empty'
                    records.append({'match_id':mid,'group_id':g['group_id'],'source_id':g['source_id'],'cohort':cohort,
                        'phase':int(center//PHASE),'target_families':g['families'],'target_relation':relation(g['owner_team_if_unambiguous'],team),
                        'center_s':center,'radius_s':radius,'summary_eligible':g['summary_eligible'],
                        'team':team,'ward_type':kind,'cell_width':width,'grid_offset':offset,'state':state,
                        'before_refs':[w['ward_index'] for w in selected['before']],
                        'after_refs':[w['ward_index'] for w in selected['after']],
                        'before_cells':sorted(before),'after_cells':sorted(after),'common_cells':sorted(before&after),
                        'before_only_cells':sorted(before-after),'after_only_cells':sorted(after-before),
                        'jaccard_if_both_nonempty':len(before&after)/len(union) if before and after else None,
                        'cell_refs':[{'grid_x':x,'grid_y':y,'before_refs':cell_refs['before'][x,y],
                            'after_refs':cell_refs['after'][x,y]} for x,y in sorted(union)]})
    buckets=defaultdict(list)
    for r in records:
        if r['summary_eligible']:
            buckets[(tuple(r['cohort']),r['phase'],tuple(r['target_families']),r['target_relation'],r['team'],r['ward_type'],r['cell_width'],r['grid_offset'])].append(r)
    summaries=[]
    for bucket,rows in sorted(buckets.items()):
        cohort,phase,families,relative,team,kind,width,offset=bucket
        by_match=defaultdict(list)
        for r in rows:by_match[r['match_id']].append(r)
        per_match=[]
        for mid,values in sorted(by_match.items()):
            counts=Counter(r['state'] for r in values)
            per_match.append({'match_id':mid,'opportunity_count':len(values),'group_refs':[r['group_id'] for r in values],
                'exposure_each_side_s':sum(r['radius_s'] for r in values),'states':{s:counts[s] for s in STATES},
                'state_fractions':{s:counts[s]/len(values) for s in STATES}})
        candidates=sorted({tuple(cell) for r in rows for side in ('before_cells','after_cells') for cell in r[side]})
        cells=[]
        for cell in candidates:
            evidence=[]
            for mid,values in sorted(by_match.items()):
                flags=[(cell in set(map(tuple,r['before_cells'])),cell in set(map(tuple,r['after_cells']))) for r in values]
                counts={'both':sum(a and b for a,b in flags),'before_only':sum(a and not b for a,b in flags),
                    'after_only':sum(b and not a for a,b in flags),'neither':sum(not a and not b for a,b in flags)}
                evidence.append({'match_id':mid,'opportunity_count':len(values),'paired_counts':counts,
                    'group_refs':[{'group_id':r['group_id'],'before':a,'after':b} for r,(a,b) in zip(values,flags)]})
            macro={name:sum(e['paired_counts'][name]/e['opportunity_count'] for e in evidence)/len(evidence)
                   for name in ('both','before_only','after_only','neither')}
            cells.append({'grid_x':cell[0],'grid_y':cell[1],'eligible_matches':len(evidence),
                'matches_with_any_before':sum(e['paired_counts']['both']+e['paired_counts']['before_only']>0 for e in evidence),
                'matches_with_any_after':sum(e['paired_counts']['both']+e['paired_counts']['after_only']>0 for e in evidence),
                'matches_with_paired_both_in_at_least_one_group':sum(e['paired_counts']['both']>0 for e in evidence),
                'macro_group_fractions':macro,'before_macro_group_fraction':macro['both']+macro['before_only'],
                'after_macro_group_fraction':macro['both']+macro['after_only'],'per_match':evidence})
        summaries.append({'cohort':list(cohort),'phase':phase,'target_families':list(families),'target_relation':relative,
            'team':team,'ward_type':kind,'cell_width':width,'grid_offset':offset,'eligible_matches':len(by_match),'opportunity_count':len(rows),
            'states':{s:sum(p['states'][s] for p in per_match) for s in STATES},
            'state_fraction_macro':{s:sum(p['state_fractions'][s] for p in per_match)/len(per_match) for s in STATES},
            'per_match':per_match,'candidate_cell_count':len(cells),'cells':cells})
    if sha(bounded(database,128*1024**2))!=digest or sha(bounded(opportunities_path,16*1024**2))!=sha(raw):
        raise ValueError('objective_regions_sources_changed')
    result={'schema_version':SCHEMA,'database_sha256':digest,'opportunities_sha256':sha(raw),'protocol':protocol,
        'target_groups':op['groups'],'records':records,'summaries':summaries,
        'eligible_target_groups':sum(g['summary_eligible'] for g in op['groups']),
        'distinct_matches_with_eligible_groups':len({g['match_id'] for g in op['groups'] if g['summary_eligible']}),
        'record_states_primary_grid':dict(Counter(r['state'] for r in records if r['summary_eligible'] and r['cell_width']==CELL and r['grid_offset']==0)),
        'limitations':['Recorded placement-cell change is not active ward survival, vision coverage or desirable placement.',
            'No physical movement, exact unseen ward location, target proximity or tactical causation is inferred.',
            '30s threshold favors isolated recorded objectives; dense pushes are not represented by eligible summaries.',
            'Candidate cells are selected from this same explored dataset, not a prospective validation set.',
            'Offsets reuse the same games; raw map coordinates, source coverage and clocks retain prior uncertainties.',
            'Opportunity radii vary from30 to90s; occurrence fractions are not constant-duration placement propensity.',
            'Macro means give equal weight to matches, but target-free phase controls and repeated players remain unresolved.']}
    (output/'regions.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return result


def render(result,output,map_path):
    output=Path(output);raw=bounded(map_path,12*1024**2)
    if sha(raw)!='9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('objective_regions_pinned_map_required')
    (output/'map.jpg').write_bytes(raw)
    payload=json.dumps(result,ensure_ascii=False).replace('<','\\u003c')
    page=r'''<!doctype html><meta charset="utf-8"><title>目标前后放眼区域对照</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}a{color:#8cd8c3}select{font:inherit;margin:5px;padding:5px;max-width:95vw}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #52677b;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}.warn{color:#f1cc87}svg{width:100%;height:65vh;min-height:350px}.wrap{overflow-x:auto}details{margin:12px 0}</style>
<h1>目标前后：双方在哪些区域留下了放眼记录？</h1>
<p id="totals"></p><p class="warn">这些是新放眼的位置集合，不是当时存活的全部眼、真实视野或“眼位移动”。同格前后都有记录，也不保证是同一坐标。暂不解释放眼意图或好坏。</p>
<p>沿用前后等长、不重叠的窗口；不足每侧30秒只留明细。前后无记录的机会仍进分母。每个目标组独立配对，再先算每局比例、最后对不同比赛等权。来源、生产版本、阶段、目标类型及建筑归属分别统计。不同机会的单侧时长为30～90秒，出现比例不等于统一时长下的放眼倾向。</p>
<label>比赛<select id="match"></select></label><label>目标组<select id="group"></select></label><label>阵营<select id="team"><option value="2">天辉</option><option value="3">夜魇</option></select></label><label>眼型<select id="kind"><option value="observer">假眼</option><option value="sentry">真眼</option></select></label><label>网格宽度<select id="width"><option value="512">512单位</option><option value="1024">1024单位</option><option value="2048">2048单位</option></select></label><label>边界偏移<select id="offset"><option value="0">原始网格</option><option value="half">偏移半格</option></select></label>
<p id="info"></p><p id="status"></p><p>蓝框=仅前有记录，金框=仅后有记录，绿框=前后均有记录；数字为前/后放眼条数。不同网格是同批资料的边界/尺度对照，不是独立验证。底图未逐局校准，框不是精确眼点，也不表示目标就在附近。</p>
<svg viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/><g id="cells"></g></svg>
<table><thead><tr><th>区域格</th><th>前方记录索引</th><th>后方记录索引</th><th>状态</th></tr></thead><tbody id="local"></tbody></table>
<h2>相同条件下的机会与分母</h2><p id="condition"></p><p id="coverage"></p>
<table><thead><tr><th>比赛</th><th>足长机会数/每侧累计秒</th><th>前后都空</th><th>仅前有记录</th><th>仅后有记录</th><th>前后都有记录</th></tr></thead><tbody id="matches"></tbody></table>
<p>表中“前后都空”指当前阵营、眼型没有留存新放眼；不代表没有视野。短窗不进入这些分母。目标中心决定10分钟阶段，窗口可能跨段。</p>
<h2>候选区域的同一目标组前后配对</h2><p>候选是本条件下前后记录出现过的全部格子，没有按变化幅度挑选。每格分母包括同条件下完全未在此格放眼的比赛和机会。只描述本批资料，不是留出验证。</p>
<div class="wrap"><table><thead><tr><th>格子</th><th>有前记录比赛/全部</th><th>有后记录比赛/全部</th><th>至少一个组前后同格的比赛</th><th>前/后机会比例（逐局均值）</th><th>同组双有/仅前/仅后/双无（逐局均值）</th></tr></thead><tbody id="summary"></tbody></table></div>
<details><summary>机会、格子、逐局引用与来源</summary><pre id="refs"></pre></details>
<p class="warn">当前条件分组最多仅少量比赛；30秒门槛偏向稀疏目标，连续推进可能没有可汇总机会。没有目标事件覆盖时不能补零。没有同阶段无目标对照，不能将前后变化归因于推塔或肉山；时钟扰动尚未做本页逐格重数。</p>
<p><a href="regions.json">完整结果</a> · <a href="protocol.json">分析口径</a></p>
<script>const D=PAYLOAD,M=document.getElementById('match'),G=document.getElementById('group'),T=document.getElementById('team'),K=document.getElementById('kind'),W=document.getElementById('width'),O=document.getElementById('offset'),ns='http://www.w3.org/2000/svg';
const fam={tower_destroyed:'防御塔',barracks_destroyed:'兵营',ancient_destroyed:'遗迹',roshan_kill:'肉山击杀记录',roshan_death_record:'肉山死亡记录',other_building:'其他建筑'},rel={own_building:'本方建筑',opponent_building:'对方建筑',mixed_or_unknown:'混合/归属未知'},states={both_empty:'前后均无放眼记录',before_records_only:'仅前有放眼记录',after_records_only:'仅后有放眼记录',both_nonempty:'前后都有放眼记录'},source={legacy_archive:'旧DEM（覆盖过滤）',api_projection:'API投影',replay_capture:'同遍采集'};
function el(t,v){const e=document.createElement(t);if(v!==undefined)e.textContent=v;return e}function tr(body,vs){const r=el('tr');for(const v of vs)r.append(el('td',v));body.append(r)}function node(t,attrs){const e=document.createElementNS(ns,t);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);return e}function time(t){return Math.floor(t/60)+':'+(t%60).toFixed(1).padStart(4,'0')}function xy(x,y){return [((8878-8356)/2+(x-7487)/18322*8356)*1600/8878,(1-(y-7693)/18322)*8356*1600/8878]}function pct(x){return (x*100).toFixed(1)+'%'}
const populations=new Map(D.records.filter(r=>r.summary_eligible).map(r=>[r.match_id,r.cohort[0]]));document.getElementById('totals').textContent=D.eligible_target_groups+'个足长目标组 / '+D.distinct_matches_with_eligible_groups+'场；可汇总来源：本人'+[...populations.values()].filter(v=>v==='own').length+'场，高分来源'+[...populations.values()].filter(v=>v==='d2pt').length+'场。原始512网格按阵营及眼型拆分：'+Object.entries(D.record_states_primary_grid).map(([s,n])=>states[s]+n+'组').join('，')+'。这些分组不是不同比赛数。';
for(const mid of [...new Set(D.target_groups.map(g=>g.match_id))]){const o=el('option',mid);o.value=mid;M.append(o)}
function groups(){G.replaceChildren();for(const g of D.target_groups.filter(g=>g.match_id===M.value)){const o=el('option',time(g.center_s)+' / '+g.families.map(f=>fam[f]||f).join(',')+(g.summary_eligible?'':'（短窗）'));o.value=g.group_id;G.append(o)}draw()}
function draw(){const offset=O.value==='half'?+W.value/2:0,r=D.records.find(r=>r.group_id===G.value&&r.team===+T.value&&r.ward_type===K.value&&r.cell_width===+W.value&&r.grid_offset===offset),g=D.target_groups.find(g=>g.group_id===G.value);const before=r.before_refs.length,after=r.after_refs.length;
document.getElementById('info').textContent='比赛'+r.match_id+' / '+source[r.cohort[5]]+' / 目标'+time(r.center_s)+' / 每侧'+r.radius_s.toFixed(3)+'秒 / '+(r.summary_eligible?'计入汇总':'短窗仅回看')+' / '+r.phase*10+'～'+(r.phase+1)*10+'分钟 / '+rel[r.target_relation];document.getElementById('status').textContent=states[r.state]+'：前'+before+'条/'+r.before_cells.length+'格，后'+after+'条/'+r.after_cells.length+'格；前后共同格'+r.common_cells.length+'。'+(r.jaccard_if_both_nonempty===null?'至少一侧无记录，不计算重合比例。':'两侧均有记录的格集合Jaccard='+r.jaccard_if_both_nonempty.toFixed(3)+'；不是视野覆盖率。');
const cells=document.getElementById('cells'),local=document.getElementById('local');cells.replaceChildren();local.replaceChildren();for(const c of r.cell_refs){const b=c.before_refs.length,a=c.after_refs.length,color=b&&a?'#82dfa0':b?'#6bbaff':'#ffce65',x=c.grid_x*r.cell_width+r.grid_offset,y=c.grid_y*r.cell_width+r.grid_offset,[px,py]=xy(x,y+r.cell_width),size=r.cell_width/18322*8356*1600/8878;const rect=node('rect',{x:px,y:py,width:size,height:size,fill:color,'fill-opacity':.22,stroke:color,'stroke-width':3});const title=node('title',{});title.textContent='格'+c.grid_x+','+c.grid_y+' / 前#'+c.before_refs.join(',')+' / 后#'+c.after_refs.join(',');rect.append(title);cells.append(rect);const text=node('text',{x:px+3,y:py+18,fill:color,'font-size':18,'paint-order':'stroke',stroke:'#101820','stroke-width':3});text.textContent=b+'/'+a;cells.append(text);tr(local,[c.grid_x+','+c.grid_y,c.before_refs.join(',')||'无',c.after_refs.join(',')||'无',b&&a?'同格前后均有记录':b?'仅前':'仅后'])}if(!r.cell_refs.length)tr(local,['当前没有留存放眼格子','无','无','不等于无视野']);
const s=D.summaries.find(s=>JSON.stringify(s.cohort)===JSON.stringify(r.cohort)&&s.phase===r.phase&&JSON.stringify(s.target_families)===JSON.stringify(r.target_families)&&s.target_relation===r.target_relation&&s.team===r.team&&s.ward_type===r.ward_type&&s.cell_width===r.cell_width&&s.grid_offset===r.grid_offset);const matches=document.getElementById('matches'),body=document.getElementById('summary');matches.replaceChildren();body.replaceChildren();const meta=v=>v==='not_recorded'?'未记录':v;document.getElementById('condition').textContent='来源'+source[r.cohort[5]]+' / 生产批次'+r.cohort[1].slice(0,10)+' / 补丁'+meta(r.cohort[2])+' / 地图'+meta(r.cohort[3])+' / 模式'+meta(r.cohort[4])+' / 构建'+meta(r.cohort[6])+' / 上下文采集'+meta(r.cohort[7]).slice(0,10)+' / 上下文模式'+meta(r.cohort[8])+' / '+r.target_families.map(f=>fam[f]||f).join(',')+' / '+rel[r.target_relation];
document.getElementById('coverage').textContent=(r.summary_eligible?'':'当前短窗不参与下列分母；')+(s?'本条件'+s.eligible_matches+'场 / '+s.opportunity_count+'个足长目标组；样本少，只浏览记录。':'本条件没有足长目标组，不显示成零发生率。');if(s){for(const p of s.per_match)tr(matches,[p.match_id,p.opportunity_count+' / '+p.exposure_each_side_s.toFixed(3),p.states.both_empty,p.states.before_records_only,p.states.after_records_only,p.states.both_nonempty]);for(const c of s.cells){const m=c.macro_group_fractions;tr(body,[c.grid_x+','+c.grid_y,c.matches_with_any_before+'/'+c.eligible_matches,c.matches_with_any_after+'/'+c.eligible_matches,c.matches_with_paired_both_in_at_least_one_group+'/'+c.eligible_matches,pct(c.before_macro_group_fraction)+' / '+pct(c.after_macro_group_fraction),['both','before_only','after_only','neither'].map(k=>pct(m[k])).join(' / ')])}if(!s.cells.length)tr(body,['同条件没有留存候选格；机会与零记录仍保留','—','—','—','—','—'])}document.getElementById('refs').textContent=JSON.stringify({target_group:g,selected_slice:r,summary:s||null},null,2)}
if(D.target_groups.length){M.addEventListener('change',groups);for(const e of[G,T,K,W,O])e.addEventListener('change',draw);groups()}else{document.getElementById('info').textContent='当前资料没有可比较的目标记录，缺少上下文不能当零目标事件。';for(const e of[M,G,T,K,W,O])e.disabled=true}</script>'''
    (output/'report.html').write_text(page.replace('PAYLOAD',payload),encoding='utf-8',newline='\n')
