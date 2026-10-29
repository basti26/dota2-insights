"""Retrospective match-split stability of recorded ward placement regions."""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import sqlite3

from .postgame_store import VERSION,bounded,sha
from .ward_patterns import CELL,COORDINATES,PHASE,key

SCHEMA='ward_region_retrospective_split_v1'
SEED='ward-region-split-v1-20261021'
TOP_K=5
MIN_TRAIN=10


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')


def analyze(database,output):
    database,output=Path(database).resolve(),Path(output).resolve()
    if output.exists():raise ValueError('ward_stability_new_output_required')
    digest=sha(bounded(database,128*1024**2))
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        metadata=dict(c.execute('SELECT key,value FROM metadata'))
        if metadata.get('schema_version')!=VERSION:raise ValueError('ward_stability_fact_schema')
        matches={r['match_id']:dict(r) for r in c.execute('SELECT * FROM matches')}
        if not 1<=len(matches)<=256:raise ValueError('ward_stability_match_budget')
        grouped=defaultdict(list)
        for m in matches.values():grouped[key(m)].append(m['match_id'])
        cohorts={};assignment={};labels={}
        for index,(group,ids) in enumerate(sorted(grouped.items()),1):
            label='cohort_'+str(index);labels[group]=label
            ordered=sorted(ids,key=lambda mid:(sha((SEED+'\0'+mid).encode()),mid))
            hold=set(ordered[:math.ceil(len(ids)/3)])
            train=set(ids)-hold
            cohorts[label]={**dict(zip(('source_group','producer_cohort','patch_key','map_key','mode_key'),group)),
                'match_count':len(ids),'training_ids':sorted(train),'heldout_ids':sorted(hold),
                'phase_count':math.ceil(max(matches[mid]['duration_s'] for mid in ids)/PHASE)}
            for mid in ids:assignment[mid]={'cohort':label,'role':'heldout' if mid in hold else 'training'}
        output.mkdir(parents=True)
        protocol={'schema_version':SCHEMA,'database_sha256':digest,'split_seed':SEED,
            'heldout_rule':'lowest_hash_ceil_one_third_of_matches_in_each_source_parser_patch_map_mode_cohort',
            'cell_world_units':CELL,'offsets':[0,CELL//2],'phase_s':PHASE,'top_k':TOP_K,
            'ranking':'distinct_training_match_support_descending_then_grid_x_y',
            'minimum_training_matches':MIN_TRAIN,'preferred_display_minimum_heldout_full_intervals':5,
            'global_model':'one_vote_per_training_match_cell_across_its_completed_intervals',
            'phase_model':'one_vote_per_training_match_cell_in_this_completed_interval',
            'heldout_denominator':'all_full_interval_matches_including_no_saved_placements',
            'prior_exploration':'whole_dataset_already_explored_design_level_leakage_not_a_pristine_or_prospective_holdout',
            'clock_and_map':'raw_export_cells_unknown_map_patch_remain_provisional'}
        write(output/'protocol.json',protocol)
        write(output/'split.json',{'cohorts':cohorts,'matches':assignment})

        def rows_for(role):
            ids=sorted(mid for mid,a in assignment.items() if a['role']==role)
            if not ids:return []
            rows=[dict(r) for r in c.execute('SELECT match_id,ward_index,team,ward_type,x,y,placed_s FROM wards '
                "WHERE placement_status='in_game' AND coordinate_basis=? AND match_id IN ("+','.join('?'*len(ids))+') ORDER BY match_id,placed_s,ward_index',
                (COORDINATES,*ids))]
            if len(rows)>100_000:raise ValueError('ward_stability_placement_budget')
            return rows

        training=rows_for('training')
        global_votes,phase_votes=defaultdict(lambda:defaultdict(set)),defaultdict(lambda:defaultdict(set))
        for row in training:
            mid=row['match_id'];phase=int(row['placed_s']//PHASE)
            if matches[mid]['duration_s']<(phase+1)*PHASE:continue
            for offset in (0,CELL//2):
                cell=(math.floor((row['x']-offset)/CELL),math.floor((row['y']-offset)/CELL))
                group=(assignment[mid]['cohort'],row['team'],row['ward_type'],offset)
                global_votes[group][cell].add(mid);phase_votes[(*group,phase)][cell].add(mid)

        def fit(votes,eligible):
            if len(eligible)<MIN_TRAIN:return {'status':'insufficient_training_matches','cells':[],'training_ids':sorted(eligible)}
            chosen=sorted(votes,key=lambda cell:(-len(votes[cell]),cell[0],cell[1]))[:TOP_K]
            return {'status':'frozen_training_selection' if chosen else 'no_training_placements',
                'training_ids':sorted(eligible),'cells':[{'grid_x':cell[0],'grid_y':cell[1],
                    'training_support':len(votes[cell]),'training_support_ids':sorted(votes[cell])} for cell in chosen]}

        frozen=[]
        for label,cohort in cohorts.items():
            for phase in range(cohort['phase_count']):
                train={mid for mid in cohort['training_ids'] if matches[mid]['duration_s']>=(phase+1)*PHASE}
                for team in (2,3):
                    for kind in ('observer','sentry'):
                        for offset in (0,CELL//2):
                            group=(label,team,kind,offset)
                            frozen.append({'cohort':label,'phase':phase,'team':team,'ward_type':kind,'grid_offset':offset,
                                'training_full_interval_ids':sorted(train),
                                'global':fit(global_votes[group],set(cohort['training_ids'])),
                                'phase_specific':fit(phase_votes[(*group,phase)],train)})
        write(output/'frozen-models.json',frozen)
        model_digest=sha((output/'frozen-models.json').read_bytes())

        # The location rows for held-out IDs are first read only after selections
        # have been persisted. Earlier all-data exploration still limits inference.
        heldout=rows_for('heldout')
        observed=defaultdict(lambda:defaultdict(list));partial=0
        for row in heldout:
            mid=row['match_id'];phase=int(row['placed_s']//PHASE)
            if matches[mid]['duration_s']<(phase+1)*PHASE:partial+=1;continue
            for offset in (0,CELL//2):
                cell=(math.floor((row['x']-offset)/CELL),math.floor((row['y']-offset)/CELL))
                group=(assignment[mid]['cohort'],phase,row['team'],row['ward_type'],offset)
                observed[group][mid].append({'ward_index':row['ward_index'],'cell':cell})
        results=[]
        for selection in frozen:
            label,phase=selection['cohort'],selection['phase'];cohort=cohorts[label]
            eligible=sorted(mid for mid in cohort['heldout_ids'] if matches[mid]['duration_s']>=(phase+1)*PHASE)
            partial_ids=sorted(mid for mid in cohort['heldout_ids'] if phase*PHASE<matches[mid]['duration_s']<(phase+1)*PHASE)
            group=(label,phase,selection['team'],selection['ward_type'],selection['grid_offset'])
            record={k:selection[k] for k in ('cohort','phase','team','ward_type','grid_offset')}
            record.update(training_full_intervals=len(selection['training_full_interval_ids']),
                heldout_full_intervals=len(eligible),heldout_partial_interval_ids=partial_ids,
                retrospective_display_eligible=len(eligible)>=5 and len(selection['training_full_interval_ids'])>=MIN_TRAIN,
                models={},paired_match_counts=None,heldout_records=[])
            per_model={}
            for name in ('global','phase_specific'):
                model=selection[name];cells={(r['grid_x'],r['grid_y']) for r in model['cells']}
                ready=model['status']=='frozen_training_selection'
                indicators={};fractions=[];nonempty=0
                for mid in eligible:
                    actual={r['cell'] for r in observed[group][mid]}
                    if actual:nonempty+=1
                    if ready:
                        indicators[mid]=bool(actual&cells)
                        if actual:fractions.append(len(actual&cells)/len(actual))
                per_model[name]=indicators
                record['models'][name]={'status':model['status'],'effective_cell_count':len(cells),
                    'selected_cells':[{**r,'heldout_support':sum((r['grid_x'],r['grid_y']) in {w['cell'] for w in observed[group][mid]} for mid in eligible)} for r in model['cells']],
                    'matches_with_any_saved_placement':nonempty,
                    'matches_with_selected_cell':sum(indicators.values()) if ready else None,
                    'match_fraction':sum(indicators.values())/len(eligible) if ready and eligible else None,
                    'mean_observed_cell_coverage_among_nonempty_matches':sum(fractions)/len(fractions) if fractions else None}
            if all(selection[n]['status']=='frozen_training_selection' for n in ('global','phase_specific')):
                record['paired_match_counts']={'both':sum(per_model['global'][mid] and per_model['phase_specific'][mid] for mid in eligible),
                    'global_only':sum(per_model['global'][mid] and not per_model['phase_specific'][mid] for mid in eligible),
                    'phase_only':sum(per_model['phase_specific'][mid] and not per_model['global'][mid] for mid in eligible),
                    'neither':sum(not per_model['global'][mid] and not per_model['phase_specific'][mid] for mid in eligible)}
            for mid in eligible:
                record['heldout_records'].append({'match_id':mid,'observed_cells':sorted({r['cell'] for r in observed[group][mid]}),
                    'ward_refs':[r['ward_index'] for r in observed[group][mid]],
                    'global_reappeared':per_model['global'].get(mid),'phase_specific_reappeared':per_model['phase_specific'].get(mid)})
            results.append(record)
        if sha((output/'frozen-models.json').read_bytes())!=model_digest or sha(bounded(database,128*1024**2))!=digest:
            raise ValueError('ward_stability_source_or_selection_changed')
        result={'schema_version':SCHEMA,'database_sha256':digest,'frozen_models_sha256':model_digest,
            'cohorts':cohorts,'split':assignment,'protocol':protocol,'results':results,
            'training_placement_rows':len(training),'heldout_placement_rows':len(heldout),
            'heldout_partial_interval_placements_not_scored':partial,
            'limitations':['Retrospective internal stability only: these matches informed earlier exploration.',
                'No prospective validation, statistical significance or tactical recommendation is claimed.',
                'Repeated players/teams, dates and map compatibility are not independently controlled.',
                'Saved placement coverage is not active vision, detection accuracy or desirable ward placement.',
                'Offset results reuse the same matches and are not independent replications.',
                'Late phases condition on match survival and have small denominators.']}
        write(output/'stability.json',result)
        return result
    finally:c.close()


def render(result,output,map_path):
    output=Path(output);raw=bounded(map_path,12*1024**2)
    if sha(raw)!='9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('ward_stability_pinned_map_required')
    (output/'map.jpg').write_bytes(raw)
    payload=json.dumps(result,ensure_ascii=False).replace('<','\\u003c')
    page=r'''<!doctype html><meta charset="utf-8"><title>常见区域的回顾性复现</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}select{font:inherit;margin:6px;padding:5px;max-width:95vw}a{color:#8cd8c3}.warn{color:#f1cc87;max-width:1250px}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #52677b}.scroll{max-height:65vh;overflow:auto}svg{width:100%;min-width:950px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<h1>训练组选出的区域，在另一组比赛出现了吗？</h1>
<p class="warn">仅为回顾性内部稳定性：整批比赛此前已参与探索，不是全新或前瞻验证。固定拆分、名单先冻结，之后才读取留出组位置；这些过程约束不能消除之前的研究设计影响。不评眼位好坏、不做战术推荐。</p>
<label>数据组<select id="cohort"></select></label><label>时段<select id="phase"></select></label><label>放置方<select id="team"><option value="2">天辉</option><option value="3">夜魇</option></select></label><label>眼型<select id="kind"><option value="observer">假眼</option><option value="sentry">真眼</option></select></label><label>网格<select id="offset"><option value="0">原始</option><option value="256">偏移半格</option></select></label>
<p id="info"></p><p>每份名单最多5个512单位区域，按不同训练比赛支持数排名，留出结果不参与选区。全时段名单使用训练比赛的全部完整时间段；阶段名单只使用本段。蓝框=全时段参考，金框=本阶段名单；框不是精确眼点，底图尚未逐局校准。</p>
<p>某局完整时段内至少一条已记录放眼落进名单，就计该局复现；没有放眼记录的完整时段仍进分母。它不是图像识别准确率，也不是实际视野覆盖率。</p>
<table><thead><tr><th>名单</th><th>有效区域数</th><th>留出比赛复现/完整段比赛</th><th>该段有放眼记录的比赛</th><th>状态</th></tr></thead><tbody id="scores"></tbody></table><p id="paired"></p>
<div class="scroll"><svg viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/><g id="cells"></g></svg></div>
<table><thead><tr><th>名单/训练排名</th><th>区域格</th><th>训练支持比赛</th><th>本段留出支持比赛</th></tr></thead><tbody id="regions"></tbody></table>
<details><summary>逐场留出记录与眼位索引</summary><pre id="records"></pre></details>
<p class="warn">样本少会明确标记；偏移网格仍是同一批比赛，不能当作第二次独立验证。补丁/地图未知、相同玩家或队伍是否重复、日期独立性均未解决。赛前放眼不在此游戏内放置评价范围。</p>
<p><a href="protocol.json">预设口径</a> · <a href="split.json">拆分</a> · <a href="frozen-models.json">冻结名单</a> · <a href="stability.json">完整结果</a></p>
<script>const D=PAYLOAD,ns='http://www.w3.org/2000/svg',C=document.getElementById('cohort'),P=document.getElementById('phase'),T=document.getElementById('team'),K=document.getElementById('kind'),O=document.getElementById('offset');const labels={global:'全时段参考',phase_specific:'本阶段名单'};
function el(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e}function svg(tag,attrs){const e=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);return e}
for(const[id,c]of Object.entries(D.cohorts)){const o=el('option',({own:'本人',d2pt:'D2PT',tier1:'Tier 1 职业'}[c.source_group]||c.source_group)+' '+c.match_count+'场 / 提取器'+c.producer_cohort.slice(0,8));o.value=id;C.append(o)}
function phases(){P.replaceChildren();for(let p=0;p<D.cohorts[C.value].phase_count;p++){const o=el('option',p*10+'～'+(p+1)*10+'分钟');o.value=p;P.append(o)}draw()}
function draw(){const c=D.cohorts[C.value],r=D.results.find(x=>x.cohort===C.value&&x.phase===+P.value&&x.team===+T.value&&x.ward_type===K.value&&x.grid_offset===+O.value);if(!r)return;
document.getElementById('info').textContent='训练'+c.training_ids.length+'场 / 留出'+c.heldout_ids.length+'场；本段完整：训练'+r.training_full_intervals+'、留出'+r.heldout_full_intervals+'；留出中途结束'+r.heldout_partial_interval_ids.length+'场。补丁'+c.patch_key+' / 地图'+c.map_key+' / 模式'+c.mode_key+'。'+(r.retrospective_display_eligible?'达到10训练/5留出完整段的浏览门槛。':'样本不足，不作稳定性概括。');
const scores=document.getElementById('scores'),regions=document.getElementById('regions'),cells=document.getElementById('cells');scores.replaceChildren();regions.replaceChildren();cells.replaceChildren();for(const name of ['global','phase_specific']){const m=r.models[name],tr=el('tr');const valid=m.match_fraction!==null;for(const v of [labels[name],m.effective_cell_count,valid?m.matches_with_selected_cell+'/'+r.heldout_full_intervals+(r.retrospective_display_eligible?' ('+(100*m.match_fraction).toFixed(1)+'%)':'\uff08\u6837\u672c\u4e0d\u8db3\uff09'):'未评估',m.matches_with_any_saved_placement,m.status==='frozen_training_selection'?'已按训练数据冻结':'训练样本或放眼记录不足'])tr.append(el('td',v));scores.append(tr);m.selected_cells.forEach((s,i)=>{const row=el('tr');for(const v of [labels[name]+' / '+(i+1),s.grid_x+','+s.grid_y,s.training_support,s.heldout_support])row.append(el('td',v));regions.append(row);const bx=((8878-8356)/2+(+O.value+s.grid_x*512-7487)/18322*8356)/8878*1600,by=(1-(+O.value+(s.grid_y+1)*512-7693)/18322)*8356/8878*1600,side=512/18322*8356/8878*1600,color=name==='global'?'#8bbfe8':'#f0c375';cells.append(svg('rect',{x:bx,y:by,width:side,height:side,fill:color,'fill-opacity':.12,stroke:color,'stroke-width':name==='global'?5:2}));const text=svg('text',{x:bx+side/2,y:by+side/2,fill:color,stroke:'black','stroke-width':3,'paint-order':'stroke','font-size':22});text.textContent=(name==='global'?'G':'P')+(i+1);cells.append(text)})}
const pair=r.paired_match_counts;document.getElementById('paired').textContent=pair?'同一批完整留出比赛：两份名单都复现'+pair.both+' / 仅全时段'+pair.global_only+' / 仅阶段'+pair.phase_only+' / 都未复现'+pair.neither+'。这是计数，不是显著性判断。':'当前没有两份均可评估的训练名单。';document.getElementById('records').textContent=r.heldout_records.map(x=>x.match_id+' / 记录格'+JSON.stringify(x.observed_cells)+' / 全时段'+(x.global_reappeared===null?'未评估':x.global_reappeared?'有':'无')+' / 阶段'+(x.phase_specific_reappeared===null?'未评估':x.phase_specific_reappeared?'有':'无')+' / ward_index='+x.ward_refs.join(',')).join('\n')||'没有完整留出时段。'}
C.onchange=phases;P.onchange=T.onchange=K.onchange=O.onchange=draw;phases();</script>'''.replace('PAYLOAD',payload)
    # Keep JavaScript newline escapes single in the raw HTML template.
    page=page.replace("join('\\\\n')","join('\\n')")
    (output/'report.html').write_text(page,encoding='utf-8',newline='\n')
