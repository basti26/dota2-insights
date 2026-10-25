"""Whole-match ten-minute ward-span summaries with paired phase populations."""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import sqlite3

from .postgame_store import bounded, sha, number
from .postgame_metadata_store import resolved_metadata
from .ward_patterns import CELL, PHASE, key as ward_key
from .ward_interval_context import SCHEMA as INTERVALS, CATEGORIES, union_seconds

SCHEMA='ward_interval_complete_phase_v2'
WIDTHS=(CELL,CELL*2,CELL*4)
COHORT_FIELDS=('source_group','ward_producer','patch','map','mode','resolved_game_build','resolved_game_mode')


def analyze(database,interval_path,output):
    database,interval_path,output=map(lambda p:Path(p).resolve(),(database,interval_path,output))
    if output.exists():raise ValueError('interval_phases_new_output_required')
    digest=sha(bounded(database,128*1024**2));raw=bounded(interval_path,64*1024**2);source=json.loads(raw)
    if source.get('schema_version')!=INTERVALS or source.get('database_sha256')!=digest:
        raise ValueError('interval_phases_source_binding')
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        matches={r['match_id']:dict(r) for r in c.execute('SELECT * FROM matches')}
        resolved=resolved_metadata(c)
        originals={(r['match_id'],r['ward_index']):dict(r) for r in c.execute("SELECT * FROM wards WHERE placement_status IN ('pregame','in_game') AND coordinate_basis='gem_cell128_plus_vec_raw_coordinates'")}
    finally:c.close()
    ledger=source['wards']
    if len(matches)>256 or len(ledger)>100_000 or {(w['match_id'],w['ward_index']) for w in ledger}!=set(originals) or len(ledger)!=len(originals):
        raise ValueError('interval_phases_source_ward_set_or_budget')
    for w in ledger:
        old=originals[w['match_id'],w['ward_index']]
        if any(w[k]!=old[k] for k in ('team','ward_type','x','y','placed_s','ended_s','placement_status','end_kind','lifetime_status')):
            raise ValueError('interval_phases_original_ward_fields_changed')
        finish=matches[w['match_id']]['duration_s'];end=w['ended_s']
        valid_end=number(end) and end>=w['placed_s'] and w['end_kind'] in ('killed','expired')
        expected='reported_in_game_end' if valid_end and end<=finish else 'reported_after_match_end' if valid_end else 'possible_unknown_end'
        if w['category']!=expected or w['match_end_s']!=finish:
            raise ValueError('interval_phases_lifecycle_class_or_game_end_changed')
    cohorts=defaultdict(list);match_cohort={};metadata_refs={}
    for mid,m in matches.items():
        metadata=resolved[mid]
        for name in ('game_build','game_mode'):
            if metadata.get(name) is not None and (type(metadata[name]) is not int or metadata[name]<=0):
                raise ValueError('interval_phases_invalid_known_context_metadata')
        cohort=(*ward_key(m),str(metadata['game_build']) if metadata.get('game_build') is not None else 'not_recorded',
                str(metadata['game_mode']) if metadata.get('game_mode') is not None else 'not_recorded')
        cohorts[cohort].append(mid);match_cohort[mid]=cohort
        metadata_refs[mid]=metadata['field_sources']
    labels={cohort:'interval_cohort_'+str(i+1) for i,(cohort,mids) in enumerate(sorted(cohorts.items(),key=lambda item:(-len(item[1]),item[0])))}
    populations=[];exposure={}
    for cohort,mids in sorted(cohorts.items()):
        count=math.ceil(max(matches[mid]['duration_s'] for mid in mids)/PHASE)
        if count>36:raise ValueError('interval_phases_time_budget')
        for p in range(count):
            full=sorted(mid for mid in mids if matches[mid]['duration_s']>=(p+1)*PHASE)
            partial=sorted(mid for mid in mids if p*PHASE<matches[mid]['duration_s']<(p+1)*PHASE)
            ended=sorted(set(mids)-set(full)-set(partial));exposure[cohort,p]=full
            populations.append({'cohort_id':labels[cohort],'cohort':list(cohort),'phase':p,'start_s':p*PHASE,'end_s':(p+1)*PHASE,
                'complete_match_ids':full,'partial_match_ids_reference_only':partial,'ended_before_phase_ids':ended})
    observations=[];placements=[]
    # Sparse references retain partial final phases, but those phases never
    # enter a full-ten-minute denominator or a paired transition.
    for w in ledger:
        finish=matches[w['match_id']]['duration_s'];cohort=match_cohort[w['match_id']]
        if w['placed_s']>=0:
            p=int(w['placed_s']//PHASE)
            placements.append({'match_id':w['match_id'],'ward_index':w['ward_index'],'phase':p,
                'summary_eligible':w['match_id'] in exposure[cohort,p]})
        stop=min(finish,w['ended_s']) if w['category']!='possible_unknown_end' else finish
        if stop<=max(0,w['placed_s']):continue
        for p in range(int(max(0,w['placed_s'])//PHASE),math.ceil(stop/PHASE)):
            a=max(0,w['placed_s'],p*PHASE);b=min(stop,(p+1)*PHASE)
            if b<=a:continue
            observations.append({'match_id':w['match_id'],'ward_index':w['ward_index'],'phase':p,
                'start_s':a,'end_s':b,'seconds':b-a,'summary_eligible':w['match_id'] in exposure[cohort,p]})
            if len(observations)>200_000:raise ValueError('interval_phases_observation_budget')
    ward_lookup={(w['match_id'],w['ward_index']):w for w in ledger}
    buckets=defaultdict(lambda:defaultdict(lambda:defaultdict(lambda:{'intervals':[],'new_refs':[]})))
    def insert(record,is_interval):
        if not record['summary_eligible']:return
        w=ward_lookup[record['match_id'],record['ward_index']];cohort=match_cohort[w['match_id']]
        for width in WIDTHS:
            for offset in (0,width//2):
                k=(cohort,record['phase'],w['team'],w['ward_type'],width,offset,w['category'])
                cell=(math.floor((w['x']-offset)/width),math.floor((w['y']-offset)/width))
                row=buckets[k][cell][w['match_id']]
                if is_interval:row['intervals'].append((record['start_s'],record['end_s'],w['ward_index']))
                else:row['new_refs'].append(w['ward_index'])
    if (len(observations)+len(placements))*6>1_500_000:raise ValueError('interval_phases_grid_work_budget')
    for row in observations:insert(row,True)
    for row in placements:insert(row,False)
    summaries=[];index={}
    for population in populations:
        cohort=tuple(population['cohort']);p=population['phase'];full=population['complete_match_ids'];n=len(full)
        if not n:continue
        for team in (2,3):
            for kind in ('observer','sentry'):
                for width in WIDTHS:
                    for offset in (0,width//2):
                        for category in CATEGORIES:
                            k=(cohort,p,team,kind,width,offset,category);cells=[]
                            for (x,y),by_match in sorted(buckets[k].items()):
                                per_match=[]
                                for mid,data in sorted(by_match.items()):
                                    seconds=union_seconds((a,b) for a,b,_ in data['intervals'])
                                    if not -1e-7<=seconds<=PHASE+1e-7:raise ValueError('interval_phases_union_bounds')
                                    per_match.append({'match_id':mid,'union_seconds':seconds,
                                        'sum_ward_seconds':sum(b-a for a,b,_ in data['intervals']),
                                        'interval_ward_refs':[i for _,_,i in data['intervals']],
                                        'new_placement_refs':data['new_refs']})
                                cells.append({'grid_x':x,'grid_y':y,'matches_with_span':sum(r['union_seconds']>0 for r in per_match),
                                    'matches_with_new_placement':sum(bool(r['new_placement_refs']) for r in per_match),
                                    'mean_union_seconds_per_complete_match':sum(r['union_seconds'] for r in per_match)/n,
                                    'mean_union_fraction':sum(r['union_seconds'] for r in per_match)/n/PHASE,
                                    'mean_new_placements_per_complete_match':sum(len(r['new_placement_refs']) for r in per_match)/n,
                                    'mean_sum_ward_seconds_per_complete_match':sum(r['sum_ward_seconds'] for r in per_match)/n,
                                    'nonzero_or_new_match_records':per_match})
                            summary={'cohort_id':labels[cohort],'cohort':list(cohort),'phase':p,'team':team,'ward_type':kind,
                                'cell_width':width,'grid_offset':offset,'category':category,'complete_match_ids':full,
                                'complete_match_count':n,'cells':cells,'zero_record_matches_in_denominator':True}
                            summaries.append(summary);index[k]=summary
    transitions=[];transition_cells=0
    for k,before in sorted(index.items()):
        cohort,p,team,kind,width,offset,category=k;after=index.get((cohort,p+1,team,kind,width,offset,category))
        if after is None:continue
        common=sorted(set(before['complete_match_ids'])&set(after['complete_match_ids']))
        if not common:continue
        lookup=[]
        for s in (before,after):
            lookup.append({(cell['grid_x'],cell['grid_y']):{r['match_id']:r for r in cell['nonzero_or_new_match_records'] if r['match_id'] in common}
                           for cell in s['cells'] if any(r['match_id'] in common for r in cell['nonzero_or_new_match_records'])})
        cells=[]
        for x,y in sorted(set(lookup[0])|set(lookup[1])):
            per_match=[]
            for mid in common:
                a,b=(s.get((x,y),{}).get(mid) for s in lookup)
                if a is None and b is None:continue
                ua=a['union_seconds'] if a else 0.;ub=b['union_seconds'] if b else 0.
                na=len(a['new_placement_refs']) if a else 0;nb=len(b['new_placement_refs']) if b else 0
                per_match.append({'match_id':mid,'before_union_seconds':ua,'after_union_seconds':ub,
                    'union_seconds_difference':ub-ua,'before_new_count':na,'after_new_count':nb,
                    'new_count_difference':nb-na})
            positive=sum(r['union_seconds_difference']>0 for r in per_match);negative=sum(r['union_seconds_difference']<0 for r in per_match)
            cells.append({'grid_x':x,'grid_y':y,'common_complete_matches':len(common),
                'mean_union_seconds_difference':sum(r['union_seconds_difference'] for r in per_match)/len(common),
                'mean_new_count_difference':sum(r['new_count_difference'] for r in per_match)/len(common),
                'matches_positive_seconds_difference':positive,'matches_negative_seconds_difference':negative,
                'matches_zero_seconds_difference':len(common)-positive-negative,
                'nonzero_or_new_match_records':per_match})
            transition_cells+=1
            if transition_cells>200_000:raise ValueError('interval_phases_transition_budget')
        transitions.append({**{name:before[name] for name in ('cohort_id','cohort','team','ward_type','cell_width','grid_offset','category')},
            'before_phase':p,'after_phase':p+1,'common_complete_match_ids':common,'cells':cells})
    if sha(bounded(database,128*1024**2))!=digest or sha(bounded(interval_path,64*1024**2))!=sha(raw):
        raise ValueError('interval_phases_inputs_changed')
    protocol={'schema_version':SCHEMA,'database_sha256':digest,'source_interval_sha256':sha(raw),'phase_seconds':PHASE,
        'cohort_fields':list(COHORT_FIELDS),'cell_widths':list(WIDTHS),'grid_offsets':'zero_and_half_width',
        'full_denominator':'all_cohort_matches_with_duration_at_least_phase_end_including_no_record_at_cell',
        'partial_phases':'raw_observations_preserved_reference_only_never_silently_dropped_or_summarized_as_full',
        'candidate_cells':'union_of_recorded_intervals_and_new_placements_in_complete_phase_exploratory_not_heldout',
        'paired_changes':'intersection_of_matches_complete_in_both_adjacent_phases_not_difference_of_changing_population_means',
        'sparse_match_rows':'omitted_matches_are_zero_recorded_value_and_explicitly_remain_in_complete_match_id_denominator',
        'unknown_end':'upper_bound_only_never_confirmed_survival_difference_of_upper_bounds_not_bound_on_change',
        'known_build':'explicitly_selected_probe_and_original_context_fields_unknown_stays_unknown_not_same_version_guarantee',
        'interpretation':'recorded_span_proxy_and_placement_patterns_not_actual_vision_quality_or_tactical_preference'}
    result={'schema_version':SCHEMA,'database_sha256':digest,'protocol':protocol,
        'cohorts':[{'cohort_id':labels[k],'cohort':list(k),'match_ids':sorted(v),'known_metadata_source_refs':{mid:metadata_refs[mid] for mid in sorted(v)}} for k,v in sorted(cohorts.items(),key=lambda item:labels[item[0]])],
        'populations':populations,'observations':observations,'placements':placements,'wards':ledger,
        'summaries':summaries,'paired_phase_changes':transitions}
    body=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    if len(body)>128*1024**2:raise ValueError('interval_phases_output_byte_budget')
    output.mkdir(parents=True);(output/'phase-intervals.json').write_bytes(body)
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8',newline='\n')
    return result


def render(result,output,map_path):
    output=Path(output);raw=bounded(map_path,12*1024**2)
    if sha(raw)!='9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':raise ValueError('interval_phases_pinned_map')
    (output/'map.jpg').write_bytes(raw)
    display={k:result[k] for k in ('cohorts','populations','summaries','paired_phase_changes')}
    payload=json.dumps(display,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    page=r'''<!doctype html><meta charset="utf-8"><title>完整时段的放眼与区间变化</title><style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}select{font:inherit;padding:5px;margin:5px;max-width:95vw}a{color:#8cd8c3}.warn{color:#f1cc87}svg{width:100%;height:auto;max-width:1400px;min-height:600px;display:block}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #52677b;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<h1>每10分钟：新放眼与记录区间分开统计</h1><p class="warn">只分析原记录。区间不是实际视野；未知结束只表示可能时长上界。版本未知的群体可能包含不同构建，不等于同版本验证。不按变化挑“好眼位”。</p>
<label>来源群体<select id="cohort"></select></label><label>时段<select id="phase"></select></label><label>阵营<select id="team"><option value="2">天辉</option><option value="3">夜魇</option></select></label><label>眼型<select id="kind"><option value="observer">假眼</option><option value="sentry">真眼</option></select></label><label>记录结束类别<select id="category"><option value="reported_in_game_end">局内已记结束</option><option value="reported_after_match_end">局后才记结束（单列）</option><option value="possible_unknown_end">结束未知：可能上界</option></select></label><label>格宽<select id="width"><option>512</option><option>1024</option><option>2048</option></select></label><label>边界<select id="offset"><option value="0">原网格</option><option value="half">偏移半格</option></select></label><label>地图数值<select id="metric"><option value="seconds">区间并集秒数均值</option><option value="new">段内新放次数均值</option></select></label>
<p id="condition"></p><p id="coverage"></p><p id="note"></p><p>完整时段每场600秒；没在候选格留记录的比赛仍进分母。尾段不足10分钟只留原始引用，不混算。开局前眼可贡献段内区间，不能算段内新放。地图仍未逐版本校准，格子不是精确点。</p><svg viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/><g id="cells"></g></svg>
<h2>本段全部候选格</h2><table><thead><tr><th>格子</th><th>有区间记录场数/完整场数</th><th>并集秒数均值/600</th><th>新放次数均值</th></tr></thead><tbody id="summary"></tbody></table>
<label>逐局查看格子<select id="cell"></select></label><table><thead><tr><th>比赛</th><th>本格区间并集秒 / 新放数</th><th>区间眼位索引 / 新放索引</th></tr></thead><tbody id="matches"></tbody></table>
<h2>相邻时段：只比较两段都完整的同一批比赛</h2><p id="paired"></p><table><thead><tr><th>格子</th><th>后一段减前一段的秒数均值</th><th>新放次数均值差</th><th>秒数正 / 负 / 零变化的比赛数</th></tr></thead><tbody id="changes"></tbody></table><details><summary>完整分母、原引用和配对时段</summary><pre id="refs"></pre></details><p><a href="phase-intervals.json">全部比赛/完整及不足时段的原数据</a> · <a href="protocol.json">分析口径</a></p>
<script>const D=PAYLOAD,ids=['cohort','phase','team','kind','category','width','offset','metric','cell'],E=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)])),ns='http://www.w3.org/2000/svg';const meta=v=>v==='not_recorded'?'未记录':v;function tr(id,vals){const r=document.createElement('tr');for(const v of vals){const c=document.createElement('td');c.textContent=v;r.append(c)}document.getElementById(id).append(r)}function node(t,a){const n=document.createElementNS(ns,t);for(const[k,v]of Object.entries(a))n.setAttribute(k,v);return n}function xy(x,y){return[(261+(x-7487)/18322*8356)*1600/8878,(1-(y-7693)/18322)*8356*1600/8878]}function selected(){return D.summaries.find(s=>s.cohort_id===E.cohort.value&&s.phase===+E.phase.value&&s.team===+E.team.value&&s.ward_type===E.kind.value&&s.category===E.category.value&&s.cell_width===+E.width.value&&s.grid_offset===(E.offset.value==='half'?+E.width.value/2:0))}function matches(){document.getElementById('matches').replaceChildren();const s=selected(),v=s&&s.cells.find(c=>c.grid_x+','+c.grid_y===E.cell.value);if(!s||!v)return;const m=new Map(v.nonzero_or_new_match_records.map(r=>[r.match_id,r]));for(const mid of s.complete_match_ids){const r=m.get(mid);tr('matches',[mid,(r?r.union_seconds.toFixed(3):'0.000')+' / '+(r?r.new_placement_refs.length:0),(r?r.interval_ward_refs.join(','):'无')+' / '+(r?r.new_placement_refs.join(','):'无')])}}
function draw(){for(const id of ['summary','matches','changes','cells'])document.getElementById(id).replaceChildren();E.cell.replaceChildren();const p=D.populations.find(p=>p.cohort_id===E.cohort.value&&p.phase===+E.phase.value),c=D.cohorts.find(c=>c.cohort_id===E.cohort.value),s=selected();document.getElementById('condition').textContent=c?'来源'+c.cohort[0]+' / 眼位生产'+c.cohort[1].slice(0,10)+' / 原补丁标识'+meta(c.cohort[2])+' / 地图'+meta(c.cohort[3])+' / 模式'+meta(c.cohort[4])+' / 已知构建'+meta(c.cohort[5])+' / 元数据模式'+meta(c.cohort[6]):'';document.getElementById('coverage').textContent=p?'完整'+p.complete_match_ids.length+'场；不足本段'+p.partial_match_ids_reference_only.length+'场仅参考；此前已结束'+p.ended_before_phase_ids.length+'场。':'';document.getElementById('note').textContent=E.category.value==='possible_unknown_end'?'本层是可能时长上界。上界数值之差不是实际变化的上界，也不证明眼仍活着。':E.category.value==='reported_after_match_end'?'本层局后才记录结束，独立显示区间代理，不合并为局内确认。':'本层为局内记录放置—结束区间代理，不是实际视野面积或有效率。';if(s){const maxNew=Math.max(1,...s.cells.map(v=>v.mean_new_placements_per_complete_match));for(const v of s.cells){tr('summary',[v.grid_x+','+v.grid_y,v.matches_with_span+'/'+s.complete_match_count,v.mean_union_seconds_per_complete_match.toFixed(3),v.mean_new_placements_per_complete_match.toFixed(3)]);const o=document.createElement('option');o.value=v.grid_x+','+v.grid_y;o.textContent=o.value;E.cell.append(o);const[px,py]=xy(v.grid_x*s.cell_width+s.grid_offset,(v.grid_y+1)*s.cell_width+s.grid_offset),size=s.cell_width/18322*8356*1600/8878,value=E.metric.value==='new'?v.mean_new_placements_per_complete_match:v.mean_union_seconds_per_complete_match,intensity=E.metric.value==='new'?value/maxNew:v.mean_union_fraction,color=E.category.value==='possible_unknown_end'?'#ffb86a':'#80d9ba';const rect=node('rect',{x:px,y:py,width:size,height:size,fill:color,'fill-opacity':.08+.5*intensity,stroke:color,'stroke-width':2,'stroke-dasharray':E.category.value==='possible_unknown_end'?'8 5':'none'}),title=node('title',{});title.textContent='格'+v.grid_x+','+v.grid_y+' / '+s.complete_match_count+'场 / '+value.toFixed(3);rect.append(title);document.getElementById('cells').append(rect);const text=node('text',{x:px+2,y:py+18,fill:color,'font-size':17,stroke:'#101820','stroke-width':3,'paint-order':'stroke'});text.textContent=value===0?'0':value<.1?'<0.1':value.toFixed(1);document.getElementById('cells').append(text)}if(!s.cells.length)tr('summary',['本条件没有留存候选格；完整比赛分母仍保留','—','—','—'])}else tr('summary',['没有完整比赛，不计算零比例或均值','—','—','—']);E.cell.disabled=!s||!s.cells.length;matches();const q=s&&D.paired_phase_changes.find(q=>q.cohort_id===s.cohort_id&&q.before_phase===s.phase&&['team','ward_type','cell_width','grid_offset','category'].every(k=>q[k]===s[k]));document.getElementById('paired').textContent=q?E.phase.value*10+'～'+(+E.phase.value+1)*10+'分钟 → '+(+E.phase.value+1)*10+'～'+(+E.phase.value+2)*10+'分钟；共同完整'+q.common_complete_match_ids.length+'场，未用不同人数的均值相减。':'没有下一段的共同完整比赛，不计算零变化。';if(q)for(const v of q.cells)tr('changes',[v.grid_x+','+v.grid_y,v.mean_union_seconds_difference.toFixed(3),v.mean_new_count_difference.toFixed(3),v.matches_positive_seconds_difference+' / '+v.matches_negative_seconds_difference+' / '+v.matches_zero_seconds_difference]);document.getElementById('refs').textContent=JSON.stringify({population:p,summary:s||null,paired:q||null},null,2)}function phases(){E.phase.replaceChildren();for(const p of D.populations.filter(p=>p.cohort_id===E.cohort.value).sort((a,b)=>a.phase-b.phase)){const o=document.createElement('option');o.value=p.phase;o.textContent=p.phase*10+'～'+(p.phase+1)*10+'分钟';E.phase.append(o)}draw()}for(const c of D.cohorts){const o=document.createElement('option');o.value=c.cohort_id;o.textContent=c.cohort[0]+' / '+c.match_ids.length+'场 / 构建'+meta(c.cohort[5])+' / 生产'+c.cohort[1].slice(0,8);E.cohort.append(o)}E.cohort.value=D.cohorts.slice().sort((a,b)=>b.match_ids.length-a.match_ids.length)[0]?.cohort_id||'';E.cohort.addEventListener('change',phases);E.cell.addEventListener('change',matches);for(const id of ['phase','team','kind','category','width','offset','metric'])E[id].addEventListener('change',draw);phases();</script>'''
    (output/'report.html').write_text(page.replace('PAYLOAD',payload),encoding='utf-8',newline='\n')
