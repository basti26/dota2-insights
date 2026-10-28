"""Named recorded GSI evidence, not support inference or a hidden-world model."""
from collections import Counter
from datetime import datetime
import math
import re

FLAGS = ('alive', 'silenced', 'stunned', 'disarmed', 'hexed', 'muted', 'break', 'smoked',
         'has_debuff', 'magicimmune', 'debuffimmune', 'rooted', 'invulnerable', 'channeling', 'teleporting')
POSITION_COLUMNS = ('line', 'sequence', 'match_id', 'game_session_id', 'record_session_id', 'hero_id',
                    'gsi_source_id',
                    'provider_timestamp', 'provider_at_utc', 'received_at_utc', 'clock_time_s', 'xpos', 'ypos',
                    'alive', 'source_age_s', 'game_state', 'paused', 'daytime', 'nightstalker_night')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def stamp(value):
    try:
        value = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None
        return value.timestamp() if value is not None and value.utcoffset() is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def boolean(value):
    return 'true' if value is True else 'false' if value is False else 'not_reported' if value is None else 'invalid'


class GsiEvidenceCensus:
    def __init__(self, *, collect_positions=False, position_limit=20000):
        if type(position_limit) is not int or not 1 <= position_limit <= 100000:
            raise ValueError('gsi_position_export_limit')
        self.collect_positions, self.position_limit = collect_positions, position_limit
        self.rows = self.probe_rows = self.position_rows = 0
        self.living_positions = self.fresh_living_positions = 0
        self.position_status, self.position_rejected = Counter(), Counter()
        self.flags = {flag: Counter() for flag in FLAGS}
        self.daylight = {key: Counter() for key in ('daytime', 'nightstalker_night', 'paused')}
        self.objectives = {key: Counter() for key in ('roshan', 'tormentor', 'miniboss')}
        self.roshan_types, self.event_types = Counter(), Counter()
        self.objective_keys, self.untimed_objectives = {}, Counter()
        self.objective_key_limit_reached = False
        self.positions, self.previous, self.transitions = [], None, []
        self.transition_counts, self.transition_gaps = Counter(), Counter()
        self.bounds = None
        self.missing_probe = self.unsupported_probe = 0
        self.provider_times, self.provider_signatures, self.source_ids = Counter(), {}, Counter()
        self.missing_provider_times = self.missing_source_ids = self.invalid_source_ids = 0
        self.source_key_limit_reached = False

    def add(self, row, *, line=None):
        self.rows += 1
        provider = stamp(row.get('provider_at_utc'))
        scope = tuple(row.get(k) for k in ('match_id', 'game_session_id', 'record_session_id'))
        token = row.get('gsi_source_id')
        if token is None:
            self.missing_source_ids += 1
        elif not isinstance(token, str) or not re.fullmatch(r'gsi_[a-f0-9]{32}', token):
            self.invalid_source_ids += 1
        elif len(self.source_ids) < 100000 or (*scope, token) in self.source_ids:
            self.source_ids[(*scope, token)] += 1
        else:
            self.source_key_limit_reached = True
        if provider is None:
            self.missing_provider_times += 1
        else:
            key = (*scope, provider)
            if len(self.provider_times) < 100000 or key in self.provider_times:
                self.provider_times[key] += 1
                position = (row.get('gsi_probe') or {}).get('own_position') or {}
                signature = (row.get('clock_time_s') if number(row.get('clock_time_s')) else None,
                    position.get('hero_id') if isinstance(position.get('hero_id'), str) else None,
                    position.get('xpos') if number(position.get('xpos')) else None,
                    position.get('ypos') if number(position.get('ypos')) else None)
                signatures = self.provider_signatures.setdefault(key, set())
                if len(signatures) < 2:
                    signatures.add(signature)  # Two suffice to prove timestamp ambiguity.
            else:
                self.source_key_limit_reached = True
        probe = row.get('gsi_probe')
        if not isinstance(probe, dict):
            self.missing_probe += 1
            self.previous = None
            return
        if type(probe.get('schema_version')) is not int or probe['schema_version'] != 1:
            self.unsupported_probe += 1
            self.previous = None
            return
        self.probe_rows += 1
        status = row.get('self_status') or {}
        status = status if isinstance(status, dict) else {}
        status_valid = type(status.get('schema_version')) is int and status.get('schema_version') == 1
        flags = status.get('flags') if status_valid else None
        flags = flags if isinstance(flags, dict) else {}
        for key in FLAGS:
            self.flags[key][boolean(flags.get(key))] += 1
        day = probe.get('map_daylight') or {}
        for key in self.daylight:
            self.daylight[key][boolean(day.get(key))] += 1
        obj = probe.get('objectives') or {}
        code = obj.get('map_roshan_state_type')
        self.roshan_types[code if isinstance(code, str) and code in set('-nbifsda?') else 'unreported'] += 1
        nodes = obj.get('nodes') or {}
        for key in self.objectives:
            shape = (nodes.get(key) or {}).get('shape')
            self.objectives[key][shape if shape in ('missing', 'empty_object', 'object', 'team_nested', 'array', 'empty_array') else 'unreported_or_other'] += 1
        for event in obj.get('events') or []:
            kind = event.get('type') if isinstance(event, dict) else None
            if isinstance(kind, str) and re.fullmatch('[A-Za-z0-9_]{1,64}', kind):
                self.event_types[kind] += 1
                game_time = event.get('game_time') if number(event.get('game_time')) else None
                timestamp = event.get('timestamp') if number(event.get('timestamp')) else None
                if game_time is None and timestamp is None:
                    self.untimed_objectives[kind] += 1
                else:
                    key = kind, game_time, timestamp
                    if key not in self.objective_keys:
                        if len(self.objective_keys) >= 1024:
                            self.objective_key_limit_reached = True
                        else:
                            self.objective_keys[key] = {'type': kind, 'reported_game_time': game_time,
                                'reported_timestamp': timestamp, 'first_sequence': row.get('sequence'),
                                'last_sequence': row.get('sequence'), 'mentions': 0,
                                'first_observed_match_clock_s': row.get('clock_time_s') if number(row.get('clock_time_s')) else None,
                                'first_observed_map_game_time_s': (row.get('map_context') or {}).get('game_time_s')
                                    if number((row.get('map_context') or {}).get('game_time_s')) else None,
                                'time_basis': 'original_GSI_event_values_not_assumed_UTC_or_match_clock'}
                    if key in self.objective_keys:
                        self.objective_keys[key]['mentions'] += 1
                        self.objective_keys[key]['last_sequence'] = row.get('sequence')
        position = probe.get('own_position') or {}
        pstatus = position.get('status')
        self.position_status[pstatus if pstatus in ('reported', 'not_local_player', 'not_reported_or_invalid') else 'unreported_or_other'] += 1
        if pstatus == 'reported':
            hid, x, y = position.get('hero_id'), position.get('xpos'), position.get('ypos')
            economy = row.get('self_economy') or {}
            if (not status_valid or probe.get('activity') != 'playing' or position.get('basis') != 'current_local_gsi_not_screen_position'
                    or not isinstance(hid, str) or not re.fullmatch('npc_dota_hero_[a-z0-9_]{1,80}', hid)
                    or status.get('hero_id') != hid or economy.get('hero_id') not in (None, hid)):
                self.position_rejected['own_identity_or_basis_not_confirmed'] += 1
            elif not number(x) or not number(y):
                self.position_rejected['axes_missing_or_invalid'] += 1
            else:
                self.position_rows += 1
                received, provider = stamp(row.get('received_at_utc')), stamp(row.get('provider_at_utc'))
                age = received-provider if received is not None and provider is not None else None
                alive = flags.get('alive') if type(flags.get('alive')) is bool else None
                self.living_positions += int(alive is True)
                self.fresh_living_positions += int(alive is True and age is not None and 0 <= age <= 5)
                if self.bounds is None:
                    self.bounds = {'x_min': x, 'x_max': x, 'y_min': y, 'y_max': y}
                else:
                    for key, value, fn in (('x_min', x, min), ('x_max', x, max), ('y_min', y, min), ('y_max', y, max)):
                        self.bounds[key] = fn(self.bounds[key], value)
                if self.collect_positions and len(self.positions) < self.position_limit:
                    self.positions.append({**{k: row.get(k) for k in POSITION_COLUMNS if k not in ('line','hero_id','xpos','ypos')},
                                           'line': line, 'hero_id': hid, 'xpos': x, 'ypos': y,
                                           'alive': alive, 'source_age_s': age,
                                           'game_state': (row.get('map_context') or {}).get('game_state'),
                                           **{key: day.get(key) if type(day.get(key)) is bool else None
                                              for key in ('paused', 'daytime', 'nightstalker_night')}})
        # Only describe a sampled boolean edge when consecutive local records
        # have the same explicit identity and fresh source/receipt clocks.
        current = {'flags': flags, 'day': day, 'row': row, 'hero_id': status.get('hero_id') if status_valid else None}
        prior = self.previous
        if prior is not None:
            old = prior['row']
            identity = all(row.get(k) == old.get(k) and row.get(k) is not None
                           for k in ('match_id', 'game_session_id', 'record_session_id'))
            times = [row.get('provider_timestamp'), old.get('provider_timestamp'),
                     stamp(row.get('received_at_utc')), stamp(old.get('received_at_utc'))]
            fresh = (identity and current['hero_id'] is not None and current['hero_id'] == prior['hero_id']
                     and all(number(v) for v in times) and 0 <= times[0]-times[1] <= 2
                     and 0 < times[2]-times[3] <= 2.5
                     and type(row.get('sequence')) is int and type(old.get('sequence')) is int
                     and row['sequence'] > old['sequence'])
            if fresh:
                for group, keys in (('flags', FLAGS), ('day', tuple(self.daylight))):
                    for key in keys:
                        a, b = prior[group].get(key), current[group].get(key)
                        if type(a) is bool and type(b) is bool and a != b:
                            label = ('hero.' if group == 'flags' else 'map.') + key
                            self.transition_counts[label] += 1
                            if len(self.transitions) < 1000:
                                self.transitions.append({'field': label, 'from': a, 'to': b,
                                    'previous_sequence': old['sequence'], 'sequence': row['sequence'],
                                    'provider_at_utc': row.get('provider_at_utc'),
                                    'received_at_utc': row.get('received_at_utc'), 'clock_time_s': row.get('clock_time_s'),
                                    'basis': 'consecutive_sampled_boolean_change_not_exact_effect_boundary'})
            else:
                self.transition_gaps['identity_time_or_sampling_gap'] += 1
        self.previous = current

    def result(self):
        return {'schema_version': 1, 'rows': self.rows, 'probe_rows': self.probe_rows,
            'source_identity_evidence': {
                'distinct_provider_times': len(self.provider_times),
                'duplicated_provider_times': sum(n > 1 for n in self.provider_times.values()),
                'provider_times_with_distinct_position_or_clock': sum(len(s) > 1 for s in self.provider_signatures.values()),
                'max_rows_same_provider_time': max(self.provider_times.values(), default=0),
                'missing_provider_time_rows': self.missing_provider_times,
                'source_id_rows': sum(self.source_ids.values()), 'distinct_source_ids': len(self.source_ids),
                'duplicate_source_ids': sum(n > 1 for n in self.source_ids.values()),
                'missing_source_id_rows': self.missing_source_ids, 'invalid_source_id_rows': self.invalid_source_ids,
                'key_limit_reached': self.source_key_limit_reached, 'timestamp_is_unique_identity': False},
            'missing_probe_rows': self.missing_probe, 'unsupported_probe_rows': self.unsupported_probe,
            'position_status': dict(self.position_status), 'valid_local_position_rows': self.position_rows,
            'living_local_position_rows': self.living_positions,
            'fresh_living_local_position_rows': self.fresh_living_positions,
            'position_rejected': dict(self.position_rejected), 'raw_world_coordinate_bounds': self.bounds,
            'exported_position_rows': len(self.positions) if self.collect_positions else None,
            'position_export_truncated': self.collect_positions and self.position_rows > len(self.positions),
            'hero_flags': {k: dict(v) for k,v in self.flags.items()},
            'daylight': {k: dict(v) for k,v in self.daylight.items()},
            'objective_node_shapes': {k: dict(v) for k,v in self.objectives.items()},
            'map_roshan_state_types': dict(self.roshan_types),
            'objective_event_row_mentions': dict(self.event_types),
            'objective_distinct_timed_event_keys': dict(Counter(e['type'] for e in self.objective_keys.values())),
            'objective_untimed_event_mentions': dict(self.untimed_objectives),
            'objective_event_examples': list(self.objective_keys.values())[:25],
            'objective_event_examples_truncated': len(self.objective_keys) > 25,
            'objective_event_key_limit_reached': self.objective_key_limit_reached,
            'sampled_transition_counts': dict(self.transition_counts), 'sampled_transition_examples': self.transitions,
            'transition_examples_truncated': sum(self.transition_counts.values()) > len(self.transitions),
            'transition_gap_counts': dict(self.transition_gaps),
            'map_pixel_mapping_verified': False, 'complete_modifier_list_verified': False,
            'missing_fields_prove_game_unsupported': False,
            'meaning': 'named_received_GSI_evidence_not_full_buff_or_TP_BKB_or_hidden_position_inference'}


def render_markdown(reports):
    lines = ['\n## GSI实际字段证据', '',
        '坐标为本人GSI原值，不是屏幕坐标或已认证地图映射。缺失不按false；magicimmune不直接等同BKB，TP物品CD不等同正在传送。', '',
        '| 比赛 | Probe行 | 本人坐标记录 | 其中存活且来源年龄≤5s | 无Probe/不支持版本 |', '|---|---:|---:|---:|---:|']
    for report in reports:
        c = report.get('gsi_evidence_census') or report.get('census')
        if c is not None:
            lines.append(f"| {report['match_id']} | {c['probe_rows']} | {c['valid_local_position_rows']} | "
                         f"{c['fresh_living_local_position_rows']} | "
                         f"{c['missing_probe_rows']}/{c['unsupported_probe_rows']} |")
    names = {'alive': '存活', 'silenced': '沉默', 'stunned': '眩晕', 'disarmed': '缴械',
             'hexed': '妖术', 'muted': '物品禁用', 'break': '被动失效', 'smoked': '诡计之雾',
             'has_debuff': '存在负面状态', 'magicimmune': '魔法免疫原字段', 'debuffimmune': '减益免疫',
             'rooted': '缠绕', 'invulnerable': '无敌', 'channeling': '持续施法', 'teleporting': '传送状态'}
    for report in reports:
        c = report.get('gsi_evidence_census') or report.get('census')
        if c is None:
            continue
        lines += ['', f"### 比赛 {report['match_id']} 的状态记录", '',
                  '数量是采样记录数，不是施法/控制次数；未报告与false分开。', '',
                  '| 字段 | true记录 | false记录 | 未报告 | 类型异常 |', '|---|---:|---:|---:|---:|']
        for key in FLAGS:
            values = c['hero_flags'][key]
            lines.append(f"| {names[key]}（{key}） | {values.get('true',0)} | {values.get('false',0)} | "
                         f"{values.get('not_reported',0)} | {values.get('invalid',0)} |")
        lines += ['', f"来源标识证据：{c.get('source_identity_evidence', '旧报告未统计')}。时间戳不作唯一记录标识。",
                  f"昼夜字段：{c['daylight']}",
                  f"公共目标节点形状：{c['objective_node_shapes']}",
                  f"目标事件提及：{c['objective_event_row_mentions']}；不同带时间的事件键：{c['objective_distinct_timed_event_keys']}。",
                  '不同事件键也不是完整对局击杀统计；事件保留/采样缺口未认证。']
        if c['objective_event_examples']:
            lines += ['', '| 事件 | 原game_time | 首次收到时比赛时钟 | 首次收到时map.game_time | 重复提及 |',
                      '|---|---:|---:|---:|---:|']
            for event in c['objective_event_examples']:
                lines.append(f"| {event['type']} | {event['reported_game_time']} | {event['first_observed_match_clock_s']} | "
                             f"{event['first_observed_map_game_time_s']} | {event['mentions']} |")
            lines += ['', '保留不同时间口径，不把原game_time直接当作比赛时钟或UTC。']
    return '\n'.join(lines)+'\n'
