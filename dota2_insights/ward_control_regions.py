"""Four-window placement-cell comparisons from frozen time-only controls."""
from __future__ import annotations

from collections import Counter, defaultdict
import html
import json
import math
from pathlib import Path
import sqlite3
from .postgame_metadata_store import context_sources as resolved_context_sources

from .postgame_store import bounded, sha
from .postgame_context_cohort import context_key
from .ward_patterns import CELL, COORDINATES, PHASE
from .ward_context_balance import SCHEMA as BALANCE, LAG_LIMITS
from .ward_time_controls import SCHEMA as CONTROLS
from .ward_objective_opportunities import EPS, relation

SCHEMA = 'ward_four_window_cell_comparison_v1'
WINDOWS = ('target_before', 'target_after', 'control_before', 'control_after')
WIDTHS = (CELL, CELL*2, CELL*4)


def analyze(database, balance_path, output):
    database, balance_path, output = map(lambda p: Path(p).resolve(), (database, balance_path, output))
    if output.exists():
        raise ValueError('control_regions_new_output_required')
    digest = sha(bounded(database, 128*1024**2))
    raw = bounded(balance_path, 20*1024**2)
    balance = json.loads(raw)
    if (balance.get('schema_version') != BALANCE or balance.get('database_sha256') != digest
            or balance['protocol']['lag_limits_s'] != list(LAG_LIMITS)):
        raise ValueError('control_regions_frozen_balance_required')
    bindings, pairs = {}, {}
    for limit in LAG_LIMITS:
        directory = 'lag-unlimited' if limit is None else f'lag-{limit}'
        path = balance_path.parent/directory/'time-controls.json'
        body = bounded(path, 16*1024**2)
        run = json.loads(body)
        expected = next(r['selection_sha256'] for r in balance['protocol']['allocations'] if r['limit_s']==limit)
        selection_path = path.parent/'frozen-control-selection.json'
        selection = bounded(selection_path, 16*1024**2)
        if (run.get('schema_version') != CONTROLS or run.get('database_sha256') != digest
                or run['selection_sha256'] != expected or sha(selection) != expected
                or json.loads(selection) != run['allocation']
                or run['protocol']['maximum_abs_center_lag_s'] != limit):
            raise ValueError('control_regions_control_selection_binding')
        bindings[path] = sha(body)
        bindings[selection_path] = sha(selection)
        for pair in run['pairs']:
            key = (limit, pair['buffer_s'], pair['group_id'])
            if key in pairs:
                raise ValueError('control_regions_duplicate_pair')
            pairs[key] = pair
    c = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        matches = {r['match_id']: dict(r) for r in c.execute('SELECT * FROM matches')}
        sources = resolved_context_sources(c)
        wards = {(r['match_id'],r['ward_index']): dict(r) for r in c.execute(
            "SELECT * FROM wards WHERE placement_status='in_game' AND coordinate_basis=?", (COORDINATES,))}
    finally:
        c.close()
    if len(pairs)>5000 or len(wards)>100_000 or len(balance['records'])>30_000:
        raise ValueError('control_regions_row_budget')
    by_match_wards = defaultdict(list)
    for ward in wards.values():
        by_match_wards[ward['match_id']].append(ward)
    if sum(len(by_match_wards[p['match_id']])*4 for p in pairs.values())>20_000_000:
        raise ValueError('control_regions_window_work_budget')
    records, used = [], set()
    for allocation in balance['records']:
        if allocation['status'] != 'matched':
            continue
        key = (allocation['maximum_center_lag_s'],allocation['buffer_s'],allocation['group_id'])
        if key in used or key not in pairs:
            raise ValueError('control_regions_missing_or_duplicate_matched_group')
        used.add(key)
        pair = pairs[key]
        if any(pair[k] != allocation[k] for k in pair if k in allocation):
            raise ValueError('control_regions_pair_allocation_mismatch')
        mid = pair['match_id']
        cohort = list(context_key(matches[mid],sources[mid]))
        radius, target, control = pair['radius_s'],pair['target_center_s'],pair['control_center_s']
        if (pair['source_id']!=sources[mid]['source_id'] or cohort!=pair['cohort']
                or radius+EPS<30 or not 0<=target-radius<target+radius<=matches[mid]['duration_s']+EPS
                or not 0<=control-radius<control+radius<=matches[mid]['duration_s']+EPS
                or int(target//PHASE)!=int(control//PHASE)):
            raise ValueError('control_regions_source_phase_or_window_bounds')
        windows = {}
        seen_refs = set()
        for name in WINDOWS:
            center = target if name.startswith('target') else control
            indices = pair[name+'_refs']
            expected = {w['ward_index'] for w in by_match_wards[mid]
                        if EPS<abs(w['placed_s']-center)<radius-EPS and (w['placed_s']<center)==name.endswith('before')}
            if set(indices)!=expected:
                raise ValueError('control_regions_incomplete_window_references')
            if len(indices) != len(set(indices)):
                raise ValueError('control_regions_duplicate_ward_in_window')
            selected = []
            for index in indices:
                ref = (mid,index)
                ward = wards.get(ref)
                if (ward is None or ref in seen_refs or not EPS<abs(ward['placed_s']-center)<radius-EPS
                        or (ward['placed_s']<center) != name.endswith('before')):
                    raise ValueError('control_regions_ward_window_assignment')
                seen_refs.add(ref)
                selected.append(ward)
            windows[name] = selected
        for team in (2,3):
            for kind in ('observer','sentry'):
                refs = {name:[w for w in values if w['team']==team and w['ward_type']==kind]
                        for name,values in windows.items()}
                for width in WIDTHS:
                    for offset in (0,width//2):
                        cells = defaultdict(lambda:{name:[] for name in WINDOWS})
                        for name, values in refs.items():
                            for ward in values:
                                position = (math.floor((ward['x']-offset)/width),math.floor((ward['y']-offset)/width))
                                cells[position][name].append(ward['ward_index'])
                        records.append({'maximum_center_lag_s':key[0],'buffer_s':key[1], 'group_id':key[2],
                            'match_id':mid,'source_id':pair['source_id'],'cohort':cohort,
                            'phase':int(target//PHASE),'target_families':pair['target_families'],
                            'target_relation':relation(pair['target_owner_team_if_unambiguous'],team),
                            'target_center_s':target,'control_center_s':control,'radius_s':radius,
                            'team':team,'ward_type':kind,'cell_width':width,'grid_offset':offset,
                            'window_refs':{name:[w['ward_index'] for w in values] for name,values in refs.items()},
                            'all_four_windows_empty':not any(refs.values()),
                            'cells':[{'grid_x':x,'grid_y':y,'window_refs':value} for (x,y),value in sorted(cells.items())]})
    if used != set(pairs):
        raise ValueError('control_regions_matched_coverage_mismatch')
    buckets = defaultdict(list)
    dimensions = ('maximum_center_lag_s','buffer_s','cohort','phase','target_families',
                  'target_relation','team','ward_type','cell_width','grid_offset')
    for record in records:
        key = tuple(tuple(record[k]) if isinstance(record[k],list) else record[k] for k in dimensions)
        buckets[key].append(record)
    summaries, cell_work = [], 0
    for key, rows in sorted(buckets.items(),key=lambda item:repr(item[0])):
        by_match = defaultdict(list)
        for row in rows:
            by_match[row['match_id']].append(row)
        candidates = sorted({(cell['grid_x'],cell['grid_y']) for row in rows for cell in row['cells']})
        cell_work += len(candidates)*len(rows)
        if cell_work>200_000:
            raise ValueError('control_regions_cell_observation_budget')
        lookup = {id(row):{(cell['grid_x'],cell['grid_y']):cell['window_refs'] for cell in row['cells']} for row in rows}
        cells = []
        for x,y in candidates:
            per_match = []
            for mid,values in sorted(by_match.items()):
                observations = []
                for row in values:
                    refs = lookup[id(row)].get((x,y),{name:[] for name in WINDOWS})
                    counts = {name:len(refs[name]) for name in WINDOWS}
                    difference = counts['target_after']-counts['target_before']-counts['control_after']+counts['control_before']
                    observations.append({'group_id':row['group_id'],'window_refs':refs,'counts':counts,
                        'presence_pattern':''.join('1' if counts[name] else '0' for name in WINDOWS),
                        'count_change_difference':difference,'count_change_difference_per_minute':difference*60/row['radius_s']})
                per_match.append({'match_id':mid,'opportunity_count':len(values),
                    'presence_fractions':{name:sum(o['counts'][name]>0 for o in observations)/len(values) for name in WINDOWS},
                    'mean_count_change_difference':sum(o['count_change_difference'] for o in observations)/len(values),
                    'mean_count_change_difference_per_minute':sum(o['count_change_difference_per_minute'] for o in observations)/len(values),
                    'observations':observations})
            cells.append({'grid_x':x,'grid_y':y,
                'eligible_matches':len(per_match),'matched_opportunities':len(rows),
                'presence_pattern_counts':dict(Counter(o['presence_pattern'] for m in per_match for o in m['observations'])),
                'macro_presence_fractions':{name:sum(m['presence_fractions'][name] for m in per_match)/len(per_match) for name in WINDOWS},
                'macro_count_change_difference':sum(m['mean_count_change_difference'] for m in per_match)/len(per_match),
                'macro_count_change_difference_per_minute':sum(m['mean_count_change_difference_per_minute'] for m in per_match)/len(per_match),
                'per_match':per_match})
        summaries.append({**{name:list(value) if isinstance(value,tuple) else value for name,value in zip(dimensions,key)},
            'eligible_matches':len(by_match),'matched_opportunities':len(rows),
            'all_four_windows_empty_opportunities':sum(r['all_four_windows_empty'] for r in rows),
            'per_match':[{'match_id':mid,'opportunity_count':len(values),'group_refs':[r['group_id'] for r in values],
                          'exposure_each_window_s':sum(r['radius_s'] for r in values)} for mid,values in sorted(by_match.items())],
            'cells':cells})
    if (sha(bounded(database,128*1024**2)) != digest or sha(bounded(balance_path,20*1024**2)) != sha(raw)
            or any(sha(bounded(path,16*1024**2))!=value for path,value in bindings.items())):
        raise ValueError('control_regions_inputs_changed')
    output.mkdir(parents=True)
    protocol = {'schema_version':SCHEMA,'database_sha256':digest,'balance_sha256':sha(raw),'resolved_metadata_field_sources':{mid:s['metadata_field_sources'] for mid,s in sources.items()},
        'source_bindings':{str(path.relative_to(balance_path.parent)):value for path,value in bindings.items()},
        'windows':list(WINDOWS),'cell_widths':list(WIDTHS),'grid_offsets':'zero_and_half_width',
        'candidate_cells':'union_of_all_four_windows_within_same_conditional_stratum_not_frozen_holdout',
        'denominator':'all_matched_opportunities_in_stratum_including_no_cell_or_no_placement_matches',
        'unmatched':'coverage_preserved_separately_never_imputed_as_zero_control',
        'weights':'within_match_opportunity_mean_then_equal_match_mean',
        'count_change_difference':'target_after-target_before-control_after+control_before_descriptive_only',
        'presence_pattern_order':list(WINDOWS),'conditional_dimensions':list(dimensions),
        'interpretation':'placement_records_not_active_vision_not_causal_effect_or_ward_quality'}
    result = {'schema_version':SCHEMA,'database_sha256':digest,'protocol':protocol,'coverage':balance['coverage'],
        'matched_parameter_pairs':len(pairs),'records':records,'summaries':summaries,
        'limitations':['All parameters reuse previously explored matches and can overlap across configurations.',
            'Same recorded objective excludes no guarantee of other fights or unobserved objectives.',
            'Economic conditions are recorded elsewhere but have not been matched; later controls can follow the target aftermath.',
            'Zero placement records do not imply no existing vision; candidate cells do not cover unobserved map cells.',
            'Unknown patch/map and provisional basemap retain original limitations; build and source strata stay separate.',
            'Positive, negative or zero count-change difference does not measure effectiveness, preference or causality.']}
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8',newline='\n')
    body = json.dumps(result,ensure_ascii=False,indent=2).encode('utf-8')
    if len(body)>64*1024**2:
        raise ValueError('control_regions_output_byte_budget')
    (output/'control-regions.json').write_bytes(body)
    return result


def render(result, output, map_path, *, balance_report_href=None):
    output = Path(output)
    raw = bounded(map_path,12*1024**2)
    if sha(raw)!='9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('control_regions_pinned_basemap_required')
    (output/'map.jpg').write_bytes(raw)
    # Detailed per-match cell observations live in the linked JSON. The viewer
    # embeds only the same summaries and selected four-window slices.
    display = {k:v for k,v in result.items() if k not in ('protocol','limitations')}
    display['summaries'] = [{**s,'cells':[{k:v for k,v in cell.items() if k!='per_match'} for cell in s['cells']]}
                            for s in result['summaries']]
    payload = json.dumps(display,ensure_ascii=False).replace('<','\\u003c')
    page = r'''<!doctype html><meta charset="utf-8"><title>目标与对照的四窗口放眼区域</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}select{font:inherit;padding:5px;margin:5px;max-width:95vw}a{color:#8cd8c3}.warn{color:#f1cc87}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #52677b;text-align:left}.maps{display:grid;grid-template-columns:1fr 1fr;gap:12px}svg{width:100%;max-height:65vh}pre{white-space:pre-wrap;overflow-wrap:anywhere}.wrap{overflow:auto}</style>
<h1>同一目标与对照：四个窗口分别在哪里放眼？</h1><p class="warn">地图只画新放眼格子，不是存活眼、实际视野或眼位优劣。配对只按时间选定，尚未匹配经济、英雄或建筑状态；较晚对照可能已包含目标之后的信息。以下四项计数差只是算术，不是目标造成的效果。</p>
<label>中心距离上限<select id="lag"><option value="none">未限定</option><option>60</option><option>120</option><option>300</option></select></label><label>目标保护秒<select id="buffer"><option>0</option><option>30</option><option>90</option></select></label><p id="coverage"></p>
<label>已匹配目标<select id="pair"></select></label><label>阵营<select id="team"><option value="2">天辉</option><option value="3">夜魇</option></select></label><label>眼型<select id="kind"><option value="observer">假眼</option><option value="sentry">真眼</option></select></label><label>格子宽度<select id="width"><option>512</option><option>1024</option><option>2048</option></select></label><label>偏移<select id="offset"><option value="0">原网格</option><option value="half">偏移半格</option></select></label>
<p id="condition"></p><p id="info"></p><p>四图使用相同底图与网格；每格数字为新放眼条数。底图未逐局校准，框不是精确眼点或目标位置。空图仍保留，不代表没有视野。</p><div class="maps" id="maps"></div>
<div class="wrap"><table><thead><tr><th>本次配对格子</th><th>目标前 / 后</th><th>对照前 / 后</th><th>两组变化相减</th><th>四窗原眼位索引</th></tr></thead><tbody id="local"></tbody></table></div>
<h2>同条件候选格与完整配对分母</h2><p id="denominator"></p><p>候选格来自本条件四窗的全部观测，不按差值挑选；分母包括完全没在该格放眼的匹配比赛/目标组。未匹配目标单列，不能补成零对照；全空条件可能没有候选格，但机会仍在。先逐局计算，再对比赛等权。</p><div class="wrap"><table><thead><tr><th>候选格</th><th>目标前 / 后有记录比例</th><th>对照前 / 后有记录比例</th><th>原计数变化差均值</th><th>每分钟变化差均值</th></tr></thead><tbody id="summary"></tbody></table></div><details><summary>所选四窗记录与来源引用</summary><pre id="refs"></pre></details><p><a href="control-regions.json">全部原始引用和逐局计数</a> · <a href="protocol.json">口径与输入指纹</a> · <a href="../context-balance/report.html">时间匹配与经济覆盖</a></p>
<script>const D=PAYLOAD,L=document.getElementById('lag'),B=document.getElementById('buffer'),P=document.getElementById('pair'),T=document.getElementById('team'),K=document.getElementById('kind'),W=document.getElementById('width'),O=document.getElementById('offset'),names=['target_before','target_after','control_before','control_after'],labels=['目标前','目标后','对照前','对照后'],colors=['#70bbff','#ffcf70','#b3a3ff','#7ee3b0'],ns='http://www.w3.org/2000/svg';const meta=v=>v==='not_recorded'?'未记录':v;const time=v=>Math.floor(v/60)+':'+(v%60).toFixed(1).padStart(4,'0');function tr(id,vals){const r=document.createElement('tr');for(const v of vals){const c=document.createElement('td');c.textContent=v;r.append(c)}document.getElementById(id).append(r)}function node(tag,attrs){const e=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);return e}function xy(x,y){return[(261+(x-7487)/18322*8356)*1600/8878,(1-(y-7693)/18322)*8356*1600/8878]}
function draw(){for(const id of ['maps','local','summary'])document.getElementById(id).replaceChildren();const r=D.records.find(r=>r.maximum_center_lag_s===(L.value==='none'?null:+L.value)&&r.buffer_s===+B.value&&r.match_id+':'+r.group_id===P.value&&r.team===+T.value&&r.ward_type===K.value&&r.cell_width===+W.value&&r.grid_offset===(O.value==='half'?+W.value/2:0));if(!r){document.getElementById('info').textContent='本参数没有匹配对照，不生成零值地图或零差异。';document.getElementById('condition').textContent='';document.getElementById('denominator').textContent='没有匹配配对，不计算候选格比例。';document.getElementById('refs').textContent='未匹配范围见覆盖记录。';return}document.getElementById('info').textContent='比赛'+r.match_id+'；目标'+time(r.target_center_s)+'，对照'+time(r.control_center_s)+'，每窗'+r.radius_s.toFixed(3)+'秒；四窗记录数'+names.map(n=>r.window_refs[n].length).join(' / ')+(r.all_four_windows_empty?'；四窗均无新放眼记录，仍保留机会。':'');document.getElementById('condition').textContent='群体'+r.cohort[0]+' / 眼位生产'+r.cohort[1].slice(0,10)+' / 补丁'+meta(r.cohort[2])+' / 地图'+meta(r.cohort[3])+' / 模式'+meta(r.cohort[4])+' / 上下文'+r.cohort[5]+' / 构建'+meta(r.cohort[6])+' / 采集'+meta(r.cohort[7]).slice(0,10)+' / 上下文模式'+meta(r.cohort[8])+' / '+r.phase*10+'～'+(r.phase+1)*10+'分钟 / '+r.target_families.join(',')+' / '+r.target_relation;
for(let i=0;i<4;i++){const div=document.createElement('div'),h=document.createElement('h3');h.textContent=labels[i]+'：'+r.window_refs[names[i]].length+'条';div.append(h);const svg=node('svg',{viewBox:'0 0 1600 1505.9'});svg.append(node('image',{href:'map.jpg',width:1600,height:1505.9}));for(const cell of r.cells){const refs=cell.window_refs[names[i]];if(!refs.length)continue;const x=cell.grid_x*r.cell_width+r.grid_offset,y=cell.grid_y*r.cell_width+r.grid_offset,[px,py]=xy(x,y+r.cell_width),size=r.cell_width/18322*8356*1600/8878;const rect=node('rect',{x:px,y:py,width:size,height:size,fill:colors[i],'fill-opacity':.25,stroke:colors[i],'stroke-width':3}),title=node('title',{});title.textContent='格'+cell.grid_x+','+cell.grid_y+' / #'+refs.join(',');rect.append(title);svg.append(rect);const text=node('text',{x:px+3,y:py+19,fill:colors[i],'font-size':20,stroke:'#101820','stroke-width':3,'paint-order':'stroke'});text.textContent=refs.length;svg.append(text)}div.append(svg);document.getElementById('maps').append(div)}
for(const cell of r.cells){const counts=names.map(n=>cell.window_refs[n].length);tr('local',[cell.grid_x+','+cell.grid_y,counts[0]+' / '+counts[1],counts[2]+' / '+counts[3],counts[1]-counts[0]-counts[3]+counts[2],names.map((n,i)=>labels[i]+': '+(cell.window_refs[n].join(',')||'无')).join('；')])}if(!r.cells.length)tr('local',['四窗均没有候选格，配对仍计入分母','0 / 0','0 / 0',0,'无留存新放眼']);const dims=['maximum_center_lag_s','buffer_s','cohort','phase','target_families','target_relation','team','ward_type','cell_width','grid_offset'];const s=D.summaries.find(s=>dims.every(k=>JSON.stringify(s[k])===JSON.stringify(r[k])));document.getElementById('denominator').textContent=s?'本条件'+s.eligible_matches+'场 / '+s.matched_opportunities+'个匹配目标；四窗全空'+s.all_four_windows_empty_opportunities+'组。样本少，仅浏览记录。':'没有相同条件汇总';if(s){for(const cell of s.cells){const v=cell.macro_presence_fractions,p=n=>(v[n]*100).toFixed(1)+'%';tr('summary',[cell.grid_x+','+cell.grid_y,p(names[0])+' / '+p(names[1]),p(names[2])+' / '+p(names[3]),cell.macro_count_change_difference.toFixed(3),cell.macro_count_change_difference_per_minute.toFixed(3)])}if(!s.cells.length)tr('summary',['本条件没有留存候选格，机会仍保留','—','—','—','—'])}document.getElementById('refs').textContent=JSON.stringify({record:r,summary:s||null},null,2)}
function pairs(){P.replaceChildren();const limit=L.value==='none'?null:+L.value,c=D.coverage.find(c=>c.maximum_center_lag_s===limit&&c.buffer_s===+B.value);document.getElementById('coverage').textContent=c?'匹配'+(c.statuses.matched||0)+'/'+c.eligible_target_groups+'个足长组，'+c.matched_distinct_matches+'场；原无候选'+(c.statuses.no_initial_candidates||0)+'、间隔排除'+(c.statuses.no_candidates_within_center_lag||0)+'、分配无剩余'+(c.statuses.no_disjoint_candidate_after_greedy||0)+'；另有'+(c.statuses.short_target_not_eligible||0)+'短窗不进分母。':'没有覆盖记录';const rows=D.records.filter(r=>r.maximum_center_lag_s===limit&&r.buffer_s===+B.value&&r.team===2&&r.ward_type==='observer'&&r.cell_width===512&&r.grid_offset===0).sort((a,b)=>a.match_id.localeCompare(b.match_id)||a.target_center_s-b.target_center_s);for(const r of rows){const o=document.createElement('option');o.value=r.match_id+':'+r.group_id;o.textContent=r.match_id+' / '+time(r.target_center_s);P.append(o)}P.disabled=!rows.length;draw()}for(const e of[L,B])e.addEventListener('change',pairs);for(const e of[P,T,K,W,O])e.addEventListener('change',draw);pairs();</script>'''
    page = page.replace(' · <a href="../context-balance/report.html">时间匹配与经济覆盖</a>',
        '' if balance_report_href is None else ' · <a href="'+html.escape(balance_report_href,quote=True)+'">时间匹配与经济覆盖</a>')
    (output/'report.html').write_text(page.replace('PAYLOAD',payload),encoding='utf-8',newline='\n')
