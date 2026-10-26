"""Source-verified recording inventory, not a recognition-accuracy assessment."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .helper_handoff import verify_package


CHANNELS = ('own_gsi', 'economy_context', 'bp', 'minimap', 'visuals',
            'audio', 'fps', 'process_metrics')
LABELS = dict(zip(CHANNELS, ('本人GSI', '经济识别', 'BP', '小地图', '画面',
                             '音频', '帧率', '进程性能')))
ACTIONS = {'no_recorded_channel': '未发现该通道记录，不能据此复盘',
    'resolve_closure_before_analysis': '先核实收尾状态，当前不进入内容分析',
    'review_coverage_gaps': '核对配额及覆盖缺口，不能视为完整对局',
    'align_launcher_scope_before_match_analysis': '仍为启动级共享资料，需先按场次对齐'}


def _channel(package, component, match):
    """Only explicit match folders and reported row identities bind to a match."""
    mid = match['match_id']
    declared = component.get('declared_match_id')
    if declared is not None and declared != mid:
        return None
    state = component.get('state')
    journals = []
    if state == 'closed_verified':
        for journal in component.get('journals', []):
            source = journal['source']
            if source not in package['sources']:
                raise ValueError('readiness_journal_not_in_verified_sources')
            stats = journal['summary']
            explicit = stats.get('reported_match_counts', {}).get(mid) if mid else None
            bound = explicit if explicit is not None else stats.get('rows') if declared is not None else None
            # CSV physical lines and shared unbound rows are never match observations.
            if stats.get('row_meaning') != 'json_objects':
                bound = None
            journals.append({'source': source, 'match_bound_rows': bound,
                'binding': 'reported_match_id' if explicit is not None else
                           'explicit_match_folder' if declared is not None else 'launcher_only',
                'launcher_rows': stats.get('rows'), 'unbound_rows': stats.get('unbound_rows'),
                'source_time_fields': stats.get('time_fields', {})})
    counts = [j['match_bound_rows'] for j in journals if j['match_bound_rows'] is not None]
    return {'kind': component['kind'], 'state': state,
        'match_bound_rows': sum(counts) if counts else None,
        'row_count_is_not_accuracy_or_unique_events': True,
        'warnings': component.get('warnings', []), 'issues': component.get('issues', []),
        'journals': journals}


def review_package(package, roots):
    verify_package(package, roots)
    matches = []
    for match in package['matches']:
        channels = []
        for kind in CHANNELS:
            found = [_channel(package, c, match) for c in package['components'] if c['kind'] == kind]
            found = [c for c in found if c is not None]
            # Keep multiple BP sessions/recorders separately; don't silently overwrite.
            channels.extend(found or [{'kind': kind, 'state': 'not_recorded',
                'match_bound_rows': None, 'warnings': [], 'issues': [], 'journals': []}])
        actions = []
        for channel in channels:
            kind = channel['kind']
            if channel['state'] == 'not_recorded':
                actions.append({'kind': kind, 'reason': 'no_recorded_channel'})
            elif channel['state'] != 'closed_verified':
                actions.append({'kind': kind, 'reason': 'resolve_closure_before_analysis'})
            elif channel['warnings']:
                actions.append({'kind': kind, 'reason': 'review_coverage_gaps'})
            if channel['state'] == 'closed_verified' and channel['match_bound_rows'] is None:
                actions.append({'kind': kind, 'reason': 'align_launcher_scope_before_match_analysis'})
        matches.append({**match, 'channels': channels, 'next_actions': actions})
    return {'launcher_run_id': package['launcher_run_id'], 'state': package['state'],
        'matches': matches, 'package_issues': package.get('issues', []),
        'source_verified': True, 'binary_assets_verified': False,
        'recognition_accuracy': None, 'full_match_coverage_verified': False}


def build_report(index_path, roots):
    """Read only content-addressed selected packages; never discover extra history."""
    index_path = Path(index_path)
    if index_path.stat().st_size > 2 * 1024 ** 2:
        raise ValueError('readiness_index_too_large')
    index = json.loads(index_path.read_text(encoding='utf-8'))
    if (index.get('kind') != 'helper_handoff_index' or type(index.get('schema_version')) is not int or index.get('schema_version') != 1
            or not isinstance(index.get('runs'), list) or len(index['runs']) > 25):
        raise ValueError('readiness_index_invalid')
    reports = []
    for run in index['runs']:
        pid = run.get('package_id')
        if pid is None and run.get('state') in ('waiting', 'blocked'):
            reports.append({'launcher_run_id': '未建立交接包', 'state': run['state'],
                'source_verified': False, 'matches': [], 'reason': run.get('reason', 'handoff_not_ready')})
            continue
        if not isinstance(pid, str) or not re.fullmatch('[a-f0-9]{64}', pid):
            raise ValueError('readiness_package_id_invalid')
        path = index_path.parent / 'packages' / (pid + '.json')
        if path.stat().st_size > 2 * 1024 ** 2:
            raise ValueError('readiness_package_too_large')
        raw = path.read_bytes()
        package = json.loads(raw)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != pid:
            raise ValueError('readiness_package_digest_mismatch')
        if package['launcher_run_id'] != run['launcher_run_id']:
            raise ValueError('readiness_index_package_identity_mismatch')
        if package['state'] not in ('ready', 'ready_with_gaps'):
            reports.append({'launcher_run_id': package['launcher_run_id'], 'package_id': pid,
                'state': package['state'], 'source_verified': False, 'matches': [],
                'issues': package.get('issues', []), 'reason': 'handoff_not_ready'})
            continue
        try:
            reviewed = review_package(package, roots)
        except (ValueError, OSError, KeyError):
            # No previous match counts survive a failed source revalidation.
            reviewed = {'launcher_run_id': package['launcher_run_id'], 'state': 'blocked',
                'source_verified': False, 'matches': [], 'reason': 'source_revalidation_failed'}
        reports.append({**reviewed, 'package_id': pid})
    return {'schema_version': 1, 'kind': 'record_readiness_report',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'selected_handoff_runs_not_all_history', 'runs': reports,
        'run_states': dict(Counter(r['state'] for r in reports)),
        'limits': ['Counts describe records, not unique captures or accuracy.',
            'Source timestamps are not necessarily match-specific or capture times.',
            'POST_GAME proves an end was recorded, not full match coverage.',
            'No screenshots, audio or replay truth were inspected.']}


def render_markdown(report):
    lines = ['# 复盘资料就绪报告', '', '仅检查本次选中的交接记录；数量不代表准确率或完整对局。',
             '共享启动记录未按比赛绑定时，不将整份数据归到每场比赛。', '']
    for run in report['runs']:
        lines += [f"## {run['launcher_run_id']}", '', f"接收状态：{run['state']}；来源复验：{run['source_verified']}", '']
        if not run['source_verified']:
            lines += ['未进入分析：' + run['reason'], '']
        for match in run['matches']:
            lines += [f"### 比赛 {match['match_id'] or '无比赛号（本地会话）'}", '',
                f"本地会话：{match['game_session_id']}",
                f"结束证据：{match['match_end_evidence']}（不代表全程采集）", '',
                '| 通道 | 收尾状态 | 明确关联本场的日志行数 | 警告/缺口 |',
                '|---|---|---:|---|']
            for channel in match['channels']:
                count = channel['match_bound_rows']
                warnings = ', '.join(channel['warnings'] + channel['issues']) or '—'
                lines.append(f"| {LABELS[channel['kind']]} | {channel['state']} | {count if count is not None else '未确定'} | {warnings} |")
            lines += ['', '经济资料明细（不同类型日志不能视为同一种观测）：', '',
                '| 日志 | 本场行数 | 来源时间字段及范围（可能覆盖整个启动记录） |',
                '|---|---:|---|']
            for channel in match['channels']:
                if channel['kind'] != 'economy_context':
                    continue
                for journal in channel['journals']:
                    count = journal['match_bound_rows']
                    times = '; '.join(f"{name}: {values.get('first')} → {values.get('last')}"
                        for name, values in journal['source_time_fields'].items()) or '未记录受支持的时间字段'
                    lines.append(f"| {Path(journal['source']['path']).name} | {count if count is not None else '未确定'} | {times} |")
            lines += ['', '待办（结构检查，不是识别结论）：', '']
            lines += [f"- {LABELS[a['kind']]}：{ACTIONS[a['reason']]}" for a in match['next_actions']]
            lines += ['']
    lines += ['未核验：图片内容、声音可闻性、英雄/物品正确率、完整比赛覆盖，以及录像独立真值。', '']
    return '\n'.join(lines)
