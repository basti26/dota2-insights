"""Independent admission of writer-drained equipment observation handoffs."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re

from .helper_handoff import utc
from .match_journals import _json, _read
from .replay_holdout import refuse_locked_match


def _path(root, relative):
    root = Path(root).resolve(strict=True)
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise ValueError('observation_source_outside_root')
    for parent in (target, *target.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('observation_source_link_refused')
    return target


def verify(root, session, *, expected_package_id=None):
    if not isinstance(session, str) or not re.fullmatch(r'run_[a-f0-9]{32}', session):
        raise ValueError('observation_session_invalid')
    folder = f'matches/sessions/{session}'
    raw = _read(_path(root, folder + '/observation-handoff.json'), 16384)
    package_id = hashlib.sha256(raw).hexdigest()
    if expected_package_id is not None and package_id != expected_package_id:
        raise ValueError('observation_index_source_changed')
    document = _json(raw)
    mid = document.get('match_id')
    if not isinstance(mid, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', mid):
        raise ValueError('observation_match_invalid')
    # The small manifest declares identity; never open a journal before this.
    refuse_locked_match(mid)
    if _read(_path(root, folder + '/observation-handoff.COMPLETE'), 66).strip() != package_id.encode():
        raise ValueError('observation_not_complete')
    if (type(document.get('schema_version')) is not int or document['schema_version'] != 1
            or document.get('kind') != 'foresight_observation_handoff'
            or document.get('record_session_id') != session
            or not re.fullmatch(r'game_[a-f0-9]{32}', str(document.get('game_session_id', '')))
            or document.get('cutoff_jobs_drained') is not True
            or document.get('end_state') != 'DOTA_GAMERULES_STATE_POST_GAME'
            or document.get('scope') != 'bound_match_observations_submitted_before_post_game_cutoff'
            or document.get('late_inflight_work') != 'retired_by_source_epoch_not_added_after_cutoff'
            or document.get('earlier_unbound_observations') != 'not_included_or_relabelled'
            or document.get('other_channels') != 'not_closed_by_this_manifest'
            or document.get('images') != 'journal_declared_sha256_requires_separate_file_verification'
            or document.get('health_scope') != 'writer_cumulative_at_publication_not_per_match'):
        raise ValueError('observation_contract_invalid')
    utc(document.get('closed_at_utc'))
    utc(document.get('end_provider_at_utc'))
    count = document.get('accepted_observations_at_cutoff')
    epoch = document.get('capture_source_epoch_at_end')
    if type(count) is not int or not 0 <= count <= 10**7 or type(epoch) is not int or epoch < 0:
        raise ValueError('observation_cutoff_invalid')
    health = document.get('health') or {}
    if (not isinstance(health, dict) or any(type(health.get(k)) is not bool for k in ('error_present', 'image_limit_reached'))
            or any(type(health.get(k)) is not int or health[k] < 0 for k in ('dropped', 'images_failed'))):
        raise ValueError('observation_health_invalid')
    payload, rows = document.get('payload'), 0
    if payload is None and _path(root, f'matches/match_{mid}/{session}/observations.jsonl').exists():
        raise ValueError('observation_empty_manifest_disagrees')
    if payload is not None:
        if (not isinstance(payload, dict) or payload.get('path') != f'match_{mid}/{session}/observations.jsonl'
                or type(payload.get('bytes')) is not int or not 0 <= payload['bytes'] <= 128 * 1024**2
                or type(payload.get('rows')) is not int or not 0 <= payload['rows'] <= min(100000, count)):
            raise ValueError('observation_payload_invalid')
        path = _path(root, 'matches/' + payload['path'])
        digest, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            before = path.stat()
            if before.st_size != payload['bytes']:
                raise ValueError('observation_payload_size_changed')
            while True:
                line = stream.readline(1024**2 + 1)
                if not line:
                    break
                size += len(line)
                rows += 1
                if size > payload['bytes'] or rows > payload['rows'] or len(line) > 1024**2 or not line.endswith(b'\n'):
                    raise ValueError('observation_payload_budget_or_tail')
                row = _json(line)
                context = row.get('clock_context') or {}
                if (type(row.get('record_version')) is not int or row['record_version'] != 1
                        or row.get('session_id') != session or row.get('match_id') != mid
                        or context.get('game_session_id') not in (None, document['game_session_id'])
                        or context.get('record_session_id') not in (None, session)):
                    raise ValueError('observation_payload_identity_conflict')
                utc(row.get('captured_at_utc'))
                attempt = row.get('attempt')
                if not isinstance(attempt, dict) or attempt.get('captured_at_utc') != row['captured_at_utc']:
                    raise ValueError('observation_frame_time_conflict')
                if row.get('hud_image') is not None and (
                        not re.fullmatch(r'hud-[0-9]{6}\.png', str(row['hud_image']))
                        or not re.fullmatch(r'[a-f0-9]{64}', str(row.get('hud_image_sha256', '')))):
                    raise ValueError('observation_image_reference_invalid')
                digest.update(line)
            after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('observation_payload_changed_during_read')
        if size != payload['bytes'] or rows != payload['rows'] or digest.hexdigest() != payload.get('sha256'):
            raise ValueError('observation_payload_sha_changed')
    gap = health['error_present'] or health['dropped'] or health['images_failed'] or health['image_limit_reached'] or rows != count
    if document.get('recording_integrity') != ('gaps' if gap else 'complete'):
        raise ValueError('observation_health_disagrees')
    return document, package_id


@contextmanager
def open_rows(root, session, *, expected_package_id=None, with_evidence=False):
    document, package_id = verify(root, session, expected_package_id=expected_package_id)
    active = True
    def rows():
        if not active:
            raise ValueError('observation_reader_closed')
        payload = document['payload']
        if payload is None:
            return
        with _path(root, 'matches/' + payload['path']).open('rb') as stream:
            remaining = payload['bytes']
            number = 0
            while active and remaining:
                line = stream.readline(min(1024**2 + 1, remaining + 1))
                if not line or len(line) > min(1024**2, remaining) or not line.endswith(b'\n'):
                    raise ValueError('observation_changed_during_analysis')
                remaining -= len(line)
                number += 1
                row = _json(line)
                if with_evidence:
                    yield number, row, {'line': number, 'line_sha256': hashlib.sha256(line).hexdigest()}
                else:
                    yield row
            if active and remaining == 0:
                if stream.read(1):
                    raise ValueError('observation_grew_during_analysis')
                return
            raise ValueError('observation_reader_closed')
    try:
        yield document, rows()
    finally:
        active = False
        verify(root, session, expected_package_id=package_id)


def review(root, session, *, expected_package_id=None, source_reference=None, catalogue=None, catalogue_source=None):
    from collections import Counter
    from .item_review_priorities import ItemReviewPriorities
    from .equipment_continuity import EquipmentContinuity
    from .observation_index import journal_reference
    statuses, heroes, slots = Counter(), Counter(), Counter()
    priorities = ItemReviewPriorities(catalogue)
    continuity = EquipmentContinuity(catalogue)
    accepted = 0
    with open_rows(root, session, expected_package_id=expected_package_id, with_evidence=True) as (manifest, rows):
        ref = journal_reference(source_reference, manifest) if source_reference is not None else None
        for _, row, evidence in rows:
            continuity.add(row, {**evidence, **({'source': ref} if ref is not None else {})})
            if ref is not None:
                priorities.add(manifest['match_id'], row, {**evidence, 'source': ref})
            attempt = row['attempt']
            status = attempt.get('status')
            if isinstance(status, str):
                statuses[status] += 1
            accepted += attempt.get('accepted') is True
            hero = attempt.get('hero') or {}
            if isinstance(hero.get('hero_id'), str):
                heroes[hero['hero_id']] += 1
            for slot in hero.get('slots', []):
                if isinstance(slot, dict) and isinstance(slot.get('status'), str):
                    slots[slot['status']] += 1
    return {'match_id': manifest['match_id'], 'record_session_id': session, 'game_session_id': manifest['game_session_id'],
            'recording_integrity': manifest['recording_integrity'],
            'rows': manifest['payload']['rows'] if manifest['payload'] is not None else 0,
            'channel_has_observations': manifest['payload'] is not None and manifest['payload']['rows'] > 0,
            'observation_status_counts': dict(statuses), 'hero_observation_counts': dict(heroes),
            'slot_status_counts': dict(slots), 'accepted_observation_rows': accepted,
            'item_review_priorities': priorities.result(), 'priority_queue_limit_reached': priorities.at_capacity,
            'catalogue_source': catalogue_source,
            'equipment_continuity': continuity.result(),
            'priority_inputs_available': source_reference is not None and catalogue is not None,
            'images_verified': False, 'accuracy_verified': False,
            'source_verified_before_and_after': True, 'other_channels': 'not_closed_by_this_manifest'}


def render_markdown(reports, skipped, *, index_state):
    lines = ['\n## 原生逐局装备观测', '', f'索引状态：{index_state}。',
        '只复核日志边界和身份；accepted/known数量不表示识别准确率，图片尚未独立核验。', '']
    for report in reports:
        lines += [f"### 比赛 {report['match_id']}", '',
            f"来源：{report['root_key']} / {report['source']}", f"原生包：{report['package_id']}",
            f"完整性：{report['recording_integrity']}；观测{report['rows']}行；有观测：{report['channel_has_observations']}。",
            f"与关闭启动器装备日志范围重合：{report['also_in_closed_launcher_scope']}（不可相加）。",
            f"通过显式比赛及两种会话字段关联的原生GSI包：{report['linked_native_gsi_packages']}；未用UTC猜归属。",
            f"英雄观测行：{report['hero_observation_counts']}",
            f"格状态日志计数：{report['slot_status_counts']}",
            '其他通道未由本清单封存，没有认证实际英雄/物品图像。', '']
        continuity = report.get('equipment_continuity', {})
        lines += ['#### 大件识别中断复查', '',
            f"目录可用：{continuity.get('catalogue_available', False)}；状态变化计数：{continuity.get('counts', {})}。",
            '以下仅为同英雄同格的日志变化；不能推断装备真值、漏算金额或屏幕实际显示。', '']
        for event in continuity.get('episodes', []):
            known, lost = event['known'], event['first_unknown']
            recovered = event['next_known']
            select = lost['economy_selection']
            selection = (f"记录选择值 {select.get('selected_value')} / 当前原观测 {select.get('current_observed_value')}"
                         if select is not None else '此行未记录经济选择，实际显示未知')
            end = (f"后续known行 {recovered['evidence']['line']}，同物品ID：{event['next_known_same_item']}"
                   if recovered else '限定窗口内未见下一条known')
            lines.append(f"- {event['hero_id']} / {event['slot_id']} / {event['last_known_item']}："
                         f"known行 {known['evidence']['line']} → unknown行 {lost['evidence']['line']}"
                         f"（{lost['captured_at_utc']}，候选 {lost['candidate']}，{event['unknown_rows']}行）；"
                         f"{selection}；{end}。")
        lines.append('')
    for entry in skipped:
        lines.append(f"- 未分析：{entry.get('state', 'unknown')} / {entry.get('reason', '未通过交接')}")
    return '\n'.join(lines) + '\n'
