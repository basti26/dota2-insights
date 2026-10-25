"""Same-DEM prefix building anchors and ward distances, with explicit ambiguity."""
from __future__ import annotations

from collections import Counter, defaultdict
import html
import json
import math
from pathlib import Path
import sqlite3

from .postgame_store import bounded, sha, number
from .replay_building_landmarks import SCHEMA as CAPTURE_SCHEMA, POSITION_FIELDS, entity_name_attributes
from .ward_phase_context import SCHEMA as PHASE_SCHEMA
from .ward_sequence_context import _gate

SCHEMA = 'same_replay_prefix_building_anchor_geometry_v1'
WINDOW_S = 90
CLASS_KIND = {'CDOTA_BaseNPC_Tower': 'tower', 'CDOTA_BaseNPC_Barracks': 'barracks',
              'CDOTA_BaseNPC_Fort': 'ancient'}


def analyze(database, phase_context, captures, output):
    database, phase_context, output = (Path(x).resolve() for x in (database, phase_context, output))
    if output.exists() or not 1 <= len(captures) <= 8:
        raise ValueError('new_geometry_output_and_bounded_captures_required')
    digest = sha(bounded(database, 128 * 1024**2))
    phase_raw = bounded(phase_context, 32 * 1024**2)
    phase = json.loads(phase_raw)
    if phase['schema_version'] != PHASE_SCHEMA or phase['database_sha256'] != digest:
        raise ValueError('geometry_phase_database_binding')
    c = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    receipts, anchors, ward_rows, event_rows, pairs = [], [], [], [], []
    raw_location_counts = Counter()
    try:
        for e in phase['events']:
            raw = c.execute('SELECT payload FROM events WHERE match_id=? AND event_kind=? AND event_index=?',
                            (e['match_id'], e['event_kind'], e['event_index'])).fetchone()
            if raw is None:
                raise ValueError('geometry_target_reference_missing')
            p = json.loads(raw['payload'])
            xy = (p.get('location_x_raw'), p.get('location_y_raw'))
            raw_location_counts['both_present' if all(number(v) for v in xy) else
                                'both_missing' if xy == (None, None) else 'partial_missing'] += 1
        mids = set()
        for path in captures:
            capture_raw = bounded(Path(path), 1024**2)
            d = json.loads(capture_raw)
            mid = d['match_id']
            if mid in mids:
                raise ValueError('geometry_duplicate_match_capture')
            mids.add(mid)
            m = c.execute('SELECT * FROM matches WHERE match_id=?', (mid,)).fetchone()
            wm = c.execute("SELECT content,sha256 FROM sources WHERE match_id=? AND kind='manifest.json'", (mid,)).fetchone()
            if m is None or wm is None or sha(wm['content']) != wm['sha256']:
                raise ValueError('geometry_original_source_missing')
            manifest = json.loads(wm['content'])
            if (d['schema_version'] != CAPTURE_SCHEMA or d['source_sha256'] != m['replay_sha256']
                    or d['source_sha256_after'] != d['source_sha256'] or d['source_unchanged'] is not True
                    or d['parser_version'] != manifest['parser_version']
                    or d['parser_commit'] != manifest['parser_commit']
                    or d['parse_error_type'] is not None or d['truncated_at_tick'] is not None
                    or d['stop_reason'] != 'prefix_tick_limit_or_eof'
                    or not 1 <= d['max_tick'] <= 12000 or not 0 < d['last_tick'] <= d['max_tick'] + 64
                    or len(d['records']) > 512):
                raise ValueError('geometry_capture_binding_or_incomplete_prefix')
            receipts.append({'match_id': mid, 'capture_sha256': sha(capture_raw),
                             'replay_sha256': d['source_sha256'], 'last_tick': d['last_tick'],
                             'snapshot_helper_sha256': d['snapshot_helper_sha256'],
                             'collector_sha256': d['collector_sha256'], 'adapter_sha256': d['adapter_sha256'],
                             'observations': len(d['records'])})
            entities = defaultdict(list)
            for i, r in enumerate(d['records']):
                if (type(r['entity_index']) is not int or type(r['entity_serial']) is not int
                        or type(r['packet_tick']) is not int or not 0 <= r['packet_tick'] <= d['last_tick']
                        or r['coordinate_basis'] != 'gem_cell128_plus_vec_raw_coordinates'
                        or r['name_attributes'] != entity_name_attributes(r['unit_name'])):
                    raise ValueError('geometry_record_identity_or_names')
                if r['class_name'] not in CLASS_KIND:
                    continue  # Watch towers remain in the raw capture; never infer static ownership.
                a = r['name_attributes']
                if (a['status'] != 'parsed_name_family_consistent' or a['kind'] != CLASS_KIND[r['class_name']]
                        or a['defending_team'] != r['team_raw'] or r['position_status'] != 'recorded'):
                    raise ValueError('geometry_building_class_name_team_or_position')
                v = r['position_components']
                if (set(v) != set(POSITION_FIELDS) or not all(number(x) for x in v.values())
                        or v[POSITION_FIELDS[0]] * 128 + v[POSITION_FIELDS[2]] != r['world_x']
                        or v[POSITION_FIELDS[1]] * 128 + v[POSITION_FIELDS[3]] != r['world_y']):
                    raise ValueError('geometry_original_component_reconstruction')
                entities[r['entity_index'], r['entity_serial']].append((i, r))
            local = []
            for (index, serial), records in sorted(entities.items()):
                names = {r['unit_name'] for _, r in records}
                positions = {(r['world_x'], r['world_y']) for _, r in records}
                if len(names) != 1 or len(positions) != 1:
                    raise ValueError('geometry_building_prefix_state_changed')
                i, r = records[0]
                b = {'match_id': mid, 'entity_index': index, 'entity_serial': serial,
                     'unit_name': r['unit_name'], 'class_name': r['class_name'],
                     'world_x': r['world_x'], 'world_y': r['world_y'],
                     'observation_indices': [n for n, _ in records],
                     'first_packet_tick': r['packet_tick'], 'name_attributes': r['name_attributes'],
                     'position_basis': 'observed_prefix_anchor_not_death_event_position'}
                local.append(b)
            anchors.extend(local)
            wards = [dict(w) for w in c.execute('SELECT * FROM wards WHERE match_id=? ORDER BY ward_index', (mid,))]
            for w in wards:
                if w['coordinate_basis'] != 'gem_cell128_plus_vec_raw_coordinates':
                    raise ValueError('geometry_ward_coordinate_basis_unverified')
                if not number(w['x']) or not number(w['y']):
                    continue
                distances = [(math.hypot(w['x']-b['world_x'], w['y']-b['world_y']), b) for b in local]
                best = min((v for v, _ in distances), default=None)
                ward_rows.append({'match_id': mid, 'ward_index': w['ward_index'], 'ward_type': w['ward_type'],
                                  'team': w['team'], 'placed_s': w['placed_s'], 'world_x': w['x'], 'world_y': w['y'],
                                  'phase': int(w['placed_s']//600) if number(w['placed_s']) and w['placed_s'] >= 0 else None,
                                  'nearest_distance_to_prefix_anchor': best,
                                  'nearest_anchor_entities': [[b['entity_index'], b['entity_serial']]
                                      for v, b in distances if v == best]})
            source = c.execute('SELECT * FROM context_sources WHERE match_id=?', (mid,)).fetchone()
            if source is None:
                continue
            _gate(c, dict(m), dict(source))
            for e in phase['events']:
                if e['match_id'] != mid:
                    continue
                raw = c.execute('SELECT * FROM events WHERE match_id=? AND event_kind=? AND event_index=?',
                                (mid, e['event_kind'], e['event_index'])).fetchone()
                p = json.loads(raw['payload'])
                if (e['source_id'] != source['source_id'] or e['target_unit'] != p['target']
                        or e['game_time_s'] != raw['time_s'] or e['packet_tick'] != p['packet_tick']):
                    raise ValueError('geometry_event_original_reference_changed')
                candidates = [b for b in local if b['name_attributes']['candidate_combat_target_name'] == e['target_unit']]
                status = 'one_name_candidate_not_event_entity_identity' if len(candidates) == 1 else \
                         'multiple_name_candidates_no_entity_assignment' if candidates else 'no_prefix_name_candidate'
                event_rows.append({**e, 'anchor_status': status,
                                   'anchor_candidates': [[b['entity_index'], b['entity_serial']] for b in candidates]})
                for w in wards:
                    if (not number(w['placed_s']) or not number(w['x']) or not number(w['y'])
                            or not 0 <= w['placed_s'] < m['duration_s']
                            or abs(w['placed_s'] - e['game_time_s']) > WINDOW_S):
                        continue
                    pairs.append({'event_id': e['event_id'], 'match_id': mid, 'ward_index': w['ward_index'],
                                  'ward_placed_minus_target_s': w['placed_s'] - e['game_time_s'],
                                  'anchor_status': status,
                                  'distance_to_each_prefix_anchor': [
                                      {'entity_index': b['entity_index'], 'entity_serial': b['entity_serial'],
                                       'distance': math.hypot(w['x']-b['world_x'], w['y']-b['world_y'])}
                                      for b in candidates]})
    finally:
        c.close()
    if sha(bounded(database, 128 * 1024**2)) != digest:
        raise ValueError('geometry_database_changed')
    result = {'schema_version': SCHEMA, 'database_sha256': digest, 'phase_context_sha256': sha(phase_raw),
              'time_window_s': WINDOW_S, 'captures': receipts, 'anchors': anchors,
              'ward_records': ward_rows, 'target_events': event_rows, 'ward_target_pairs': pairs,
              'all_bound_targets_raw_location_coverage': dict(raw_location_counts),
              'event_candidate_statuses': dict(Counter(e['anchor_status'] for e in event_rows)),
              'analysis_source_sha256': sha(Path(__file__).read_bytes()),
              'entity_alias_source_sha256': sha(Path(__file__).with_name('replay_building_landmarks.py').read_bytes()),
              'limits': ['Same replay only; no coordinates copied across matches or builds.',
                         'Distances are to observed prefix anchors, not measured combat-event locations.',
                         'Single name candidate is not event entity identity; four towers may be ambiguous.',
                         'All in-window wards retained without distance threshold, no effectiveness score.',
                         'Prefix completeness does not prove all structures recorded or future stability.',
                         'Offline omniscient data, never live assistant input.']}
    output.mkdir(parents=True)
    (output/'geometry.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    refs = {(b['match_id'], b['entity_index'], b['entity_serial']): b for b in anchors}
    def stamp(value):
        if not number(value):
            return '未知'
        sign = '-' if value < 0 else ''
        value = abs(value)
        return f'{sign}{int(value//60)}:{value%60:06.3f}'

    def building_label(b):
        a = b['name_attributes']
        side = {2: '天辉', 3: '夜魇'}.get(a['defending_team'], '阵营未知')
        lane = {'top': '上路', 'mid': '中路', 'bot': '下路', None: ''}[a['lane']]
        if a['kind'] == 'tower':
            return side + lane + {1: '一塔', 2: '二塔', 3: '三塔', 4: '四塔'}[a['tower_tier']]
        if a['kind'] == 'barracks':
            return side + lane + {'melee': '近战兵营', 'range': '远程兵营'}[a['barracks_type']]
        return side + '远古遗迹'

    rows = []
    for w in ward_rows:
        names = ' / '.join(building_label(refs[w['match_id'], *key]) for key in w['nearest_anchor_entities'])
        rows.append('<tr>' + ''.join('<td>'+html.escape(str(v))+'</td>' for v in
                    (w['match_id'], w['ward_index'], {2: '天辉', 3: '夜魇'}.get(w['team'], '未知'),
                     {'observer': '侦查守卫', 'sentry': '岗哨守卫'}.get(w['ward_type'], '未知'), stamp(w['placed_s']), names,
                     round(w['nearest_distance_to_prefix_anchor'], 2) if w['nearest_distance_to_prefix_anchor'] is not None else '未知')) + '</tr>')
    page = '<!doctype html><meta charset="utf-8"><title>同录像建筑锚点取样</title><style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}td,th{padding:8px;border-bottom:1px solid #52677b;text-align:left}a{color:#8cd8c3}</style>'
    page += '<h1>建筑锚点与原眼位：同录像离线取样</h1><p>只读录像前缀的实体坐标；不是事件发生位置、完整建筑清单、有效视野或眼位评分。两场样例不能代表通用偏好，坐标不迁移到其他版本或比赛。</p>'
    page += '<p>建筑实体 '+str(len(anchors))+'；原眼位 '+str(len(ward_rows))+'；目标记录 '+str(len(event_rows))+'；时间窗口配对 '+str(len(pairs))+'。四塔保留多个位置候选。</p><p><a href="geometry.json">原坐标、实体索引、名称候选与全部配对数据</a></p>'
    page += '<table><tr><th>比赛</th><th>原眼位索引</th><th>阵营</th><th>眼型</th><th>放置时间</th><th>最近已记录建筑</th><th>至取样锚点距离（世界单位）</th></tr>'+''.join(rows)+'</table>'
    page += '<h2>目标事件的位置候选</h2><p>按名称列出同录像取样实体。单一候选也未证明事件实体身份；四塔不任选一座，肉山没有本次建筑取样位置。时间窗口内全部眼位配对留在数据文件中，未按距离筛选。</p><table><tr><th>比赛</th><th>原事件索引</th><th>时间</th><th>目标</th><th>位置候选数</th><th>实体与取样坐标</th></tr>'
    for e in event_rows:
        candidates = [refs[e['match_id'], *key] for key in e['anchor_candidates']]
        label = building_label(candidates[0]) if candidates else '未取得建筑位置'
        positions = '；'.join(f"#{b['entity_index']}:{b['entity_serial']} ({b['world_x']}, {b['world_y']})" for b in candidates)
        page += '<tr>' + ''.join('<td>'+html.escape(str(v))+'</td>' for v in
                                (e['match_id'], e['event_index'], stamp(e['game_time_s']), label,
                                 len(candidates), positions or '未知')) + '</tr>'
    page += '</table>'
    (output/'report.html').write_text(page, encoding='utf-8', newline='\n')
    return result
