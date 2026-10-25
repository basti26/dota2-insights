"""Recorded ward spans and unknown-end bounds in frozen four-window pairs."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sqlite3

from .postgame_store import bounded, sha, number
from .ward_patterns import CELL, COORDINATES
from .ward_control_regions import SCHEMA as REGIONS, WINDOWS

SCHEMA = 'ward_recorded_interval_context_v1'
CATEGORIES = ('reported_in_game_end', 'reported_after_match_end', 'possible_unknown_end')


def union_seconds(intervals):
    """Union of source-time intervals in one cell/category, never across cells."""
    total, right = 0.0, None
    for start,end in sorted(intervals):
        total += end-start if right is None or start>=right else max(0,end-right)
        right = end if right is None else max(right,end)
    return total


def analyze(database, regions_path, output):
    database,regions_path,output = map(lambda p:Path(p).resolve(),(database,regions_path,output))
    if output.exists():
        raise ValueError('ward_intervals_new_output_required')
    digest = sha(bounded(database,128*1024**2))
    raw = bounded(regions_path,64*1024**2)
    regions = json.loads(raw)
    if regions.get('schema_version')!=REGIONS or regions.get('database_sha256')!=digest:
        raise ValueError('ward_intervals_frozen_region_binding')
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    try:
        matches={r['match_id']:dict(r) for r in c.execute('SELECT * FROM matches')}
        rows=[dict(r) for r in c.execute('SELECT * FROM wards ORDER BY match_id,ward_index')]
    finally:
        c.close()
    if len(rows)>100_000 or len(regions['records'])>120_000:
        raise ValueError('ward_intervals_row_budget')
    ledger,by_match,excluded = [],defaultdict(list),Counter()
    for row in rows:
        if row['placement_status'] not in ('pregame','in_game') or row['coordinate_basis']!=COORDINATES:
            excluded[row['placement_status']+'::'+row['coordinate_basis']]+=1
            continue
        end,placed,finish=row['ended_s'],row['placed_s'],matches[row['match_id']]['duration_s']
        if (not number(placed) or not number(row['x']) or not number(row['y'])
                or row['team'] not in (2,3)):
            raise ValueError('ward_intervals_invalid_accepted_placement')
        valid_end=(number(end) and end>=placed and row['end_kind'] in ('killed','expired'))
        category=('reported_in_game_end' if valid_end and end<=finish else
                  'reported_after_match_end' if valid_end else 'possible_unknown_end')
        raw_record=json.loads(row['record_json'])
        entry={k:row[k] for k in ('match_id','ward_index','ward_type','team','x','y','placed_s','ended_s',
                                  'end_kind','placement_status','lifetime_status','event_fingerprint')}
        entry.update(category=category,match_end_s=finish,
            end_fields_status='usable_recorded_end' if valid_end else 'missing_end' if end is None else 'invalid_or_conflicting_end',
            raw_killed_tick=raw_record.get('killed_tick'),raw_expires_tick=raw_record.get('expires_tick'),
            raw_killer=raw_record.get('killer'),
            killer_attribution='raw_unit_present_not_enemy_team_proof' if raw_record.get('killer') else 'not_recorded',
            interval_interpretation='recorded_placement_to_end_proxy_not_verified_visibility' if valid_end else
                'upper_bound_only_after_recorded_placement_no_assumed_lifetime')
        ledger.append(entry)
        by_match[row['match_id']].append(entry)
    chosen=[r for r in regions['records'] if r['cell_width']==CELL and r['grid_offset']==0]
    if sum(len(by_match[r['match_id']])*4 for r in chosen)>20_000_000:
        raise ValueError('ward_intervals_window_work_budget')
    records,seen=[],set()
    for r in chosen:
        identity=(r['maximum_center_lag_s'],r['buffer_s'],r['group_id'],r['team'],r['ward_type'])
        if identity in seen:
            raise ValueError('ward_intervals_duplicate_pair_slice')
        seen.add(identity)
        record={k:v for k,v in r.items() if k not in ('cells','window_refs','all_four_windows_empty')}
        record['windows']={}
        for name in WINDOWS:
            center=r['target_center_s'] if name.startswith('target') else r['control_center_s']
            a,b=(center-r['radius_s'],center) if name.endswith('before') else (center,center+r['radius_s'])
            if not 0<=a<b<=matches[r['match_id']]['duration_s']+1e-7:
                raise ValueError('ward_intervals_window_bounds')
            cells=defaultdict(lambda:{category:[] for category in CATEGORIES})
            for ward in by_match[r['match_id']]:
                if ward['team']!=r['team'] or ward['ward_type']!=r['ward_type']:
                    continue
                left=max(a,ward['placed_s'],0)
                right=min(b,ward['ended_s'],ward['match_end_s']) if ward['category']!='possible_unknown_end' else min(b,ward['match_end_s'])
                if right<=left:
                    continue
                cell=(math.floor(ward['x']/CELL),math.floor(ward['y']/CELL))
                cells[cell][ward['category']].append({'ward_index':ward['ward_index'],'start_s':left,'end_s':right,
                    'seconds':right-left,'placed_before_or_at_window_start':ward['placed_s']<=a,
                    'placed_pregame':ward['placement_status']=='pregame'})
            cell_rows=[]
            for (x,y),categories in sorted(cells.items()):
                values={}
                for category,intervals in categories.items():
                    union=union_seconds((v['start_s'],v['end_s']) for v in intervals)
                    values[category]={'ward_refs':[v['ward_index'] for v in intervals],
                        'intervals':intervals,'union_seconds':union,'union_fraction_of_window':union/(b-a),
                        'sum_ward_seconds':sum(v['seconds'] for v in intervals)}
                cell_rows.append({'grid_x':x,'grid_y':y,'categories':values})
            totals={}
            for category in CATEGORIES:
                contributions=[v for cell in cell_rows for v in cell['categories'][category]['intervals']]
                totals[category]={'ward_count':len(contributions),'ward_refs':[v['ward_index'] for v in contributions],
                    'carry_in_ward_refs':[v['ward_index'] for v in contributions if v['placed_before_or_at_window_start']],
                    'pregame_ward_refs':[v['ward_index'] for v in contributions if v['placed_pregame']],
                    'sum_ward_seconds':sum(v['seconds'] for v in contributions),
                    'sum_cell_union_seconds':sum(cell['categories'][category]['union_seconds'] for cell in cell_rows)}
            record['windows'][name]={'start_s':a,'end_s':b,'duration_s':b-a,
                'new_placement_refs':r['window_refs'][name], 'categories':totals,'cells':cell_rows}
        records.append(record)
    protocol={'schema_version':SCHEMA,'database_sha256':digest,'regions_sha256':sha(raw),
        'cell_width':CELL,'grid_offset':0,'window_order':list(WINDOWS),'categories':list(CATEGORIES),
        'placement_scope':'valid_pregame_and_in_game_source_wards_include_existing_before_window',
        'reported_in_game_end':'recorded_placed_to_end_half_open_interval_clipped_to_window_and_game',
        'reported_after_match_end':'raw_end_is_after_game_separate_proxy_not_merged_into_in_game_end',
        'possible_unknown_end':'zero_confirmed_duration_upper_bound_from_placement_to_window_end_no_expiry_imputation',
        'boundaries':'half_open_time_intervals_zero_length_excluded_new_placement_refs_keep_frozen_strict_boundary_policy',
        'union':'per_cell_per_category_merge_intervals_before_dividing_by_window_seconds',
        'interpretation':'recorded_span_proxy_or_unknown_upper_bound_not_visibility_or_deward_success',
        'no_merge_across_categories':True,'no_cross_match_mean_or_preference_claim':True}
    if sha(bounded(database,128*1024**2))!=digest or sha(bounded(regions_path,64*1024**2))!=sha(raw):
        raise ValueError('ward_intervals_input_changed')
    result={'schema_version':SCHEMA,'database_sha256':digest,'protocol':protocol,'coverage':regions['coverage'],
        'source_ward_counts':dict(Counter(w['category'] for w in ledger)),
        'source_placement_counts':dict(Counter(w['placement_status'] for w in ledger)),
        'excluded':dict(excluded),'wards':ledger,'records':records,
        'limitations':['Intervals are retrospective source spans, not proof of continuously active vision or unbroken entity tracking.',
            'Expiry/kill labels include parser fallbacks; killer identity and enemy removal are not inferred.',
            'Unknown end upper bounds can include very old wards; they are not a prediction of continued survival.',
            'After-match end records stay separate; neither censored nor unknown spans are silently merged with in-game ends.',
            'Cell union describes time with at least one recorded span, not area of vision, line of sight or ward quality.',
            'Parameters reuse games; this first interval view uses512-unit original grid and no pooled population estimate.']}
    body=json.dumps(result,ensure_ascii=False,indent=2).encode('utf-8')
    if len(body)>64*1024**2:
        raise ValueError('ward_intervals_output_byte_budget')
    output.mkdir(parents=True)
    (output/'interval-context.json').write_bytes(body)
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8',newline='\n')
    return result


def render(result,output,map_path):
    output=Path(output)
    raw=bounded(map_path,12*1024**2)
    if sha(raw)!='9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('ward_intervals_pinned_map_required')
    (output/'map.jpg').write_bytes(raw)
    used_matches={r['match_id'] for r in result['records']}
    display={k:v for k,v in result.items() if k not in ('protocol','limitations','wards')}
    display['wards']=[w for w in result['wards'] if w['match_id'] in used_matches]
    payload=json.dumps(display,ensure_ascii=False).replace('<','\\u003c')
    page=r'''<!doctype html><meta charset="utf-8"><title>眼位记录存续与未知结束</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}select{font:inherit;padding:5px;margin:5px;max-width:95vw}a{color:#8cd8c3}.warn{color:#f1cc87}.maps{display:grid;grid-template-columns:1fr 1fr;gap:12px}svg{width:100%;max-height:65vh}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #52677b;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<h1>窗口内：新放眼与已有记录存续分开看</h1><p class="warn">已记录放置至结束的区间只是存续代理，不是实际视野。局后结束单列；结束未知只显示可能时长上界，不能当作仍存活。击杀标签含解析器兜底，不能据此判断被敌方排掉。</p>
<label>中心距离<select id="lag"><option value="none">未限定</option><option>60</option><option>120</option><option>300</option></select></label><label>目标保护秒<select id="buffer"><option>0</option><option>30</option><option>90</option></select></label><p id="coverage"></p>
<label>已匹配目标<select id="pair"></select></label><label>阵营<select id="team"><option value="2">天辉</option><option value="3">夜魇</option></select></label><label>眼型<select id="kind"><option value="observer">假眼</option><option value="sentry">真眼</option></select></label><label>记录类型<select id="category"><option value="reported_in_game_end">局内已记结束区间</option><option value="reported_after_match_end">局后才记结束（单列）</option><option value="possible_unknown_end">结束未知：可能时长上界</option></select></label>
<p id="info"></p><p id="note"></p><p>四图同为512单位原网格。每格显示该类区间在窗内的并集秒数，同格重叠只计一次。数字不是视野面积；不同格子的秒数可重复，类别也不合并。“新放”是本窗所有类型的新记录数，“涉及眼数”仅属当前所选结束类型。底图未逐局校准。</p><div id="maps" class="maps"></div><table><thead><tr><th>窗口</th><th>严格边界内新放眼</th><th>该类涉及眼数 / 窗前已有</th><th>其中开局前放置</th><th>眼秒数合计 / 逐格并集秒数和</th></tr></thead><tbody id="totals"></tbody></table>
<h2>逐眼原记录与区间引用</h2><table><thead><tr><th>眼位索引 / 格子</th><th>放置 / 记录结束</th><th>原结束标签 / 原寿命状态</th><th>本窗区间 / 秒</th><th>窗前已有 / 开局前放置</th></tr></thead><tbody id="wards"></tbody></table><details><summary>所选四窗和原眼位字段</summary><pre id="refs"></pre></details><p><a href="interval-context.json">完整记录</a> · <a href="protocol.json">区间口径</a></p>
<script>const D=PAYLOAD,L=document.getElementById('lag'),B=document.getElementById('buffer'),P=document.getElementById('pair'),T=document.getElementById('team'),K=document.getElementById('kind'),C=document.getElementById('category'),names=['target_before','target_after','control_before','control_after'],labels=['目标前','目标后','对照前','对照后'],ns='http://www.w3.org/2000/svg',lookup=new Map(D.wards.map(w=>[w.match_id+':'+w.ward_index,w]));const time=v=>v===null?'未记录':(v<0?'-':'')+Math.floor(Math.abs(v)/60)+':'+(Math.abs(v)%60).toFixed(1).padStart(4,'0');function tr(id,vals){const r=document.createElement('tr');for(const v of vals){const c=document.createElement('td');c.textContent=v;r.append(c)}document.getElementById(id).append(r)}function node(t,attrs){const e=document.createElementNS(ns,t);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);return e}function xy(x,y){return[(261+(x-7487)/18322*8356)*1600/8878,(1-(y-7693)/18322)*8356*1600/8878]}
function draw(){for(const id of ['maps','totals','wards'])document.getElementById(id).replaceChildren();const r=D.records.find(r=>r.maximum_center_lag_s===(L.value==='none'?null:+L.value)&&r.buffer_s===+B.value&&r.match_id+':'+r.group_id===P.value&&r.team===+T.value&&r.ward_type===K.value);if(!r){document.getElementById('info').textContent='没有匹配对照，不计算零区间或零变化。';document.getElementById('note').textContent='';document.getElementById('refs').textContent='未匹配情况保留在覆盖中。';return}document.getElementById('info').textContent='比赛'+r.match_id+' / 构建'+(r.cohort[6]==='not_recorded'?'未记录':r.cohort[6])+'；目标'+time(r.target_center_s)+'，对照'+time(r.control_center_s)+'；各窗'+r.radius_s.toFixed(3)+'秒。';document.getElementById('note').textContent=C.value==='possible_unknown_end'?'本层是假定该眼尚未结束时的可能时长上界，已确认时长不据此增加；很早的旧眼也可能列入，不能读成仍存活。':C.value==='reported_after_match_end'?'本层只有局后才记录结束的区间，未与局内结束记录合并；不把截尾补成已确认存活。':'本层只用局内记录结束的放置—结束区间；不是经过连续可见性验证的实际视野。';const wardRefs=new Set();for(let i=0;i<4;i++){const w=r.windows[names[i]],cat=w.categories[C.value],div=document.createElement('div'),h=document.createElement('h3');h.textContent=labels[i]+'：'+cat.ward_count+'眼 / 新放'+w.new_placement_refs.length;div.append(h);const svg=node('svg',{viewBox:'0 0 1600 1505.9'});svg.append(node('image',{href:'map.jpg',width:1600,height:1505.9}));for(const cell of w.cells){const value=cell.categories[C.value];if(!value.ward_refs.length)continue;const[px,py]=xy(cell.grid_x*512,(cell.grid_y+1)*512),size=512/18322*8356*1600/8878,color=C.value==='possible_unknown_end'?'#ffb86a':C.value==='reported_after_match_end'?'#c4b1ff':'#7ee0ba',rect=node('rect',{x:px,y:py,width:size,height:size,fill:color,'fill-opacity':.12+.35*value.union_fraction_of_window,stroke:color,'stroke-width':3,'stroke-dasharray':C.value==='possible_unknown_end'?'8 5':'none'}),title=node('title',{});title.textContent='格'+cell.grid_x+','+cell.grid_y+' / #'+value.ward_refs.join(',')+' / 并集'+value.union_seconds.toFixed(3)+'秒';rect.append(title);svg.append(rect);const txt=node('text',{x:px+2,y:py+18,fill:color,'font-size':18,stroke:'#101820','stroke-width':3,'paint-order':'stroke'});txt.textContent=value.union_seconds.toFixed(0);svg.append(txt);for(const v of value.intervals){wardRefs.add(v.ward_index);const source=lookup.get(r.match_id+':'+v.ward_index);tr('wards',[labels[i]+' #'+v.ward_index+' / '+cell.grid_x+','+cell.grid_y,time(source.placed_s)+' / '+time(source.ended_s),source.end_kind+' / '+source.lifetime_status,time(v.start_s)+'～'+time(v.end_s)+' / '+v.seconds.toFixed(3),(v.placed_before_or_at_window_start?'是':'否')+' / '+(v.placed_pregame?'是':'否')])}}div.append(svg);document.getElementById('maps').append(div);tr('totals',[labels[i]+' '+time(w.start_s)+'～'+time(w.end_s),w.new_placement_refs.length,cat.ward_count+' / '+cat.carry_in_ward_refs.length,cat.pregame_ward_refs.length,cat.sum_ward_seconds.toFixed(3)+' / '+cat.sum_cell_union_seconds.toFixed(3)])}document.getElementById('refs').textContent=JSON.stringify({record:r,category:C.value,source_wards:[...wardRefs].map(i=>lookup.get(r.match_id+':'+i))},null,2)}function pairs(){P.replaceChildren();const limit=L.value==='none'?null:+L.value,c=D.coverage.find(c=>c.maximum_center_lag_s===limit&&c.buffer_s===+B.value);document.getElementById('coverage').textContent=c?'匹配'+(c.statuses.matched||0)+'/'+c.eligible_target_groups+'个足长目标组；短窗'+(c.statuses.short_target_not_eligible||0)+'不进入；未匹配不补零。':'';const rows=D.records.filter(r=>r.maximum_center_lag_s===limit&&r.buffer_s===+B.value&&r.team===2&&r.ward_type==='observer').sort((a,b)=>a.match_id.localeCompare(b.match_id)||a.target_center_s-b.target_center_s);for(const r of rows){const o=document.createElement('option');o.value=r.match_id+':'+r.group_id;o.textContent=r.match_id+' / '+time(r.target_center_s);P.append(o)}P.disabled=!rows.length;draw()}for(const e of[L,B])e.addEventListener('change',pairs);for(const e of[P,T,K,C])e.addEventListener('change',draw);pairs();</script>'''
    (output/'report.html').write_text(page.replace('PAYLOAD',payload),encoding='utf-8',newline='\n')
