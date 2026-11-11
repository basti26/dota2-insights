"""Independent optional lane-metadata reader; no helper source imports."""
from collections import Counter
import math
import re
from .helper_handoff import utc


class LaneWidgetSummary:
    def __init__(self, expected_session=None):
        self.expected_session = expected_session
        self.extensions = self.invalid = 0
        self.states = Counter()
        self.selected, self.tipped, self.packages, self.ready_states = set(), set(), set(), set()
        self.allies, self.ally_tips = set(), set()
        self.ally_extensions = 0
        self.evidence = []
        self.budget_exceeded = False

    def add(self, row, evidence):
        if 'laning' not in row:
            return
        if self.extensions >= 4096:
            self.budget_exceeded = True
            return
        self.extensions += 1
        try:
            data = row['laning']
            if (type(row.get('record_version')) is not int or row['record_version'] != 1
                    or not isinstance(row.get('session_id'), str) or not re.fullmatch('[a-f0-9]{32}', row['session_id'])
                    or self.expected_session is not None and row['session_id'] != self.expected_session
                    or type(row.get('revision')) is not int or row['revision'] < 0
                    or row.get('display_mode') != 'laning' or row.get('visible') is not True
                    or row.get('expanded') is not True or row.get('reason') != 'shown'
                    or row.get('advice_stage') not in {'waiting', 'ban', 'round_one', 'last_pick'}
                    or row.get('phase') not in {'waiting', 'ban', 'selection', 'strategy', 'in_game', 'finished'}
                    or utc(row['observed_at_utc']) > utc(row['written_at_utc'])):
                raise ValueError('invalid_lane_widget_context')
            fields = {'selected_hero_ids', 'tip_hero_ids', 'advice_status', 'package_sha256',
                      'rendered_text_sha256', 'viewport_y', 'meaning'}
            own_fields = {'selected_ally_hero_ids', 'tip_ally_hero_ids'}
            if not isinstance(data, dict) or set(data) not in (fields, fields | own_fields):
                raise ValueError('invalid_lane_metadata_fields')
            for key in ('selected_hero_ids', 'tip_hero_ids', *[k for k in own_fields if k in data]):
                heroes = data[key]
                if (not isinstance(heroes, list) or len(heroes) > 5
                        or any(not isinstance(h, str) or not re.fullmatch('npc_dota_hero_[a-z0-9_]{1,50}', h) for h in heroes)
                        or len(set(heroes)) != len(heroes)):
                    raise ValueError('invalid_lane_heroes')
            status = data['advice_status']
            allies, own_tips = data.get('selected_ally_hero_ids', []), data.get('tip_ally_hero_ids', [])
            if (status not in {'not_selected', 'loading', 'ready', 'unavailable'}
                    or (status == 'not_selected') != (not (data['selected_hero_ids'] or allies))
                    or not set(data['tip_hero_ids']) <= set(data['selected_hero_ids'])
                    or not set(own_tips) <= set(allies) or set(allies) & set(data['selected_hero_ids'])
                    or status != 'ready' and (data['tip_hero_ids'] or own_tips or data['package_sha256'] is not None)):
                raise ValueError('inconsistent_lane_metadata')
            for key in ('package_sha256', 'rendered_text_sha256'):
                value = data[key]
                if key == 'package_sha256' and value is None and status != 'ready': continue
                if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
                    raise ValueError('invalid_lane_digest')
            viewport = data['viewport_y']
            if (not isinstance(viewport, list) or len(viewport) != 2
                    or any(type(x) not in {int, float} or not math.isfinite(x) or not 0 <= x <= 1 for x in viewport)
                    or viewport[0] > viewport[1]
                    or data['meaning'] != 'widget_state_not_OS_pixels_or_user_reading'):
                raise ValueError('invalid_lane_viewport')
        except (KeyError, ValueError, TypeError, OverflowError):
            self.invalid += 1
            return
        self.states[status] += 1
        self.selected.update(data['selected_hero_ids'])
        self.tipped.update(data['tip_hero_ids'])
        self.allies.update(allies)
        self.ally_tips.update(own_tips)
        self.ally_extensions += int(own_fields <= set(data))
        if status == 'ready':
            self.packages.add(data['package_sha256'])
            # Scrolling changes widget state, not the submitted tip text.
            self.ready_states.add((row['session_id'], tuple(sorted(data['selected_hero_ids'])),
                tuple(sorted(data['tip_hero_ids'])), tuple(sorted(allies)), tuple(sorted(own_tips)),
                data['package_sha256'], data['rendered_text_sha256']))
        if len(self.evidence) < 32:
            self.evidence.append({'session_id': row['session_id'], 'revision': row['revision'],
                'advice_status': status, 'evidence': evidence})

    def result(self):
        return {'status': 'not_recorded_unknown' if not self.extensions else 'metadata_recorded',
            'extension_rows': self.extensions, 'invalid_rows': self.invalid,
            'recorded_state_counts': dict(self.states),
            'unique_ready_text_states': len(self.ready_states) if self.extensions else None,
            'declared_selected_hero_ids': sorted(self.selected), 'declared_tip_hero_ids': sorted(self.tipped),
            'ally_metadata_status': 'metadata_recorded' if self.ally_extensions else 'not_recorded_unknown',
            'ally_extension_rows': self.ally_extensions,
            'declared_selected_ally_hero_ids': sorted(self.allies) if self.ally_extensions else None,
            'declared_tip_ally_hero_ids': sorted(self.ally_tips) if self.ally_extensions else None,
            'declared_package_sha256': sorted(self.packages), 'evidence': self.evidence,
            'budget_exceeded': self.budget_exceeded, 'OS_pixels_verified': False, 'user_reading_verified': False,
            'selected_identity_independently_verified': False, 'advice_quality_verified': False,
            'meaning': 'source_scoped_widget_metadata_changes_not_views_or_reads; missing_extension_is_unknown'}


def summary_text(result):
    if result.get('status') == 'not_recorded_unknown':
        return '对线页元数据：未知（未记录扩展，不能解释为零次使用）。'
    text = (f"对线页元数据：有效{sum(result['recorded_state_counts'].values())}行，"
        f"无效{result['invalid_rows']}行；状态{result['recorded_state_counts']}；"
        f"不同ready文字状态{result['unique_ready_text_states']}，不是阅读次数。")
    if result.get('budget_exceeded'):
        text += ' 已达分析预算，结果不代表完整内容。'
    return text + ' 未认证屏幕像素、对位身份或建议效果。'
