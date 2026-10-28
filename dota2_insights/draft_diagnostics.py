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


class DraftDiagnostics:
    def __init__(self):
        self.events, self.ui = [], []

    def add(self, name, line, row, evidence):
        if name not in ('events.jsonl', 'ui-presented.jsonl'):
            return
        if len(self.events) + len(self.ui) >= 20000:
            raise ValueError('draft_diagnostics_row_budget')
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
            'candidate_count': len(advice.get('candidates') or []), 'message': advice.get('message'),
            'processing_ms': _duration(timing.get('processing_last')),
            'vision_ms': _duration(vision.get('total_ms')), 'advice_ms': _duration(timing.get('advice_last')),
            'timing_meaning': 'recorded_last_call_wall_time_not_independent_reveal_or_total_latency'})

    def result(self):
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
        return {'events': self.events, 'ui_application_evidence': matches,
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
    return '\n'.join(lines) + '\n'
