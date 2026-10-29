"""Recorded BP generation/application evidence; no visual-reveal latency claim."""
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import re


def _stamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.timestamp() if result.utcoffset() is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _signature(snapshot, advice):
    return hashlib.sha256(json.dumps([snapshot, advice], sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _duration(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _stage_timing(events, applications):
    """Recorded eligibility to recorded widget application, never reveal latency."""
    ending = next((e for e in events if e['phase'] in ('strategy', 'in_game', 'finished', 'post_game')), None)
    end_stamp = _stamp(ending['written_at_utc']) if ending else None
    stages = []
    for stage, own in (('round_one', 2), ('last_pick', 4)):
        eligible = [e for e in events if e['phase'] == 'selection'
                    and e['accepted_own_heroes'] == own
                    and e['accepted_enemy_heroes'] is not None and e['accepted_enemy_heroes'] >= own
                    and (ending is None or e['line'] < ending['line'])]
        ready = eligible[0] if eligible else None
        generated = [e for e in eligible if e['stage'] == stage and e['candidate_count'] > 0]
        first = generated[0] if generated else None
        generated_by_line = {e['line']: e for e in generated}
        applied = next((u for u in applications if u.get('status') == 'exact_view_unique_prior_event'
                        and u.get('event_line') in generated_by_line), None)

        def delay(target_event, target_stamp):
            if ready is None or target_event is None or target_stamp is None:
                return None
            stamps = [_stamp(e['written_at_utc']) for e in events
                      if ready['line'] <= e['line'] <= target_event['line']]
            if (not stamps or any(t is None for t in stamps)
                    or any(a > b for a, b in zip(stamps, stamps[1:])) or target_stamp < stamps[-1]):
                return None
            return round((target_stamp - stamps[0]) * 1000, 3)

        applied_stamp = _stamp(applied.get('presented_at_utc')) if applied else None
        stages.append({'stage': stage,
            'status': ('recorded_widget_application' if applied else 'generated_without_linked_widget_receipt'
                       if first else 'eligible_without_recorded_recommendation' if ready else 'eligibility_not_observed'),
            'eligible_event_line': ready['line'] if ready else None,
            'first_generated_event_line': first['line'] if first else None,
            'first_widget_receipt_line': applied['line'] if applied else None,
            'widget_surface': applied.get('surface') if applied else None,
            'widget_source_event_line': applied['event_line'] if applied else None,
            'eligibility_and_generation_same_event': bool(ready and first and ready['line'] == first['line']),
            'recorded_eligibility_to_generation_ms': (None if ready and first and ready['line'] == first['line'] else
                                                      delay(first, _stamp(first['written_at_utc']) if first else None)),
            'recorded_eligibility_to_widget_ms': delay(generated_by_line[applied['event_line']], applied_stamp) if applied else None,
            'widget_relative_to_recorded_end': ('unknown' if applied_stamp is None or delay(ending, end_stamp) is None else
                                               'before' if applied_stamp < end_stamp else 'at_or_after'),
            'basis': 'public_nonconflicting_roster_at_recorded_event_not_first_visible_frame'})
    return {'stages': stages, 'first_recorded_end_event_line': ending['line'] if ending else None,
            'first_recorded_end_phase': ending['phase'] if ending else None,
            'end_widget_hidden_verified': False,
            'limits': ['UTC differences are recorded log intervals, not screen reveal latency',
                       'missing receipts at prefix cutoff are unknown, not proof of no display',
                       'terminal event does not prove widget hidden or OS display',
                       'same event readiness and generation have no separately measured duration',
                       'unknown side, conflicts, unavailable clocks never become zero delay']}


class DraftDiagnostics:
    def __init__(self):
        self.events, self.ui, self.visibility = [], [], []

    def add(self, name, line, row, evidence):
        from .draft_advice_audit import audit
        if name not in ('events.jsonl', 'ui-presented.jsonl', 'panel-visibility.jsonl'):
            return
        if len(self.events) + len(self.ui) + len(self.visibility) >= 20000:
            raise ValueError('draft_diagnostics_row_budget')
        if name == 'panel-visibility.jsonl':
            required = ('session_id', 'revision', 'phase', 'advice_stage', 'visible', 'expanded',
                        'reason', 'observed_at_utc', 'written_at_utc')
            observed, written = _stamp(row.get('observed_at_utc')), _stamp(row.get('written_at_utc'))
            valid = (all(k in row for k in required) and type(row.get('record_version')) is int and row['record_version'] == 1
                and isinstance(row.get('session_id'), str) and re.fullmatch('[a-f0-9]{32}', row['session_id'])
                and type(row.get('revision')) is int and row['revision'] >= 0
                and row.get('phase') in ('waiting', 'ban', 'selection', 'strategy', 'in_game', 'finished')
                and row.get('advice_stage') in ('waiting', 'ban', 'round_one', 'last_pick')
                and type(row.get('visible')) is bool and type(row.get('expanded')) is bool
                and row.get('reason') in ('shown', 'outside_draft', 'worker_inactive', 'hidden_by_user',
                    'panel_collapsed', 'input_unavailable', 'game_not_foreground', 'window_unavailable')
                and row['visible'] == (row['reason'] == 'shown')
                and row.get('display_mode', 'bp') in ('bp', 'laning', 'roster')
                and observed is not None and written is not None and observed <= written)
            self.visibility.append({'line': line, 'line_sha256': evidence['line_sha256'],
                'status': 'recorded_widget_flag' if valid else 'invalid_widget_flag',
                **({k: row[k] for k in required} if valid else {}),
                **({'display_mode': row.get('display_mode', 'bp'),
                    'display_mode_basis': 'explicit' if 'display_mode' in row else 'legacy_default_not_recorded'} if valid else {})})
            return
        if name == 'ui-presented.jsonl':
            view = row.get('view_input') or {}
            snapshot, advice = view.get('snapshot'), view.get('advice')
            if not isinstance(snapshot, dict) or not isinstance(advice, dict):
                self.ui.append({'line': line, 'status': 'missing_exact_view_input'})
                return
            self.ui.append({'line': line, 'signature': _signature(snapshot, advice),
                'presented_at_utc': row.get('presented_at_utc'), 'surface': row.get('surface'),
                'phase': snapshot.get('phase'), 'candidate_count': len(advice.get('candidates') or []),
                'line_sha256': evidence['line_sha256']})
            return
        snapshot, advice = row.get('snapshot') or {}, row.get('advice') or {}
        slots = snapshot.get('slots') or []
        side = snapshot.get('own_side')
        accepted, conflicted, seen = Counter(), [], set()
        for slot in slots:
            key = slot.get('slot')
            if not isinstance(key, str) or not re.fullmatch(r'(left|right)[1-5]', key) or key in seen:
                raise ValueError('draft_diagnostics_slot_identity')
            seen.add(key)
            if slot.get('hero_id') and slot.get('conflict'):
                conflicted.append(key)
            if slot.get('hero_id') and slot.get('public') is True and not slot.get('conflict'):
                accepted[key[:-1]] += 1
        timing = row.get('timing_ms') or {}
        vision = timing.get('vision_last') or {}
        self.events.append({'line': line, 'line_sha256': evidence['line_sha256'],
            'signature': _signature(snapshot, advice), 'written_at_utc': row.get('written_at_utc'),
            'snapshot_observed_at_utc': snapshot.get('last_observed_at_utc'), 'phase': snapshot.get('phase'),
            'revision': snapshot.get('revision'), 'raw_hero_slots': sum(bool(s.get('hero_id')) for s in slots),
            'accepted_own_heroes': accepted[side] if side in ('left', 'right') else None,
            'accepted_enemy_heroes': accepted['right' if side == 'left' else 'left'] if side in ('left', 'right') else None,
            'conflicted_slots': conflicted, 'stage': advice.get('stage'),
            'advice_data_health': audit(snapshot, advice),
            'candidate_count': len(advice.get('candidates') or []), 'message': advice.get('message'),
            'processing_ms': _duration(timing.get('processing_last')),
            'vision_ms': _duration(vision.get('total_ms')), 'advice_ms': _duration(timing.get('advice_last')),
            'timing_meaning': 'recorded_last_call_wall_time_not_independent_reveal_or_total_latency'})

    def result(self, *, available_streams=None):
        matches = []
        for row in self.ui:
            result = dict(row)
            if 'signature' in row:
                same = [event for event in self.events if event['signature'] == row['signature']]
                when = _stamp(row.get('presented_at_utc'))
                before = [e for e in same if when is not None and _stamp(e['written_at_utc']) is not None
                          and _stamp(e['written_at_utc']) <= when]
                if len(before) == 1:
                    source = before[0]
                    result.update(status='exact_view_unique_prior_event', event_line=source['line'],
                                  recorded_generation_to_widget_ms=round((when - _stamp(source['written_at_utc'])) * 1000, 3))
                else:
                    result.update(status='ambiguous_or_missing_prior_event', event_line=None,
                                  recorded_generation_to_widget_ms=None)
            matches.append(result)
        selection = [e for e in self.events if e['phase'] == 'selection']
        flags = [r for r in self.visibility if r['status'] == 'recorded_widget_flag']
        visibility = {'stream_present': ('panel-visibility.jsonl' in available_streams
                                        if available_streams is not None else True if self.visibility else None),
            'rows': self.visibility, 'valid_rows': len(flags), 'invalid_rows': len(self.visibility)-len(flags),
            'recorded_reason_counts': dict(Counter(r['reason'] for r in flags)),
            'explicit_mode_counts': dict(Counter(r['display_mode'] for r in flags if r['display_mode_basis'] == 'explicit')),
            'legacy_missing_mode_rows': sum(r['display_mode_basis'] != 'explicit' for r in flags),
            'end_phase_explicit_bp_shown_lines': [r['line'] for r in flags if r['phase'] in ('strategy', 'in_game', 'finished')
                and r['visible'] and r['display_mode_basis'] == 'explicit' and r['display_mode'] == 'bp'],
            'os_visibility_verified': False, 'recommendation_application_verified': False,
            'limits': ['widget flag and mode intent only; not pixels or OS display',
                       'legacy missing mode is not proof BP content was shown',
                       'no revision-only join to recommendation or end event',
                       'missing stream or hidden flag is not proof of correct end transition']}
        return {'events': self.events, 'ui_application_evidence': matches,
            'stage_timing': _stage_timing(self.events, matches),
            'panel_visibility_evidence': visibility,
            'selection_events': len(selection),
            'selection_events_with_conflicts': sum(bool(e['conflicted_slots']) for e in selection),
            'selection_wait_messages': dict(Counter(e['message'] or 'unknown' for e in selection if not e['candidate_count'])),
            'selection_recommendation_event_lines': [e['line'] for e in selection if e['candidate_count']],
            'reveal_latency_verified': False, 'os_display_verified': False, 'recommendation_quality_verified': False,
            'meaning': 'prefix_recorded_state_and_exact_view_widget_application_only; absent_UI_is_unknown_not_failure'}


def render_markdown(reports):
    lines = ['\n## BP生成与应用链路', '',
        '冲突头像不算已确认阵容。仅用完整snapshot/advice一致且唯一的先行事件关联界面回执；不是英雄出现到屏幕显示的延迟。', '',
        '| 比赛/会话 | 选人事件 | 含冲突事件 | 推荐事件行 | 有精确先行事件的选人推荐应用回执 |',
        '|---|---:|---:|---|---:|']
    for report in reports:
        diag = report.get('diagnostics') or {}
        matches = diag.get('ui_application_evidence', [])
        count = sum(u.get('phase') == 'selection' and u.get('candidate_count', 0) > 0
                    and u.get('status') == 'exact_view_unique_prior_event' for u in matches)
        lines.append(f"| {report.get('effective_match_id') or report['session_id']} | {diag.get('selection_events', '未知')} | "
                     f"{diag.get('selection_events_with_conflicts', '未知')} | {diag.get('selection_recommendation_event_lines', [])} | "
                     f"{count if 'ui-presented.jsonl' in report.get('stream_rows', {}) else '未知（无回执流）'} |")
    lines += ['', '### BP各轮记录时序', '',
        '仅比较日志中的公开阵容、生成和控件回执；缺回执不判失败，结束事件不证明面板已隐藏。', '',
        '| 比赛/会话 | 轮次 | 证据状态 | 阵容满足到生成(ms) | 阵容满足到控件(ms) | 控件相对记录结束 |',
        '|---|---|---|---:|---:|---|']
    statuses = {'recorded_widget_application': '有控件应用回执',
                'generated_without_linked_widget_receipt': '已生成，关联回执未知',
                'eligible_without_recorded_recommendation': '阵容已满足，未记录推荐',
                'eligibility_not_observed': '未观察到满足阵容'}
    for report in reports:
        for stage in (report.get('diagnostics') or {}).get('stage_timing', {}).get('stages', []):
            def known(value):
                return '未知' if value is None else str(value)
            lines.append(f"| {report.get('effective_match_id') or report['session_id']} | "
                         f"{'首轮' if stage['stage'] == 'round_one' else '末选'} | {statuses[stage['status']]} | "
                         f"{'同事件（非耗时）' if stage['eligibility_and_generation_same_event'] else known(stage['recorded_eligibility_to_generation_ms'])} | "
                         f"{known(stage['recorded_eligibility_to_widget_ms'])} | "
                         f"{ {'before': '结束前', 'at_or_after': '结束时或之后', 'unknown': '未知'}[stage['widget_relative_to_recorded_end']]} |")
    lines += ['', '### 面板显隐记录', '',
        '只表示控件标志和显示模式意图，不认证OS呈现或推荐内容。旧记录未写模式时不强认BP；阶段结束也不推断已经隐藏。', '',
        '| 比赛/会话 | 显隐流 | 有效/无效行 | 原因计数 | 明确BP模式在结束阶段shown行（待查） |',
        '|---|---|---|---|---|']
    for report in reports:
        v = (report.get('diagnostics') or {}).get('panel_visibility_evidence') or {}
        present = v.get('stream_present')
        state = '已记录' if present is True else '缺失' if present is False else '未知'
        counts = f"{v['valid_rows']}/{v['invalid_rows']}" if present is True else '未知'
        lines.append(f"| {report.get('effective_match_id') or report['session_id']} | {state} | {counts} | "
                     f"{v.get('recorded_reason_counts', {}) if present is True else '未知'} | "
                     f"{v.get('end_phase_explicit_bp_shown_lines', []) if present is True else '未知'} |")
    return '\n'.join(lines) + '\n'
