"""One offline entry for ward patterns and event windows from frozen facts."""
from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import html
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.postgame_store import bounded,sha
from dota2_insights.postgame_metadata_store import inventory as metadata_inventory,render as render_metadata
from dota2_insights.ward_patterns import describe,render
from dota2_insights.ward_event_windows import analyze,render as render_events
from dota2_insights.ward_region_stability import analyze as analyze_stability,render as render_stability
from dota2_insights.ward_objective_opportunities import analyze as analyze_opportunities,render as render_opportunities
from dota2_insights.postgame_evidence_store import inventory as evidence_inventory,render_inventory
from dota2_insights.ward_objective_regions import analyze as analyze_regions,render as render_regions
from dota2_insights.ward_time_controls import analyze as analyze_controls,render as render_controls
from dota2_insights.ward_context_balance import analyze as analyze_balance,render as render_balance
from dota2_insights.ward_control_regions import analyze as analyze_control_regions,render as render_control_regions
from dota2_insights.ward_interval_context import analyze as analyze_intervals,render as render_intervals
from dota2_insights.ward_interval_phases import analyze as analyze_interval_phases,render as render_interval_phases
from dota2_insights.ward_team_phase_states import analyze as analyze_team_phases,render as render_team_phases
from dota2_insights.ward_placement_sequences import analyze as analyze_placement_sequences
from dota2_insights.ward_sequence_context import analyze as analyze_sequence_context
from dota2_insights.ward_cohort_trajectories import analyze as analyze_trajectories, render as render_trajectories
from dota2_insights.ward_phase_context import analyze as analyze_phase_context


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database',type=Path,required=True)
    cli.add_argument('--map',type=Path,required=True)
    cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve();out=args.output.resolve();database=args.database.resolve()
    if not database.is_relative_to(root) or not out.is_relative_to(root) or out==root or out.exists():
        cli.error('Use diagnostics facts and a new diagnostics output directory.')
    digest=sha(bounded(database,128*1024**2))
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        sources=[dict(r) for r in c.execute('''SELECT s.match_id,s.source_kind,s.source_id,
            COUNT(e.event_index) raw_events,SUM(CASE WHEN e.phase='in_game' AND e.family IS NOT NULL THEN 1 ELSE 0 END) selected_events
            FROM context_sources s LEFT JOIN context_event_refs e USING(source_id)
            GROUP BY s.source_id ORDER BY s.match_id''')]
        has_evidence=dict(c.execute('SELECT key,value FROM metadata')).get('evidence_registry_version') is not None
    finally:c.close()
    names=('ward_patterns.py','ward_event_windows.py','postgame_event_types.py','postgame_store.py',
           'postgame_legacy_context.py','postgame_replay_context.py','replay_postgame_context.py','ward_region_stability.py',
           'ward_objective_opportunities.py','postgame_evidence_store.py','ward_objective_regions.py','ward_time_controls.py','postgame_context_cohort.py','ward_context_balance.py','ward_control_regions.py','ward_interval_context.py','ward_interval_phases.py','ward_team_phase_states.py','ward_placement_sequences.py','ward_sequence_context.py','postgame_capture_review.py','postgame_metadata_store.py','replay_metadata_probe.py')
    names=(*names,'ward_cohort_trajectories.py','ward_phase_context.py','postgame_target_names.py')
    code={n:(ROOT/'dota2_insights'/n).read_bytes() for n in names}
    entrypoint=Path(__file__).read_bytes()
    out.mkdir(parents=True)
    (out/'metadata').mkdir();metadata_report=metadata_inventory(database);render_metadata(metadata_report,out/'metadata')
    stability=analyze_stability(database,out/'region-stability');render_stability(stability,out/'region-stability',args.map)
    patterns=describe(database,out/'ward-patterns');render(patterns,out/'ward-patterns',args.map)
    events=analyze(database,out/'event-windows');render_events(events,out/'event-windows',args.map)
    opportunities=analyze_opportunities(database,out/'objective-opportunities');render_opportunities(opportunities,out/'objective-opportunities',args.map)
    regions=analyze_regions(database,out/'objective-opportunities/opportunities.json',out/'objective-regions');render_regions(regions,out/'objective-regions',args.map)
    controls=analyze_controls(database,out/'objective-opportunities/opportunities.json',out/'time-controls');render_controls(controls,out/'time-controls')
    balance=analyze_balance(database,out/'objective-opportunities/opportunities.json',out/'context-balance');render_balance(balance,out/'context-balance')
    control_regions=analyze_control_regions(database,out/'context-balance/context-balance.json',out/'control-regions');render_control_regions(control_regions,out/'control-regions',args.map,balance_report_href='../context-balance/report.html')
    intervals=analyze_intervals(database,out/'control-regions/control-regions.json',out/'ward-intervals');render_intervals(intervals,out/'ward-intervals',args.map)
    interval_phases=analyze_interval_phases(database,out/'ward-intervals/interval-context.json',out/'interval-phases');render_interval_phases(interval_phases,out/'interval-phases',args.map)
    team_phases=analyze_team_phases(database,out/'interval-phases/phase-intervals.json',out/'team-phases')
    placement_sequences=analyze_placement_sequences(database,out/'team-phases/team-phase-states.json',out/'team-phases')
    sequence_context=analyze_sequence_context(database,out/'team-phases/placement-sequences.json',out/'team-phases')
    render_team_phases(team_phases,out/'team-phases',args.map,placement_sequences,sequence_context)
    phase_context=analyze_phase_context(database,out/'team-phases/team-phase-states.json',out/'team-phases/sequence-target-context.json',out/'phase-context')
    trajectories=analyze_trajectories(database,out/'team-phases/team-phase-states.json',out/'cohort-trajectories')
    render_trajectories(trajectories,out/'cohort-trajectories',args.map,phase_context)
    if has_evidence:
        (out/'evidence').mkdir();evidence=evidence_inventory(database);render_inventory(evidence,out/'evidence')
    if sha(bounded(database,128*1024**2))!=digest or Path(__file__).read_bytes()!=entrypoint or any((ROOT/'dota2_insights'/n).read_bytes()!=b for n,b in code.items()):
        raise ValueError('postgame_review_source_or_code_changed')
    (out/'source-code').mkdir()
    for n,b in code.items():(out/'source-code'/n).write_bytes(b)
    (out/'source-code'/'review_postgame_context_store.py').write_bytes(entrypoint)
    receipt={'status':'complete','database_sha256':digest,'matches':patterns['matches'],'wards':patterns['ward_records'],
        'context_sources_by_kind':dict(Counter(s['source_kind'] for s in sources)),
        'event_window_matches':events['distinct_matches_with_selected_events'],'selected_events':events['selected_event_count'],
        'time_only_target_pairs':patterns['time_only_context_pair_count'],
        'pattern_schema_version':patterns['schema_version'],
        'retrospective_training_matches':sum(x['role']=='training' for x in stability['split'].values()),
        'retrospective_heldout_matches':sum(x['role']=='heldout' for x in stability['split'].values()),
        'retrospective_frozen_models_sha256':stability['frozen_models_sha256'],
        'objective_opportunity_groups':opportunities['timestamp_groups'],
        'objective_opportunity_eligible_groups':opportunities['summary_eligible_groups'],
        'objective_region_states_primary_grid':regions['record_states_primary_grid'],
        'time_control_matched_by_buffer':{str(r['buffer_s']):r['statuses'].get('matched',0) for r in controls['coverage']},
        'time_distance_and_economic_coverage':[{k:v for k,v in r.items() if k!='matched_target_group_refs'} for r in balance['coverage']],
        'four_window_region_parameter_pairs':control_regions['matched_parameter_pairs'],
        'four_window_region_slices':len(control_regions['records']),
        'four_window_region_strata':len(control_regions['summaries']),
        'ward_interval_source_categories':intervals['source_ward_counts'],
        'ward_interval_pair_slices':len(intervals['records']),
        'whole_match_interval_phase_observations':len(interval_phases['observations']),
        'whole_match_interval_phase_strata':len(interval_phases['summaries']),
        'whole_match_paired_phase_strata':len(interval_phases['paired_phase_changes']),
        'two_team_phase_strata':len(team_phases['summaries']),
        'two_team_phase_cells':sum(len(r['cells']) for r in team_phases['summaries']),
        'two_team_paired_phase_strata':len(team_phases['paired_phase_states']),
        'two_team_paired_phase_cells':sum(len(r['cells']) for r in team_phases['paired_phase_states']),
        'placement_sequence_counts':placement_sequences['counts'],
        'sequence_target_context_counts':sequence_context['counts'],
        'fixed_population_trajectory_counts':trajectories['counts'],
        'phase_context_ledger_counts':phase_context['counts'],
        'evidence_source_versions':len(evidence['sources']) if has_evidence else None,
        'selected_metadata_probes':metadata_report['selected_probes'],'matches_with_known_build':metadata_report['matches_with_build'],
        'source_code_sha256':{n:hashlib.sha256(b).hexdigest() for n,b in code.items()},
        'entrypoint_sha256':hashlib.sha256(entrypoint).hexdigest()}
    (out/'run-summary.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    labels={'api_projection':'API公开投影（摘要与解析覆盖不同）','legacy_archive':'旧DEM导出（英雄参与过滤）','replay_capture':'同遍上下文采集（指标语义待继续核对）'}
    rows=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
        (s['match_id'],labels.get(s['source_kind'],s['source_kind']),s['raw_events'],s['selected_events']))+'</tr>' for s in sources)
    evidence_link='<li><a href="evidence/report.html">来源版本、当前选用与精确记录差异</a>：同场资料分别留存，截断资料不混入主分析。</li>' if has_evidence else ''
    page=f'''<!doctype html><meta charset="utf-8"><title>赛后资料与眼位关联</title>
<style>body{{font:17px system-ui;background:#17232f;color:#e3ebf3;margin:28px;max-width:1300px}}a{{color:#8cd8c3}}li{{margin:15px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border-bottom:1px solid #52677b}}.warn{{color:#f1cc87}}code{{overflow-wrap:anywhere}}</style>
<h1>赛后资料：眼位与时间关联</h1><p>{patterns['matches']}场事实库，{patterns['ward_records']}条眼位；{len(sources)}份上下文来源，{events['distinct_matches_with_selected_events']}场有已选事件。本页只组织数据与候选关联，不给战术评价。</p>
<ul><li><a href="metadata/report.html">录像构建号、模式与额外读取证据</a>：明确选择、冲突/拒收及逐字段来源；原事实不改写。</li>{evidence_link}<li><a href="ward-patterns/report.html">不同阶段、阵营的放眼区域</a>：按不同比赛计数、完整时段分母、同批时段变化。</li>
<li><a href="event-windows/report.html">目标、死亡候选与买活前后对照</a>：{events['selected_event_count']}条已选事件，地图与原始指标。</li>
<li><a href="region-stability/report.html">常见区域的回顾性复现</a>：固定拆分、先冻结名单，再数留出记录；整批曾参与探索，不能当新比赛验证。</li>
<li><a href="objective-opportunities/report.html">目标窗口重叠与放眼机会</a>：前后等长、互不重叠，零放眼窗口保留；{opportunities['summary_eligible_groups']}个足长组，逐局计数与地图。</li>
<li><a href="objective-regions/report.html">目标前后放眼区域对照</a>：同一窗口格子配对、零记录分母、逐局等权与粗细网格，区分放置记录和实际视野。</li>
<li><a href="time-controls/report.html">同场、同阶段、等时长的时间对照</a>：避开已记录目标、对照不重用，未匹配原因与配对原计数，不作因果结论。</li>
<li><a href="context-balance/report.html">更近的时间对照与窗口前经济记录</a>：60/120/300秒距离限制重新配对，缺失与原始团队计数分别保留，尚未做经济平衡。</li>
<li><a href="control-regions/report.html">目标与对照的四窗口放眼地图</a>：同网格原索引、全空配对分母与逐局等权；计数变化差仅是描述，不作因果或眼位评价。</li>
<li><a href="ward-intervals/report.html">窗前已有的眼与结束未知记录</a>：放置至记录结束区间、局后结束与未知结束上界分层，包含开局前放置，不冒充实际视野。</li>
<li><a href="interval-phases/report.html">完整比赛每10分钟的区间与新放眼变化</a>：完整时段分母、尾段仅参考，相邻段只用共同完整比赛，未知结束上界单列。</li>
<li><a href="team-phases/report.html">双阵营同格放眼与相邻时段状态</a>：四种同场状态与16种配对变化，零记录比赛留在分母；不是同时视野或区域控制。</li>
<li><a href="cohort-trajectories/report.html">固定同一批比赛的连续时段眼区记录</a>：三个及以上完整时段使用同一分母，保留全零比赛与逐场路径；分开显示因时长不足排除的比赛。</li>
<li><a href="run-summary.json">构建回执</a> · <a href="ward-patterns/time-only-context-pairs.jsonl">目标时间近邻明细</a>：{patterns['time_only_context_pair_count']}条候选，不代表空间接近或因果。</li></ul>
<p class="warn">完整EOF不保证事件齐全；旧导出有覆盖过滤，缺失不补零。地图/补丁未知仍是未知。不同来源和窗口可能重叠，不能将事件条数当独立比赛。</p>
<h2>来源覆盖</h2><table><tr><th>比赛</th><th>来源</th><th>当前留存原始事件</th><th>游戏内已选事件</th></tr>{rows}</table>
<p>数据库SHA：<code>{digest}</code>。API玩家槽位与DEM逻辑玩家ID分开；结束后经济样本单列，不混入游戏内分析。</p>'''
    (out/'index.html').write_text(page,encoding='utf-8',newline='\n')
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('source_code_sha256','time_distance_and_economic_coverage')},ensure_ascii=False))


if __name__=='__main__':main()
