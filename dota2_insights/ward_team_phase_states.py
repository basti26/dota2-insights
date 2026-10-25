"""Paired team placement states and adjacent-phase transitions, descriptive only."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sqlite3

from .postgame_store import bounded, sha
from .postgame_metadata_store import resolved_metadata
from .ward_patterns import PHASE, key as ward_key
from .ward_interval_phases import SCHEMA as PHASE_SCHEMA, WIDTHS

SCHEMA = 'ward_two_team_phase_states_v1'
STATE_NAMES = ('neither_recorded', 'radiant_only', 'dire_only', 'both_recorded')


def state(row):
    return int(bool(row and row['radiant_refs'])) + 2 * int(bool(row and row['dire_refs']))


def summarize(records, match_ids):
    counts = Counter(state(row) for row in records.values())
    counts[0] += len(match_ids) - len(records)
    if sum(counts.values()) != len(match_ids) or counts[0] < 0:
        raise ValueError('team_phase_population_mismatch')
    return [counts[i] for i in range(4)]


def analyze(database, phase_path, output):
    database, phase_path, output = (Path(p).resolve() for p in (database, phase_path, output))
    if output.exists():
        raise ValueError('team_phase_new_output_required')
    digest = sha(bounded(database, 128 * 1024**2))
    raw = bounded(phase_path, 128 * 1024**2)
    source = json.loads(raw)
    if source.get('schema_version') != PHASE_SCHEMA or source.get('database_sha256') != digest:
        raise ValueError('team_phase_source_binding')
    c = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        matches = {r['match_id']: dict(r) for r in c.execute('SELECT * FROM matches')}
        metadata = resolved_metadata(c)
        wards = [dict(r) for r in c.execute("""SELECT match_id,ward_index,team,ward_type,x,y,placed_s
            FROM wards WHERE placement_status='in_game'
            AND coordinate_basis='gem_cell128_plus_vec_raw_coordinates'
            ORDER BY match_id,ward_index""")]
    finally:
        c.close()
    if len(matches) > 256 or len(wards) > 100_000:
        raise ValueError('team_phase_source_budget')
    cohort_by_match, cohort_keys = {}, {}
    for cohort in source['cohorts']:
        cid = cohort['cohort_id']
        if cid in cohort_keys:
            raise ValueError('team_phase_duplicate_cohort')
        cohort_keys[cid] = cohort['cohort']
        for mid in cohort['match_ids']:
            if mid not in matches or mid in cohort_by_match:
                raise ValueError('team_phase_cohort_match_set')
            m = matches[mid]
            expected = [*ward_key(m), *[str(metadata[mid][f]) if metadata[mid][f] is not None
                                      else 'not_recorded' for f in ('game_build', 'game_mode')]]
            if cohort['cohort'] != expected:
                raise ValueError('team_phase_resolved_cohort_changed')
            cohort_by_match[mid] = cid
    if set(cohort_by_match) != set(matches):
        raise ValueError('team_phase_missing_match')
    populations = {}
    expected_keys = set()
    for cid in cohort_keys:
        mids = sorted(mid for mid in matches if cohort_by_match[mid] == cid)
        count = math.ceil(max(matches[mid]['duration_s'] for mid in mids) / PHASE)
        if count > 36:
            raise ValueError('team_phase_time_budget')
        expected_keys.update((cid, p) for p in range(count))
    for pop in source['populations']:
        cid, p = pop['cohort_id'], pop['phase']
        if (cid, p) not in expected_keys or (cid, p) in populations:
            raise ValueError('team_phase_population_keys')
        mids = {mid for mid in matches if cohort_by_match[mid] == cid}
        full = sorted(mid for mid in mids if matches[mid]['duration_s'] >= (p + 1) * PHASE)
        partial = sorted(mid for mid in mids if p * PHASE < matches[mid]['duration_s'] < (p + 1) * PHASE)
        if (pop['complete_match_ids'] != full or pop['partial_match_ids_reference_only'] != partial
                or pop['ended_before_phase_ids'] != sorted(mids - set(full) - set(partial))
                or pop['start_s'] != p * PHASE or pop['end_s'] != (p + 1) * PHASE):
            raise ValueError('team_phase_full_population_changed')
        populations[cid, p] = pop
    if set(populations) != expected_keys:
        raise ValueError('team_phase_population_coverage')
    references = {(w['match_id'], w['ward_index']): w for w in wards}
    input_refs = {(r['match_id'], r['ward_index']): r for r in source['placements']}
    if set(input_refs) != set(references) or len(input_refs) != len(source['placements']):
        raise ValueError('team_phase_new_placement_set_changed')
    buckets = defaultdict(lambda: defaultdict(dict))
    partial = 0
    for w in wards:
        cid, p = cohort_by_match[w['match_id']], int(w['placed_s'] // PHASE)
        pop = populations[cid, p]
        eligible = w['match_id'] in pop['complete_match_ids']
        ref = input_refs[w['match_id'], w['ward_index']]
        if ref['phase'] != p or ref['summary_eligible'] != eligible:
            raise ValueError('team_phase_placement_phase_changed')
        if not eligible:
            partial += 1
            continue
        if w['team'] not in (2, 3) or w['ward_type'] not in ('observer', 'sentry'):
            raise ValueError('team_phase_unsupported_team_or_type')
        for width in WIDTHS:
            for offset in (0, width // 2):
                cell = math.floor((w['x'] - offset) / width), math.floor((w['y'] - offset) / width)
                data = buckets[cid, p, w['ward_type'], width, offset][cell].setdefault(
                    w['match_id'], {'radiant_refs': [], 'dire_refs': []})
                data['radiant_refs' if w['team'] == 2 else 'dire_refs'].append(w['ward_index'])
    summaries, paired = [], []
    for (cid, p), pop in sorted(populations.items()):
        full = pop['complete_match_ids']
        for kind in ('observer', 'sentry'):
            for width in WIDTHS:
                for offset in (0, width // 2):
                    key = cid, p, kind, width, offset
                    cells = []
                    for (x, y), records in sorted(buckets[key].items()):
                        counts = summarize(records, full)
                        cells.append({'grid_x': x, 'grid_y': y, 'state_counts': counts,
                                      'state_proportions': [n / len(full) for n in counts],
                                      'nonempty_match_records': [dict(match_id=mid, state=state(r), **r)
                                                                 for mid, r in sorted(records.items())]})
                    common_fields = {'cohort_id': cid, 'cohort': cohort_keys[cid], 'ward_type': kind,
                                     'cell_width': width, 'grid_offset': offset}
                    summaries.append({**common_fields, 'phase': p, 'complete_match_ids': full,
                                      'complete_match_count': len(full), 'cells': cells,
                                      'status': 'complete_population' if full else 'no_complete_matches'})
                    if (cid, p + 1) not in populations:
                        continue
                    common = sorted(set(full) & set(populations[cid, p + 1]['complete_match_ids']))
                    sides = []
                    for phase in (p, p + 1):
                        sides.append({cell: {mid: r for mid, r in records.items() if mid in common}
                                      for cell, records in buckets[cid, phase, kind, width, offset].items()
                                      if any(mid in common for mid in records)})
                    transitions = []
                    for x, y in sorted(set(sides[0]) | set(sides[1])):
                        matrix = [[0] * 4 for _ in range(4)]
                        match_records = []
                        for mid in common:
                            before, after = (side.get((x, y), {}).get(mid) for side in sides)
                            a, b = state(before), state(after)
                            matrix[a][b] += 1
                            if a or b:
                                match_records.append({'match_id': mid, 'before_state': a, 'after_state': b,
                                                      'before_refs': before, 'after_refs': after})
                        if sum(map(sum, matrix)) != len(common):
                            raise ValueError('team_phase_matrix_denominator')
                        transitions.append({'grid_x': x, 'grid_y': y, 'transition_counts': matrix,
                                            'before_state_counts': list(map(sum, matrix)),
                                            'after_state_counts': [sum(row[i] for row in matrix) for i in range(4)],
                                            'nonempty_match_records': match_records})
                    paired.append({**common_fields, 'before_phase': p, 'after_phase': p + 1,
                                   'common_complete_match_ids': common, 'cells': transitions,
                                   'status': 'common_complete_population' if common else 'no_common_complete_matches'})
    if sum(len(s['cells']) for s in summaries + paired) > 250_000:
        raise ValueError('team_phase_cell_budget')
    if sha(bounded(database, 128 * 1024**2)) != digest or sha(bounded(phase_path, 128 * 1024**2)) != sha(raw):
        raise ValueError('team_phase_inputs_changed')
    protocol = {'schema_version': SCHEMA, 'database_sha256': digest, 'source_phase_sha256': sha(raw),
                'phase_seconds': PHASE, 'state_order': list(STATE_NAMES), 'widths': list(WIDTHS),
                'offsets': 'zero_and_half_width', 'zero_record_matches_retained': True,
                'adjacent_phases_use_common_complete_matches_only': True,
                'candidate_cells': 'union_of_recorded_placements_in_this_population_not_all_map_cells',
                'new_placements_only': True, 'end_status_not_used_to_filter_placements': True,
                'pregame_and_partial_phase_not_in_summary': True,
                'no_support_threshold_or_outcome_based_selection': True,
                'meaning': 'same_cell_and_ten_minute_phase_not_simultaneous_vision_control_or_preference_cause',
                'limitations': ['unknown_build_is_not_same_version', 'map_not_certified_per_build',
                                'same_records_regrouped_not_independent_replication',
                                'neither_recorded_is_not_proof_of_no_vision',
                                'state_change_not_the_same_ward_changing_owner']}
    result = {**protocol, 'cohorts': source['cohorts'], 'populations': source['populations'],
              'placements': wards, 'partial_phase_placement_count_reference_only': partial,
              'summaries': summaries, 'paired_phase_states': paired}
    output.mkdir(parents=True)
    for name, value in (('team-phase-states.json', result), ('protocol.json', protocol)):
        (output / name).write_text(json.dumps(value, separators=(',', ':')), encoding='utf-8', newline='\n')
    return result


def render(result, output, map_path, sequences, sequence_context):
    output = Path(output)
    if (sequences.get('database_sha256') != result['database_sha256']
            or sequences.get('source_team_phase_sha256') != sha(bounded(output / 'team-phase-states.json', 128 * 1024**2))):
        raise ValueError('team_phase_sequence_binding')
    if (sequence_context.get('database_sha256') != result['database_sha256']
            or sequence_context.get('source_sequence_sha256') != sha(bounded(output / 'placement-sequences.json', 128 * 1024**2))):
        raise ValueError('team_phase_sequence_context_binding')
    raw = bounded(map_path, 12 * 1024**2)
    if sha(raw) != '9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('team_phase_pinned_map')
    (output / 'map.jpg').write_bytes(raw)
    display = {k: result[k] for k in ('cohorts', 'populations', 'summaries', 'paired_phase_states')}
    display['placement_sequences'] = sequences['timelines']
    display['sequence_context'] = {k: sequence_context[k] for k in ('source_decisions', 'target_events', 'batch_contexts')}
    payload = json.dumps(display, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    page = r'''<!doctype html><meta charset="utf-8"><title>双阵营放眼与相邻时段</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}select{font:inherit;padding:5px;margin:5px;max-width:95vw}a{color:#8cd8c3}.warn{color:#f1cc87}table{border-collapse:collapse;width:100%;margin:12px 0}td,th{padding:8px;border-bottom:1px solid #52677b;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}svg{width:100%;height:auto;max-width:1400px;display:block}</style>
<h1>同一批比赛：双方在哪些区域留下放眼记录</h1>
<p class="warn">只计本时段新放眼；“双方都有”不表示同时有视野、有效控制或偏好原因。相邻时段状态变化不表示同一支眼换了主人。未知版本不当作同版本，底图尚未逐构建认证。</p>
<label for="cohort">来源群体</label><select id="cohort"></select>
<label for="phase">时段</label><select id="phase"></select>
<label for="kind">眼型</label><select id="kind"><option value="observer">假眼</option><option value="sentry">真眼</option></select>
<label for="width">格宽</label><select id="width"><option>512</option><option>1024</option><option>2048</option></select>
<label for="offset">边界</label><select id="offset"><option value="0">原网格</option><option value="half">偏移半格</option></select>
<label for="metric">地图数值</label><select id="metric"><option value="3">双方都有记录的比例</option><option value="1">仅天辉有记录的比例</option><option value="2">仅夜魇有记录的比例</option><option value="any">任一方有记录的比例</option></select>
<p id="condition"></p><p id="coverage"></p><p id="context-coverage"></p><p>四种状态互斥，总数等于完整比赛数。未在候选格留记录的比赛仍在分母；不足10分钟的尾段只作参考。只列本群体有放置记录的候选格，不能据此覆盖地图全部位置。灰框表示所选指标为零，不贴百分比文字；完整数值留在下表。</p>
<svg viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/><g id="cells"></g></svg>
<h2>本段候选格：同场双方记录配对</h2><table><thead><tr><th>格子</th><th>仅天辉</th><th>仅夜魇</th><th>双方都有</th><th>双方都无记录</th><th>完整比赛分母</th></tr></thead><tbody id="summary"></tbody></table>
<h2>相邻时段：四种状态之间的16种变化</h2><p id="paired"></p>
<label for="cell">查看格子（含仅下一段出现的候选）</label><select id="cell"></select>
<table><thead><tr><th>前段 → 后段</th><th>双方都无记录</th><th>仅天辉</th><th>仅夜魇</th><th>双方都有</th></tr></thead><tbody id="matrix"></tbody></table>
<h3>本格逐场原索引</h3><p>本段表保留全部完整比赛；配对变化仅使用两段都完整的比赛。编号是原眼位索引；没有记录不表示没有视野。</p>
<table><thead><tr><th>比赛</th><th>本段状态</th><th>天辉 / 夜魇眼位索引</th><th>配对前 → 后</th><th>后段天辉 / 夜魇眼位索引</th></tr></thead><tbody id="matches"></tbody></table>
<h2>本格放置时间线：本段及下一段</h2><p>每行是一批原始时刻相同的放置，同刻内部不猜先后。相邻批次可跨时段；尾段记录仅供回看。最后一批只知道到比赛结束为止没有更晚记录，不能当作以后不再放眼。先后放置不等于排眼或控制权交接。</p>
<p>目标事件只关联同录像、同暂停时钟且包时间可复算的记录，范围为放置前后90秒。事件缺失和公开API时钟未对齐分别标注；时间近邻尚无空间认证，不能用来判断当时视野或放眼原因。</p>
<table><thead><tr><th>比赛</th><th>游戏时间</th><th>阵营批次 / 天辉与夜魇索引</th><th>距同格上一批</th><th>相邻位置距离范围</th><th>时段与结束随访</th><th>已记录目标事件（前后90秒，仅时间）</th></tr></thead><tbody id="timeline"></tbody></table>
<details><summary>本条件、原分母和相邻段引用</summary><pre id="refs"></pre></details>
<p><a href="team-phase-states.json">完整数据与原放眼时间/坐标</a> · <a href="protocol.json">统计口径</a> · <a href="placement-sequences.json">全部批次和严格相邻时序</a> · <a href="sequence-protocol.json">时序口径</a> · <a href="sequence-target-context.json">目标事件引用、时钟与缺失状态</a></p>
<script>const D=PAYLOAD,ids=['cohort','phase','kind','width','offset','metric','cell'],E=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)])),names=['双方都无记录','仅天辉','仅夜魇','双方都有'],ns='http://www.w3.org/2000/svg';let selectedSummary=null,selectedPair=null;
const meta=v=>v==='not_recorded'?'未记录':v;
function tr(id,values){const r=document.createElement('tr');for(const value of values){const c=document.createElement('td');c.textContent=value;r.append(c)}document.getElementById(id).append(r)}
function node(tag,attrs){const n=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);return n}
function xy(x,y){return[(261+(x-7487)/18322*8356)*1600/8878,(1-(y-7693)/18322)*8356*1600/8878]}
const sequencePositions=new Map(D.placement_sequences.map((r,i)=>[r,i])),batchContexts=new Map(D.sequence_context.batch_contexts.map(r=>[r.sequence_index+':'+r.batch_index,r])),targetEvents=new Map(D.sequence_context.target_events.map(r=>[r.event_id,r]));
function targetLabel(e){const unit=e.target_unit||'',tower=unit.match(/^npc_dota_(goodguys|badguys)_tower([1-4])(?:_(top|mid|bot))?$/),rax=unit.match(/^npc_dota_(goodguys|badguys)_(melee|range)_rax_(top|mid|bot)$/),side=x=>x==='goodguys'?'天辉':'夜魇',lane={top:'上路',mid:'中路',bot:'下路'};if(tower)return side(tower[1])+(lane[tower[3]]||'')+{1:'一塔',2:'二塔',3:'三塔',4:'四塔'}[tower[2]];if(rax)return side(rax[1])+lane[rax[3]]+(rax[2]==='melee'?'近战兵营':'远程兵营');if(unit==='npc_dota_goodguys_fort')return '天辉遗迹';if(unit==='npc_dota_badguys_fort')return '夜魇遗迹';if(unit==='npc_dota_roshan')return '肉山';return ''}
function targetContext(t,i){const decision=D.sequence_context.source_decisions[t.match_id],status=decision?.status,missing={context_not_recorded:'本局未记录目标上下文',public_projection_clock_unverified:'公开API时钟未对齐，未关联',source_rejected:'来源核对未通过，未关联'};if(status!=='same_replay_manifest_clock_bound')return missing[status]||'来源状态未知，未关联';const row=batchContexts.get(sequencePositions.get(t)+':'+i);if(!row)return '缺少该批次关联记录，待核对';const labels={tower_destroyed:'塔摧毁',barracks_destroyed:'兵营摧毁',ancient_destroyed:'遗迹摧毁',other_building:'其他建筑',roshan_kill:'肉山击杀记录',roshan_death_record:'肉山死亡记录'},items=row.neighbor_events.map(ref=>{const e=targetEvents.get(ref.event_id),delta=ref.event_minus_placement_s;return labels[e.family]+'（放眼'+(delta===0?'同刻':delta>0?'后'+delta.toFixed(3)+'秒':'前'+(-delta).toFixed(3)+'秒')+'；事件#'+e.event_index+(targetLabel(e)?'；'+targetLabel(e):'')+'）'}),boundary=(!row.full_before_window?'；前90秒未完整':'')+(!row.full_after_window?'；后90秒未完整':'');return (items.length?items.join('； '):'本窗口无已记录目标，覆盖有限')+boundary}
function placementTimeline(){document.getElementById('timeline').replaceChildren();const s=selectedSummary;if(!s||!s.complete_match_count||!E.cell.value){tr('timeline',['没有完整比赛或候选格，不推断时序','—','—','—','—','—','—']);return}const [gx,gy]=E.cell.value.split(',').map(Number),items=D.placement_sequences.filter(r=>r.cohort_id===s.cohort_id&&r.ward_type===s.ward_type&&r.cell_width===s.cell_width&&r.grid_offset===s.grid_offset&&r.grid_x===gx&&r.grid_y===gy),byMatch=new Map(items.map(r=>[r.match_id,r])),clock=x=>{const ms=Math.round(x*1000);return Math.floor(ms/60000)+':'+((ms%60000)/1000).toFixed(3).padStart(6,'0')},relation={same_team_next_batch:'同方下一批',opposing_team_next_batch:'对方下一批',mixed_team_batch_order_ambiguous:'混合阵营批次，内部顺序未知'};for(const mid of s.complete_match_ids){const t=byMatch.get(mid),rows=t?t.batches.map((b,i)=>[b,i]).filter(([b])=>b.phase>=s.phase&&b.phase<=s.phase+1):[];if(!rows.length){tr('timeline',[mid,'本段及下一段无本格新放记录','—','—','—','—','—']);continue}for(const [b,i]of rows){const edge=i?t.adjacent_batches[i-1]:null,mixed=b.radiant_refs.length&&b.dire_refs.length,team=mixed?'同刻双方，内部无先后':b.radiant_refs.length?'天辉':'夜魇',refs=(b.radiant_refs.join(',')||'无')+' / '+(b.dire_refs.join(',')||'无'),tail=i===t.batches.length-1?'；末批到比赛结束'+t.last_batch_followup_to_match_end_s.toFixed(3)+'秒':'';tr('timeline',[mid,clock(b.placed_s),team+' / '+refs,edge?edge.delta_s.toFixed(3)+'秒；'+relation[edge.relation]:'首批，无上一批',edge?edge.minimum_position_distance.toFixed(1)+'～'+edge.maximum_position_distance.toFixed(1)+'世界单位':'—',b.phase*10+'～'+(b.phase+1)*10+'分钟；'+(b.phase_complete?'完整段':'不足尾段，仅参考')+tail,targetContext(t,i)])}}}
function detail(){for(const id of ['matrix','matches'])document.getElementById(id).replaceChildren();const cell=E.cell.value,s=selectedSummary,q=selectedPair,find=rows=>rows?.find(r=>r.grid_x+','+r.grid_y===cell),v=find(s?.cells),t=find(q?.cells);if(t){for(let i=0;i<4;i++)tr('matrix',[names[i],...t.transition_counts[i]])}else tr('matrix',[q?.common_complete_match_ids.length?'本格没有相邻段候选记录':'没有共同完整比赛，不计算零变化','—','—','—','—']);if(s){for(const mid of s.complete_match_ids){const a=v?.nonempty_match_records.find(r=>r.match_id===mid),b=t?.nonempty_match_records.find(r=>r.match_id===mid),paired=q?.common_complete_match_ids.includes(mid),refs=r=>r?(r.radiant_refs.join(',')||'无')+' / '+(r.dire_refs.join(',')||'无'):'无 / 无';tr('matches',[mid,names[a?.state||0],refs(a),paired?names[b?.before_state||0]+' → '+names[b?.after_state||0]:'未进入共同完整分母',paired?refs(b?.after_refs):'—'])}}document.getElementById('refs').textContent=JSON.stringify({summary:s,pair:q,selected_cell:cell},null,2);placementTimeline()}
function draw(){for(const id of ['summary','cells'])document.getElementById(id).replaceChildren();const offset=E.offset.value==='half'?+E.width.value/2:0,match=r=>r.cohort_id===E.cohort.value&&r.ward_type===E.kind.value&&r.cell_width===+E.width.value&&r.grid_offset===offset,s=D.summaries.find(r=>match(r)&&r.phase===+E.phase.value),q=D.paired_phase_states.find(r=>match(r)&&r.before_phase===+E.phase.value);selectedSummary=s;selectedPair=q;const pop=D.populations.find(r=>r.cohort_id===E.cohort.value&&r.phase===+E.phase.value),cohort=D.cohorts.find(r=>r.cohort_id===E.cohort.value);document.getElementById('condition').textContent=cohort?'来源'+(cohort.cohort[0]==='own'?'本人':'高分')+' / 构建'+meta(cohort.cohort[5])+' / 元数据模式'+meta(cohort.cohort[6])+' / 原地图'+meta(cohort.cohort[3]):'';document.getElementById('coverage').textContent=pop?'完整'+pop.complete_match_ids.length+'场；不足本段'+pop.partial_match_ids_reference_only.length+'场仅参考；此前已结束'+pop.ended_before_phase_ids.length+'场。':'';const aligned=mid=>D.sequence_context.source_decisions[mid]?.status==='same_replay_manifest_clock_bound',fullMids=s?.complete_match_ids||[];document.getElementById('context-coverage').textContent='同源目标事件覆盖：本群体'+(cohort?.match_ids.filter(aligned).length||0)+' / '+(cohort?.match_ids.length||0)+'场可对齐；当前完整段'+fullMids.filter(aligned).length+' / '+fullMids.length+'场可对齐。缺少上下文的比赛仍保留在四状态分母。';E.cell.replaceChildren();const candidates=new Map();for(const r of [...(s?.cells||[]),...(q?.cells||[])])candidates.set(r.grid_x+','+r.grid_y,r);for(const key of candidates.keys()){const o=document.createElement('option');o.value=key;o.textContent=key;E.cell.append(o)}E.cell.disabled=!candidates.size;
if(s?.complete_match_count){for(const r of s.cells){tr('summary',[r.grid_x+','+r.grid_y,r.state_counts[1],r.state_counts[2],r.state_counts[3],r.state_counts[0],s.complete_match_count]);const value=E.metric.value==='any'?1-r.state_proportions[0]:r.state_proportions[+E.metric.value],color=E.metric.value==='1'?'#8bcfff':E.metric.value==='2'?'#ffa5a5':'#83ddbb',[x,y]=xy(r.grid_x*s.cell_width+s.grid_offset,r.grid_y*s.cell_width+s.grid_offset+s.cell_width),size=s.cell_width/18322*8356*1600/8878,rect=node('rect',{x,y,width:size,height:size,fill:value?color:'#83909b','fill-opacity':value ? .08+.6*value : .03,stroke:value?color:'#83909b','stroke-width':2}),title=node('title',{});title.textContent='格'+r.grid_x+','+r.grid_y+' / '+(value*100).toFixed(2)+'% / '+s.complete_match_count+'场';rect.append(title);document.getElementById('cells').append(rect);if(value>0){const text=node('text',{x:x+2,y:y+18,fill:value?color:'#a8b2bb','font-size':17,stroke:'#101820','stroke-width':3,'paint-order':'stroke'});text.textContent=(value*100).toFixed(1)+'%';document.getElementById('cells').append(text)}}if(!s.cells.length)tr('summary',['本条件无候选格，完整分母仍保留','—','—','—','—',s.complete_match_count])}else tr('summary',['没有完整比赛，不计算零比例','—','—','—','—','—']);document.getElementById('paired').textContent=q?.common_complete_match_ids.length?'只比较共同完整的'+q.common_complete_match_ids.length+'场；前段'+q.before_phase*10+'～'+(q.before_phase+1)*10+'分钟，后段'+q.after_phase*10+'～'+(q.after_phase+1)*10+'分钟。':'没有下一段的共同完整比赛，不计算零变化。';detail()}
function phases(){E.phase.replaceChildren();for(const p of D.populations.filter(r=>r.cohort_id===E.cohort.value).sort((a,b)=>a.phase-b.phase)){const o=document.createElement('option');o.value=p.phase;o.textContent=p.phase*10+'～'+(p.phase+1)*10+'分钟';E.phase.append(o)}draw()}
for(const c of D.cohorts){const o=document.createElement('option');o.value=c.cohort_id;o.textContent=(c.cohort[0]==='own'?'本人':'高分')+' / '+c.match_ids.length+'场 / 构建'+meta(c.cohort[5])+' / 生产'+c.cohort[1].slice(0,8);E.cohort.append(o)}E.cohort.value=D.cohorts.slice().sort((a,b)=>b.match_ids.length-a.match_ids.length)[0]?.cohort_id||'';E.cohort.addEventListener('change',phases);E.cell.addEventListener('change',detail);for(const id of ['phase','kind','width','offset','metric'])E[id].addEventListener('change',draw);phases();</script>'''
    (output / 'report.html').write_text(page.replace('PAYLOAD', payload), encoding='utf-8', newline='\n')
