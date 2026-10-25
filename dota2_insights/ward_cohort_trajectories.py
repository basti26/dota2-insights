"""Multi-phase placement states with one fixed complete-match population."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sqlite3

from .postgame_store import bounded, sha
from .postgame_metadata_store import resolved_metadata
from .ward_patterns import PHASE, key as ward_key
from .ward_team_phase_states import SCHEMA as INPUT_SCHEMA, STATE_NAMES
from .ward_interval_phases import WIDTHS

SCHEMA = 'ward_fixed_cohort_trajectories_v3'
# Chosen before reviewing trajectory counts. Adjacent comparisons already exist.
HORIZONS = ((0, 2), (0, 3), (0, 4), (1, 3), (1, 4), (2, 4))
RECURRENCE = ('radiant_any_phase', 'dire_any_phase', 'both_sides_any_phase_same_match',
              'radiant_two_or_more_phases', 'dire_two_or_more_phases',
              'both_sides_two_or_more_phases_same_match', 'radiant_every_phase', 'dire_every_phase')


def analyze(database, source_path, output):
    database, source_path, output = (Path(p).resolve() for p in (database, source_path, output))
    if output.exists():
        raise ValueError('cohort_trajectory_new_output_required')
    digest = sha(bounded(database, 128 * 1024**2))
    raw = bounded(source_path, 128 * 1024**2)
    source = json.loads(raw)
    if source.get('schema_version') != INPUT_SCHEMA or source.get('database_sha256') != digest:
        raise ValueError('cohort_trajectory_source_binding')
    c = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        matches = {r['match_id']: dict(r) for r in c.execute('SELECT * FROM matches')}
        metadata = resolved_metadata(c)
        wards = [dict(r) for r in c.execute("""SELECT match_id,ward_index,team,ward_type,x,y,placed_s
            FROM wards WHERE placement_status='in_game'
            AND coordinate_basis='gem_cell128_plus_vec_raw_coordinates' ORDER BY match_id,ward_index""")]
    finally:
        c.close()
    if len(matches) > 256 or len(wards) > 100_000 or source.get('placements') != wards:
        raise ValueError('cohort_trajectory_placement_identity_or_budget')
    membership = {}
    cohorts = source['cohorts']
    if len({r['cohort_id'] for r in cohorts}) != len(cohorts):
        raise ValueError('cohort_trajectory_duplicate_cohort')
    for cohort in cohorts:
        for mid in cohort['match_ids']:
            if mid not in matches or mid in membership:
                raise ValueError('cohort_trajectory_match_membership')
            expected = [*ward_key(matches[mid]), *[str(metadata[mid][f]) if metadata[mid][f] is not None
                                                  else 'not_recorded' for f in ('game_build', 'game_mode')]]
            if cohort['cohort'] != expected:
                raise ValueError('cohort_trajectory_resolved_metadata_changed')
            membership[mid] = cohort['cohort_id']
    if set(membership) != set(matches):
        raise ValueError('cohort_trajectory_match_coverage')
    buckets = defaultdict(list)
    for w in wards:
        if w['team'] not in (2, 3) or w['ward_type'] not in ('observer', 'sentry'):
            raise ValueError('cohort_trajectory_unknown_team_or_ward')
        buckets[membership[w['match_id']]].append(w)
    records = []
    for cohort in cohorts:
        cid = cohort['cohort_id']
        mids = sorted(cohort['match_ids'])
        for start, end in HORIZONS:
            phases = list(range(start, end + 1))
            common = [mid for mid in mids if matches[mid]['duration_s'] >= (end + 1) * PHASE]
            excluded = [mid for mid in mids if mid not in common]
            natural = [[mid for mid in mids if matches[mid]['duration_s'] >= (phase + 1) * PHASE]
                       for phase in phases]
            for kind in ('observer', 'sentry'):
                for width in WIDTHS:
                    for offset in (0, width // 2):
                        cells = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [[], []])))
                        for w in buckets[cid]:
                            phase = int(w['placed_s'] // PHASE)
                            if w['match_id'] not in common or phase not in phases or w['ward_type'] != kind:
                                continue
                            xy = math.floor((w['x'] - offset) / width), math.floor((w['y'] - offset) / width)
                            cells[xy][w['match_id']][phase][w['team'] - 2].append(w['ward_index'])
                        values = []
                        for (x, y), by_match in sorted(cells.items()):
                            paths = defaultdict(list)
                            counts = [[0] * 4 for _ in phases]
                            recurrence = {name: [] for name in RECURRENCE}
                            refs = []
                            for mid in common:
                                path = []
                                by_phase = by_match.get(mid, {})
                                for i, phase in enumerate(phases):
                                    pair = by_phase.get(phase, [[], []])
                                    state = int(bool(pair[0])) + 2 * int(bool(pair[1]))
                                    path.append(state)
                                    counts[i][state] += 1
                                    if state:
                                        refs.append({'match_id': mid, 'phase': phase,
                                                     'radiant_refs': sorted(pair[0]), 'dire_refs': sorted(pair[1])})
                                paths[tuple(path)].append(mid)
                                radiant = sum(bool(s & 1) for s in path)
                                dire = sum(bool(s & 2) for s in path)
                                flags = (radiant > 0, dire > 0, radiant > 0 and dire > 0,
                                         radiant >= 2, dire >= 2, radiant >= 2 and dire >= 2,
                                         radiant == len(phases), dire == len(phases))
                                for name, included in zip(RECURRENCE, flags):
                                    if included:
                                        recurrence[name].append(mid)
                            values.append({'grid_x': x, 'grid_y': y, 'phase_state_counts': counts,
                                           'trajectories': [{'states': list(path), 'match_ids': ids}
                                                            for path, ids in sorted(paths.items())],
                                           'nonempty_phase_refs': refs, 'recurrence_match_ids': recurrence,
                                           'one_match_removed_support_bounds': {
                                               metric: [max(0, len(ids) - 1), min(len(ids), len(common) - 1)]
                                               if len(common) >= 2 else None
                                               for metric, ids in recurrence.items()}})
                        records.append({'cohort_id': cid, 'cohort': cohort['cohort'], 'phases': phases,
                                        'ward_type': kind, 'cell_width': width, 'grid_offset': offset,
                                        'common_complete_match_ids': common,
                                        'excluded_not_complete_for_entire_horizon': excluded,
                                        'natural_complete_match_ids_by_phase': natural,
                                        'one_match_removed_denominator': len(common) - 1 if len(common) >= 2 else None,
                                        'one_match_removed_status': 'computed' if len(common) >= 2 else 'fewer_than_two_matches',
                                        'status': 'common_complete_population' if common else 'no_common_complete_matches',
                                        'cells': values})
    protocol = {'schema_version': SCHEMA, 'database_sha256': digest, 'source_team_phase_sha256': sha(raw),
                'phase_seconds': PHASE, 'state_order': list(STATE_NAMES), 'horizons': [list(x) for x in HORIZONS],
                'candidate_cells': 'all_cells_observed_in_common_population_across_entire_horizon',
                'zero_record_matches_retained_per_cell': True, 'same_match_ids_in_every_phase': True,
                'new_placements_only_not_ward_lifetimes': True, 'no_support_or_outcome_filter': True,
                'recurrence_metrics': list(RECURRENCE),
                'recurrence_basis': 'distinct_phases_per_team_per_match_not_ward_count',
                'both_sides_basis': 'each_team_recorded_somewhere_in_horizon_same_match_not_same_time',
                'one_match_removed_bound_order': ['minimum_support', 'maximum_support'],
                'one_match_removed_basis': 'remove_each_original_match_once_fixed_cohort_horizon_cells_metric_no_reselection',
                'one_match_removed_interpretation': 'descriptive_support_sensitivity_not_confidence_interval_or_new_validation',
                'limits': ['common_population_conditions_on_match_duration_not_all_matches',
                           'same_records_regrouped_not_independent_validation',
                           'unknown_build_not_same_version_map_not_certified',
                           'no_placement_not_no_vision_state_change_not_control_or_ward_owner_change',
                           'grids_and_horizons_overlap_not_independent_samples']}
    result = {**protocol, 'cohorts': cohorts, 'placements': wards, 'records': records,
              'counts': {'strata': len(records), 'strata_with_common_matches': sum(bool(r['common_complete_match_ids']) for r in records),
                         'cell_trajectories': sum(len(r['cells']) for r in records)}}
    if sha(bounded(database, 128 * 1024**2)) != digest or sha(bounded(source_path, 128 * 1024**2)) != sha(raw):
        raise ValueError('cohort_trajectory_inputs_changed')
    output.mkdir(parents=True)
    for name, value in (('cohort-trajectories.json', result), ('protocol.json', protocol)):
        (output / name).write_text(json.dumps(value, separators=(',', ':')), encoding='utf-8', newline='\n')
    return result


def render(result, output, map_path=None, phase_context=None):
    output = Path(output)
    if phase_context is not None:
        from .ward_phase_context import SCHEMA as CONTEXT_SCHEMA
        if (phase_context.get('schema_version') != CONTEXT_SCHEMA
                or phase_context.get('database_sha256') != result['database_sha256']
                or phase_context.get('source_team_phase_sha256') != result['source_team_phase_sha256']):
            raise ValueError('cohort_trajectory_phase_context_binding')
    map_meta = None
    if map_path is not None:
        raw = bounded(map_path, 12 * 1024**2)
        digest = sha(raw)
        if digest != '9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
            raise ValueError('cohort_trajectory_pinned_illustrative_map_required')
        (output / 'map.jpg').write_bytes(raw)
        map_meta = {'map_sha256': digest, 'status': 'illustrative_map_not_certified_per_build',
                    'image_width': 8878, 'image_height': 8356, 'square_left': 261, 'square_side': 8356,
                    'world_x_min': 7487, 'world_y_min': 7693, 'world_side': 18322, 'flip_y': True}
    (output / 'map-reference.json').write_text(json.dumps(map_meta, indent=2), encoding='utf-8', newline='\n')
    # JSON escapes '<' so original metadata cannot terminate the data element.
    data = json.dumps({**result, 'map_reference': map_meta, 'phase_context': phase_context}, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    page = r'''<!doctype html><meta charset="utf-8"><title>固定比赛的连续时段眼区记录</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:25px}select{max-width:100%;padding:6px;margin:5px}td,th{padding:8px;border-bottom:1px solid #52677b;text-align:left}table{border-collapse:collapse;width:100%}a{color:#8cd8c3}.warn{color:#f1cc87}details{max-width:650px}pre{white-space:pre-wrap}</style>
<h1>固定同一批比赛，连续看眼区记录</h1><p><a href="../index.html">返回总览</a> · <a href="../team-phases/report.html">原眼位地图</a> · <a href="cohort-trajectories.json">全部原始引用</a></p>
<p>每个比较区间固定能完整覆盖所有时段的比赛。不同比赛各计一次，不按放眼数量加权；全零比赛也留在格子的分母里。只比较本段新放眼，不表示实际视野或控制权。</p>
<p class="warn">这是按比赛时长筛选后的历史子集，不能代表全部比赛。不同区间、网格重复使用原记录，不能当成独立验证；未知版本与未认证地图仍保留。底图仅为位置参考，未按录像构建认证；同场双方曾放不等于同时有视野。</p>
<label for="cohort">资料组</label><select id="cohort"></select><label for="horizon">连续区间</label><select id="horizon"></select><label for="kind">眼型</label><select id="kind"><option value="observer">侦查守卫</option><option value="sentry">岗哨守卫</option></select><label for="grid">网格</label><select id="grid"></select>
<p id="coverage"></p><label for="metric">地图计数</label><select id="metric"></select><p id="map-note"></p>
<svg id="map" viewBox="0 0 1600 1505.9" style="width:100%;max-width:1100px"><image href="map.jpg" width="1600" height="1505.9"/><g id="cells"></g><g id="points"></g></svg>
<h2>选中眼区的原始记录</h2><p id="selected"></p><pre id="point-details" style="white-space:pre-wrap;max-height:260px;overflow:auto"></pre>
<h2>这项记录受单场比赛影响多大</h2><p id="single-impact"></p><details><summary>逐一删去一场的全部结果（固定格子和条件）</summary><table><thead><tr><th>删去比赛</th><th>删去后支持数 / 剩余分母</th></tr></thead><tbody id="single-impact-rows"></tbody></table></details>
<h2>本格每场、每段的记录上下文</h2><p>每个完整比赛都保留，包括本段没有在该格放眼的比赛。经济只列段开始前、最多90秒旧的同源完整采样包原计数；不填补、不用GSI或公开曲线替代。段中事件是之后发生的全图记录，尚未核验与所选格的空间关系，不能当成段前条件。建筑阵营按目标名称标注，不拿攻击者阵营替代；同名记录保留，不据名称合并成一座建筑。缺来源和未记录不补零；塔记录数不是剩余塔数。</p>
<p id="context-note"></p><table><thead><tr><th>比赛 / 时段</th><th>本格状态</th><th>段前天辉 / 夜魇原始计数</th><th>采样游戏时间 / 距段开始</th><th>段前已记录塔摧毁数</th><th>本段已记录目标（仅时间）</th></tr></thead><tbody id="phase-context"></tbody></table>
<table><thead id="head"></thead><tbody id="rows"></tbody></table>
<script id="data" type="application/json">__DATA__</script><script>
const D=JSON.parse(document.querySelector('#data').textContent),names=['未记录放置','仅天辉','仅夜魇','双方均有'];
const $=id=>document.getElementById(id),esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels=['天辉曾放置','夜魇曾放置','同场双方曾放置（可不同段）','天辉跨至少两段放置','夜魇跨至少两段放置','同场双方各跨至少两段','天辉每段均放置','夜魇每段均放置'];
let current=null,selectedCell=null;const ns='http://www.w3.org/2000/svg',positions=new Map(D.placements.map(w=>[w.match_id+':'+w.ward_index,w]));
const contextIndex=new Map((D.phase_context?.records||[]).map(r=>[r.match_id+':'+r.phase,r])),contextEvents=new Map((D.phase_context?.events||[]).map(e=>[e.event_id,e]));
function targetNameLabel(e,families){const label=families[e.family]||e.family,a=e.target_name_attributes;if(!a)return label;if(a.status==='parsed_name_family_conflict')return label+'（名称与事件类型待核对）';if(a.status!=='parsed_name_family_consistent')return label+(a.status==='missing_name'?'（目标名称未记录）':'（目标名称待核对）');const side={2:'天辉',3:'夜魇'}[a.defending_team]||'',lane={top:'上路',mid:'中路',bot:'下路'}[a.lane]||'';if(a.kind==='tower')return label+'（'+side+lane+{1:'一',2:'二',3:'三',4:'四'}[a.tower_tier]+'塔）';if(a.kind==='barracks')return label+'（'+side+lane+(a.barracks_type==='melee'?'近战':'远程')+'兵营）';if(a.kind==='ancient')return label+'（'+side+'遗迹）';return label}
function phaseDetails(c){$('phase-context').replaceChildren();$('context-note').textContent=D.phase_context?'来自同一事实库的逐段记录，数值含义仍需核对；点击所选眼区后同步显示。':'本报告未提供上下文资料，不能据此判断没有事件或经济。';if(!c||!D.phase_context)return;
const statuses={context_not_recorded:'本局未记录上下文',public_projection_clock_unverified:'公开资料时钟未对齐',source_rejected:'来源核对未通过',net_worth_not_recorded_for_source:'来源未采此经济字段',no_prior_in_game_packet:'段前无游戏内采样',prior_packet_too_old:'段前采样超龄',player_resource_mapping_incomplete:'玩家映射不完整',net_worth_field_missing_or_invalid:'五人值不全'},families={tower_destroyed:'塔摧毁',barracks_destroyed:'兵营摧毁',ancient_destroyed:'遗迹摧毁',other_building:'其他建筑',roshan_kill:'肉山击杀记录',roshan_death_record:'肉山死亡记录'},clock=v=>Math.floor(v/60)+':'+(v%60).toFixed(3).padStart(6,'0');
for(const mid of current.common_complete_match_ids)for(const phase of current.phases){const row=contextIndex.get(mid+':'+phase),ref=c.nonempty_phase_refs.find(r=>r.match_id===mid&&r.phase===phase),state=(ref?.radiant_refs.length?1:0)+(ref?.dire_refs.length?2:0);let values;if(!row)values=[mid+' / '+phase*10+'～'+(phase+1)*10+'分钟',names[state],'缺少该段资料','—','—','缺少该段资料'];else{const cp=row.pre_phase_checkpoint,sums=cp.raw_team_counter_sums,missing=row.source_status!=='same_replay_manifest_clock_bound',events=row.phase_event_ids.map(id=>contextEvents.get(id)),targets=missing?(statuses[row.source_status]||'来源状态未知'):events.length?events.map(e=>targetNameLabel(e,families)+' #'+e.event_index+' '+clock(e.game_time_s)).join('；'):'本段无已记录目标，覆盖有限';values=[mid+' / '+phase*10+'～'+(phase+1)*10+'分钟',names[state],cp.status==='reported_raw_team_counters'?sums['2']+' / '+sums['3']:(statuses[cp.status]||cp.status),cp.packet_ref?clock(cp.packet_ref.time_s)+' / '+cp.sample_age_s.toFixed(3)+'秒':'—',missing?'未知':String(row.before_start_event_family_counts.tower_destroyed||0),targets]}const tr=document.createElement('tr');for(const v of values){const td=document.createElement('td');td.textContent=v;tr.append(td)}$('phase-context').append(tr)}}
function node(tag,attrs){const n=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);return n}
function xy(x,y){return[(261+(x-7487)/18322*8356)*1600/8878,(1-(y-7693)/18322)*8356*1600/8878]}
function singleImpact(c){$('single-impact-rows').replaceChildren();if(!c){$('single-impact').textContent='没有候选格或共同完整比赛，不计算单场影响。';return}const n=current.common_complete_match_ids.length,key=$('metric').value,support=c.recurrence_match_ids[key],bounds=c.one_match_removed_support_bounds[key],index=D.recurrence_metrics.indexOf(key);$('single-impact').textContent=n<2?'原分母仅1场，删去后没有剩余比赛，不能计算比例。':labels[index]+'原支持 '+support.length+'/'+n+'；逐一删去任意1场后，支持范围 '+bounds[0]+'/'+(n-1)+' ～ '+bounds[1]+'/'+(n-1)+'。格子、分组与条件固定，不重新挑选；这是单场影响范围，不是置信区间或独立验证。地图颜色仍表示原支持比例。';for(const mid of current.common_complete_match_ids){const tr=document.createElement('tr');for(const value of [mid,n<2?'无剩余比赛，未计算比例':(support.length-Number(support.includes(mid)))+' / '+(n-1)]){const td=document.createElement('td');td.textContent=value;tr.append(td)}$('single-impact-rows').append(tr)}}
for(let i=0;i<D.recurrence_metrics.length;i++){const o=document.createElement('option');o.value=D.recurrence_metrics[i];o.textContent=labels[i];$('metric').append(o)}$('metric').value='both_sides_two_or_more_phases_same_match';
for(let i=0;i<D.cohorts.length;i++){const c=D.cohorts[i],o=document.createElement('option');o.value=c.cohort_id;o.textContent='第'+(i+1)+'组 · '+(c.cohort[0]==='own'?'本人':'高分')+' · 构建 '+(c.cohort[c.cohort.length-2]==='not_recorded'?'未记录':c.cohort[c.cohort.length-2])+' · '+c.match_ids.length+'场';$('cohort').append(o)}
for(const h of D.horizons){const o=document.createElement('option');o.value=h.join(',');o.textContent=(h[0]*10)+'～'+((h[1]+1)*10)+'分钟';$('horizon').append(o)}
for(const w of [512,1024,2048])for(const off of [0,w/2]){const o=document.createElement('option');o.value=w+','+off;o.textContent=w+' / 偏移'+off;$('grid').append(o)}
function detail(){const c=current?.cells.find(c=>c.grid_x+','+c.grid_y===selectedCell);$('points').replaceChildren();phaseDetails(c);singleImpact(c);if(!c){$('selected').textContent='没有可查看的候选格。';$('point-details').textContent='';return}
const index=D.recurrence_metrics.indexOf($('metric').value),support=c.recurrence_match_ids[$('metric').value];$('selected').textContent='格 '+selectedCell+'：'+labels[index]+' '+support.length+' / '+current.common_complete_match_ids.length+'场；比赛 '+(support.join('、')||'无')+'。蓝点天辉、红点夜魇，显示整个区间全部原放置；重复阶段按不同10分钟段计数。';
const items=[];for(const ref of c.nonempty_phase_refs)for(const key of ['radiant_refs','dire_refs'])for(const i of ref[key]){const w=positions.get(ref.match_id+':'+i);if(w)items.push(w)}items.sort((a,b)=>a.match_id.localeCompare(b.match_id)||a.placed_s-b.placed_s||a.ward_index-b.ward_index);
const lines=[];for(const w of items){const label=w.match_id+' #'+w.ward_index+' '+(w.team===2?'天辉':'夜魇')+' '+Math.floor(w.placed_s/60)+':'+(w.placed_s%60).toFixed(3).padStart(6,'0')+' 坐标('+w.x+','+w.y+')';lines.push(label);if(D.map_reference){const [x,y]=xy(w.x,w.y),point=node('circle',{cx:x,cy:y,r:7,fill:w.team===2?'#77caff':'#ff9292',stroke:'#10202d','stroke-width':2}),title=node('title',{});title.textContent=label;point.append(title);$('points').append(point)}}$('point-details').textContent=lines.join('\n')}
function drawMap(){$('cells').replaceChildren();$('map').style.display=D.map_reference?'block':'none';const r=current,key=$('metric').value,n=r.common_complete_match_ids.length,index=D.recurrence_metrics.indexOf(key);$('map-note').textContent=(D.map_reference?'参考底图：':'未提供底图：')+labels[index]+'；数值为满足条件的不同比赛数 / 固定'+n+'场。所有候选格均保留，灰框表示该指标为零；不是眼位评分或实际视野。';
if(D.map_reference)for(const c of r.cells){const value=n?c.recurrence_match_ids[key].length/n:0,[x,y]=xy(c.grid_x*r.cell_width+r.grid_offset,c.grid_y*r.cell_width+r.grid_offset+r.cell_width),size=r.cell_width/18322*8356*1600/8878,chosen=c.grid_x+','+c.grid_y===selectedCell,rect=node('rect',{x,y,width:size,height:size,fill:value?'#8bdab9':'#83909b','fill-opacity':value ? .12+.55*value : .03,stroke:chosen?'#fff2a4':value?'#8bdab9':'#83909b','stroke-width':chosen?4:1.5}),title=node('title',{});title.textContent='格'+c.grid_x+','+c.grid_y+' '+c.recurrence_match_ids[key].length+'/'+n+'场';rect.append(title);rect.addEventListener('click',()=>pick(c.grid_x+','+c.grid_y));$('cells').append(rect)}detail()}
function pick(cell){selectedCell=cell;drawMap()}
function show(){const [a,b]=$('horizon').value.split(',').map(Number),[w,off]=$('grid').value.split(',').map(Number);const r=D.records.find(x=>x.cohort_id===$('cohort').value&&x.phases[0]===a&&x.phases[x.phases.length-1]===b&&x.ward_type===$('kind').value&&x.cell_width===w&&x.grid_offset===off);const n=r.common_complete_match_ids.length;
current=r;selectedCell=r.cells.length?r.cells[0].grid_x+','+r.cells[0].grid_y:null;
$('coverage').textContent='固定分母 '+n+'场：'+r.common_complete_match_ids.join('、')+'；各段原完整分母 '+r.natural_complete_match_ids_by_phase.map(x=>x.length).join(' / ')+'；未完整覆盖整个区间的比赛：'+(r.excluded_not_complete_for_entire_horizon.join('、')||'无')+'。候选格 '+r.cells.length+'。'+(!n?'没有共同完整比赛，不能比较。':'');
$('head').innerHTML='<tr><th>格子</th>'+r.phases.map(p=>'<th>'+p*10+'～'+(p+1)*10+'分钟<br>未放 / 天辉 / 夜魇 / 双方</th>').join('')+'<th>同场连续状态与出处</th></tr>';
$('rows').innerHTML=r.cells.map(c=>'<tr><td><button data-cell="'+c.grid_x+','+c.grid_y+'">'+c.grid_x+','+c.grid_y+'</button></td>'+c.phase_state_counts.map(x=>'<td>'+x.join(' / ')+'</td>').join('')+'<td><details><summary>'+c.trajectories.length+'种记录路径 · '+n+'场</summary>'+c.trajectories.map(t=>'<p>'+t.states.map(s=>names[s]).join(' → ')+'：'+esc(t.match_ids.join('、'))+'</p>').join('')+'<pre>'+esc(c.nonempty_phase_refs.map(x=>x.match_id+' '+x.phase*10+'～'+(x.phase+1)*10+'分钟 天辉索引['+x.radiant_refs+'] 夜魇索引['+x.dire_refs+']').join('\n'))+'</pre></details></td></tr>').join('');for(const b of $('rows').querySelectorAll('button[data-cell]'))b.addEventListener('click',()=>pick(b.dataset.cell));drawMap()}
$('metric').addEventListener('change',drawMap);for(const id of ['cohort','horizon','kind','grid'])$(id).addEventListener('change',show);show();</script>'''
    (Path(output) / 'report.html').write_text(page.replace('__DATA__', data), encoding='utf-8', newline='\n')
