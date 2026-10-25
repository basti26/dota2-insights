"""Descriptive building-anchor neighborhoods with explicit geometry coverage denominators."""
from __future__ import annotations

from collections import defaultdict
import html
import json
import math
from pathlib import Path
import sqlite3

from .postgame_store import bounded, sha
from .postgame_building_geometry import SCHEMA as GEOMETRY_SCHEMA
from .ward_team_phase_states import SCHEMA as TEAM_SCHEMA
from .replay_building_landmarks import entity_name_attributes

SCHEMA = 'ward_same_source_building_neighborhoods_v2'
RADII = (1024, 2048)


def analyze(database, geometry_path, team_path, output):
    database, geometry_path, team_path, output = map(lambda x: Path(x).resolve(),
                                                    (database, geometry_path, team_path, output))
    if output.exists():
        raise ValueError('new_building_region_output_required')
    digest = sha(bounded(database, 128 * 1024**2))
    geometry_raw, team_raw = bounded(geometry_path, 32 * 1024**2), bounded(team_path, 64 * 1024**2)
    g, t = json.loads(geometry_raw), json.loads(team_raw)
    if (g['schema_version'] != GEOMETRY_SCHEMA or t['schema_version'] != TEAM_SCHEMA
            or g['database_sha256'] != digest or t['database_sha256'] != digest):
        raise ValueError('building_region_source_binding')
    c = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        originals = {(r['match_id'], r['ward_index']): dict(r) for r in c.execute('SELECT * FROM wards')}
    finally:
        c.close()
    by_anchor = defaultdict(dict)
    for b in g['anchors']:
        mid, name = b['match_id'], b['unit_name']
        if mid in by_anchor[name]:
            raise ValueError('building_name_not_unique_within_match_no_merge')
        by_anchor[name][mid] = b
    wards = defaultdict(list)
    for w in g['ward_records']:
        old = originals[w['match_id'], w['ward_index']]
        if (w['world_x'], w['world_y'], w['placed_s'], w['team'], w['ward_type']) != \
                (old['x'], old['y'], old['placed_s'], old['team'], old['ward_type']):
            raise ValueError('building_region_original_ward_changed')
        wards[w['match_id']].append(w)
    captured = {r['match_id'] for r in g['captures']}
    if len(captured) != len(g['captures']) or set(wards) - captured:
        raise ValueError('building_region_capture_membership')
    expected = {key for key in originals if key[0] in captured}
    received = [(w['match_id'], w['ward_index']) for w in g['ward_records']]
    if len(received) != len(set(received)) or set(received) != expected:
        raise ValueError('building_region_all_original_wards_required_no_missing_as_zero')
    records, landmarks, coverage = [], [], []
    for cohort in t['cohorts']:
        group = set(cohort['match_ids'])
        available = captured & group
        if not available:
            continue
        names = sorted(name for name, cases in by_anchor.items() if set(cases) & available)
        for name in names:
            cases = by_anchor[name]
            mids = sorted(available & set(cases))
            positions = [[mid, cases[mid]['world_x'], cases[mid]['world_y']] for mid in mids]
            span = max((math.hypot(a[1]-b[1], a[2]-b[2]) for a in positions for b in positions), default=0)
            landmarks.append({'cohort_id': cohort['cohort_id'], 'unit_name': name,
                              'positions_by_match': positions,
                              'missing_captured_match_ids': sorted(available-set(cases)),
                              'max_pairwise_position_distance': span,
                              'meaning': 'observed_same_name_anchor_dispersion_not_map_certification'})
        for population in t['populations']:
            if population['cohort_id'] != cohort['cohort_id']:
                continue
            full = set(population['complete_match_ids'])
            eligible = full & available
            phase = population['phase']
            coverage.append({'cohort_id': cohort['cohort_id'], 'phase': phase,
                             'original_complete_match_ids': sorted(full),
                             'geometry_complete_match_ids': sorted(eligible),
                             'missing_geometry_complete_match_ids': sorted(full-available),
                             'geometry_partial_match_ids_reference_only': sorted(available & set(population['partial_match_ids_reference_only']))})
            for name in names:
                cases = by_anchor[name]
                participants = sorted(eligible & set(cases))
                for radius in RADII:
                    for side in (2, 3):
                        for kind in ('observer', 'sentry'):
                            refs = []
                            for mid in participants:
                                b = cases[mid]
                                for w in wards[mid]:
                                    if (w['team'] != side or w['ward_type'] != kind
                                            or not phase*600 <= w['placed_s'] < (phase+1)*600):
                                        continue
                                    distance = math.hypot(w['world_x']-b['world_x'], w['world_y']-b['world_y'])
                                    if distance <= radius:
                                        refs.append({'match_id': mid, 'ward_index': w['ward_index'],
                                                     'placed_s': w['placed_s'], 'distance': distance,
                                                     'anchor_entity_index': b['entity_index'],
                                                     'anchor_entity_serial': b['entity_serial']})
                            supported = sorted({r['match_id'] for r in refs})
                            records.append({'cohort_id': cohort['cohort_id'], 'phase': phase, 'unit_name': name,
                                            'radius': radius, 'ward_team': side, 'ward_type': kind,
                                            'complete_geometry_anchor_match_ids': participants,
                                            'missing_anchor_with_geometry_match_ids': sorted(eligible-set(cases)),
                                            'support_match_ids': supported, 'record_refs': refs,
                                            'support_fraction': len(supported)/len(participants) if participants else None,
                                            'status': 'recorded_subset' if participants else 'no_complete_geometry_matches'})
    paired = []
    lookup = {(r['cohort_id'], r['phase'], r['unit_name'], r['radius'], r['ward_team'], r['ward_type']): r
              for r in records}
    coverage_lookup = {(r['cohort_id'], r['phase']): r for r in coverage}
    for key, before in sorted(lookup.items()):
        cohort, phase, name, radius, side, kind = key
        after = lookup.get((cohort, phase+1, name, radius, side, kind))
        if after is None:
            continue
        original = sorted(set(coverage_lookup[cohort, phase]['original_complete_match_ids'])
                          & set(coverage_lookup[cohort, phase+1]['original_complete_match_ids']))
        common = sorted(set(before['complete_geometry_anchor_match_ids'])
                        & set(after['complete_geometry_anchor_match_ids']))
        b, a = set(before['support_match_ids']), set(after['support_match_ids'])
        states = [[] for _ in range(4)]
        for mid in common:
            states[int(mid in b) + 2*int(mid in a)].append(mid)
        paired.append({'cohort_id': cohort, 'before_phase': phase, 'after_phase': phase+1,
                       'unit_name': name, 'radius': radius, 'ward_team': side, 'ward_type': kind,
                       'original_common_complete_match_ids': original,
                       'common_complete_geometry_anchor_match_ids': common,
                       'missing_geometry_or_anchor_common_match_ids': sorted(set(original)-set(common)),
                       'state_match_ids': states, 'state_counts': list(map(len, states)) if common else None,
                       'before_record_refs': [r for r in before['record_refs'] if r['match_id'] in common],
                       'after_record_refs': [r for r in after['record_refs'] if r['match_id'] in common],
                       'status': 'paired_common_population' if common else 'no_common_complete_geometry_matches'})
    result = {'schema_version': SCHEMA, 'database_sha256': digest,
              'geometry_sha256': sha(geometry_raw), 'team_phase_sha256': sha(team_raw),
              'radii': list(RADII), 'radius_basis': 'fixed_before_same_build_results_not_optimized',
              'phase_seconds': 600, 'cohorts': t['cohorts'], 'coverage': coverage,
              'landmarks': landmarks, 'records': records,
              'adjacent_phase_pairs': paired,
              'paired_state_order': ['neither_recorded', 'before_only', 'after_only', 'both_recorded'],
              'limits': ['Geometry subset is not the original full cohort; missing geometry is not zero support.',
                         'Only complete 10-minute phases; pregame and partial phases excluded from support.',
                         'All observed anchors retained including zero support; one match counts once per condition.',
                         'Radii and nearby anchors overlap; rows are not independent samples.',
                         'Distance is to prefix anchor, not current standing building, event location or actual vision.',
                         'Different phases may contain different matches; no direct behavioral trend inferred.',
                         'Paired comparisons restrict both phases to the same complete matches; not ward survival or control.',
                         'Identical positions in this subset do not certify a whole map/build or unseen matches.'],
              'analysis_source_sha256': sha(Path(__file__).read_bytes())}
    if sha(bounded(database, 128 * 1024**2)) != digest:
        raise ValueError('building_region_database_changed')
    output.mkdir(parents=True)
    (output/'regions.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    page = '<!doctype html><meta charset="utf-8"><title>建筑周边眼位记录</title><style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}td,th{padding:7px;border-bottom:1px solid #52677b;text-align:left}a{color:#8cd8c3}</style><h1>同录像建筑周边：分时段放眼记录</h1><p>只计完整10分钟段的新放眼。缺建筑取样的比赛保持缺失，不当作没有放眼；零支持地标仍保留。不同段人群可不同，不能直接解释成行为趋势。距离到前缀取样建筑，不代表该塔当时仍在或有有效视野。</p><p><a href="regions.json">全部原索引、位置差异、分母和条件</a></p><h2>每段资料覆盖</h2><table><tr><th>资料组</th><th>时段</th><th>原完整比赛</th><th>有建筑取样</th><th>缺建筑取样</th></tr>'
    cohort_labels = {g['cohort_id']: ('本人' if g['cohort'][0] == 'own' else '高分')
                     + ' · 构建' + str(g['cohort'][5]) for g in t['cohorts']}
    def label(name):
        a = entity_name_attributes(name)
        side = {2: '天辉', 3: '夜魇'}.get(a['defending_team'], '未知阵营')
        lane = {'top': '上路', 'mid': '中路', 'bot': '下路', None: ''}.get(a['lane'], '')
        if a['kind'] == 'tower':
            return side + lane + str(a['tower_tier']) + '塔' + ('（上侧）' if a['entity_locator_suffix'] == 'top'
                         else '（下侧）' if a['entity_locator_suffix'] == 'bot' else '')
        if a['kind'] == 'barracks':
            return side + lane + {'melee': '近战兵营', 'range': '远程兵营'}[a['barracks_type']]
        return side + '远古遗迹'
    for r in coverage:
        page += '<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
                               (cohort_labels[r['cohort_id']], f"{r['phase']*10}～{(r['phase']+1)*10}分钟",
                                len(r['original_complete_match_ids']), len(r['geometry_complete_match_ids']),
                                len(r['missing_geometry_complete_match_ids'])))+'</tr>'
    page += '</table><h2>各地标的逐段记录</h2><p>下列每项支持=有过至少一次该条件放眼的不同比赛数。1024/2048世界单位为预设观察半径，并非有效视野或最优范围。同一眼可能落在多个地标范围内；逐场原索引见数据文件。</p><table><tr><th>资料组</th><th>时段</th><th>原实体地标名</th><th>半径</th><th>阵营</th><th>眼型</th><th>支持 / 有同源地标的完整比赛</th></tr>'
    for r in records:
        page += '<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
            (cohort_labels[r['cohort_id']], f"{r['phase']*10}～{(r['phase']+1)*10}分钟", label(r['unit_name']), r['radius'],
             {2: '天辉', 3: '夜魇'}[r['ward_team']], {'observer': '侦查守卫', 'sentry': '岗哨守卫'}[r['ward_type']],
             f"{len(r['support_match_ids'])} / {len(r['complete_geometry_anchor_match_ids'])}" if r['support_fraction'] is not None else '无完整取样比赛，不计算'))+'</tr>'
    page += '</table><h2>同一批比赛的相邻时段配对</h2><p>两段都完整且有同源地标的比赛才进入配对。四种状态只表示各段新放眼记录，不是眼的存续、视野或控制权变化。缺共同样本不补零。</p><table><tr><th>资料组</th><th>前段 → 后段</th><th>地标</th><th>半径</th><th>阵营 / 眼型</th><th>原共同完整 / 可用共同完整</th><th>两段均无 / 仅前段 / 仅后段 / 两段均有</th></tr>'
    for r in paired:
        page += '<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
            (cohort_labels[r['cohort_id']], f"{r['before_phase']*10}～{(r['before_phase']+1)*10} → {r['after_phase']*10}～{(r['after_phase']+1)*10}分钟",
             label(r['unit_name']), r['radius'], {2: '天辉', 3: '夜魇'}[r['ward_team']]+' / '+{'observer': '侦查守卫', 'sentry': '岗哨守卫'}[r['ward_type']],
             f"{len(r['original_common_complete_match_ids'])} / {len(r['common_complete_geometry_anchor_match_ids'])}",
             ' / '.join(map(str, r['state_counts'])) if r['state_counts'] is not None else '无共同完整取样比赛，不计算'))+'</tr>'
    page += '</table>'
    (output/'report.html').write_text(page, encoding='utf-8', newline='\n')
    return result
