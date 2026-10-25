"""Offline blind-review batches. Integrity checks do not certify screenshot truth."""
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import shutil

from dota2_insights.ward_observation_check import (
    FIELDS, compare_interval, compare_lifetime, compare_world_position,
    load_blind, number, preflight,
)
from dota2_insights.ward_validation_package import sha


def read_json(path, limit=4 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError('review_json_budget')
    body = path.read_bytes()
    return json.loads(body), body


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def freeze_batch(package, observations, output, sample_ids=None, *, prior_exposure='not_declared'):
    """Copy a reviewed subset before opening answers; preserve the full blind roster."""
    output, observations = Path(output), Path(observations)
    if prior_exposure not in ('not_declared', 'blind_declared', 'previously_compared'):
        raise ValueError('invalid_prior_exposure_declaration')
    if output.exists():
        raise ValueError('new_batch_directory_required')
    checked = preflight(package, observations)
    observed, body = read_json(observations)
    states = {r['sample_id']: r['fields'] for r in checked['samples']}
    chosen = list(sample_ids) if sample_ids else [key for key, fields in states.items()
        if any(v != 'pending' for v in fields.values())]
    if not chosen or len(set(chosen)) != len(chosen) or any(key not in states for key in chosen):
        raise ValueError('nonempty_unique_reviewed_sample_ids_required')
    if any(all(v == 'pending' for v in states[key].values()) for key in chosen):
        raise ValueError('sample_has_no_reviewed_field')
    output.mkdir(parents=True)
    copied = {}
    # Keep all frames in the original observation file, including unknown-field evidence.
    for row in observed['evidence']:
        source = (observations.parent / row['file']).resolve()
        target = (output / row['file']).resolve()
        if not target.is_relative_to(output.resolve()) or target == output.resolve():
            raise ValueError('frame_outside_batch')
        if row['file'] in ('observations.json', 'batch.json', 'batch.sha256', 'preflight.json'):
            raise ValueError('reserved_frame_path')
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha(target.read_bytes()) != row['sha256']:
                raise ValueError('duplicate_frame_path_conflict')
        else:
            shutil.copyfile(source, target)
        copied[row['file']] = row['sha256']
    (output / 'observations.json').write_bytes(body)
    check_copy = preflight(package, output / 'observations.json')
    if check_copy != checked or observations.read_bytes() != body:
        raise ValueError('observation_changed_during_freeze')
    write_new(output / 'preflight.json', checked)
    receipt = {'schema_version': 1, 'kind': 'frozen_ward_review_batch',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'match_id': checked['match_id'], 'role': checked['role'],
        'cohort_key': checked['cohort_key'], 'package_sha256': checked['package_sha256'],
        'source_freeze_sha256': checked['source_freeze_sha256'],
        'observations_sha256': sha(body), 'preflight_sha256': sha((output / 'preflight.json').read_bytes()),
        'frame_files': copied, 'sample_ids': chosen,
        'observer_prior_exposure': prior_exposure,
        'independent_validation_passed': False,
        'scope': 'snapshot before this tool opens answers; cannot prove observer prior exposure or pixel semantics'}
    write_new(output / 'batch.json', receipt)
    (output / 'batch.sha256').write_text(sha((output / 'batch.json').read_bytes()) + '\n', encoding='ascii')
    return receipt


def load_batch(package, batch):
    batch = Path(batch)
    receipt, body = read_json(batch / 'batch.json')
    if sha(body) != (batch / 'batch.sha256').read_text(encoding='ascii').strip():
        raise ValueError('batch_fingerprint_mismatch')
    if receipt.get('kind') != 'frozen_ward_review_batch' or receipt.get('schema_version') != 1:
        raise ValueError('frozen_review_batch_required')
    observed, observed_body = read_json(batch / 'observations.json')
    if sha(observed_body) != receipt.get('observations_sha256'):
        raise ValueError('frozen_observations_changed')
    checked = preflight(package, batch / 'observations.json')
    saved, saved_body = read_json(batch / 'preflight.json')
    if sha(saved_body) != receipt.get('preflight_sha256') or saved != checked:
        raise ValueError('frozen_preflight_changed')
    for key in ('package_sha256', 'source_freeze_sha256', 'match_id', 'role', 'cohort_key'):
        if receipt.get(key) != checked.get(key):
            raise ValueError('batch_binding_mismatch:' + key)
    if receipt.get('frame_files') != {r['file']: r['sha256'] for r in observed['evidence']}:
        raise ValueError('frozen_frame_manifest_changed')
    ids = receipt.get('sample_ids', [])
    if not ids or len(set(ids)) != len(ids) or not set(ids) <= {r['sample_id'] for r in observed['samples']}:
        raise ValueError('invalid_frozen_sample_subset')
    return receipt, observed, checked, body


def compare_field(field, row, value):
    if field == 'ward_type':
        expected = value.get('ward_type')
        return ('unjudgeable' if expected not in ('observer', 'sentry') else
                'pass' if expected == row['observed_type'] else 'mismatch'), None
    if field == 'team':
        expected = value.get('team')
        return ('unjudgeable' if type(expected) is not int or expected not in (2, 3) else
                'pass' if expected == row['observed_team'] else 'mismatch'), None
    if field in ('placed_time', 'ended_time'):
        key = 'placed' if field == 'placed_time' else 'ended'
        return compare_interval(value.get(key + '_game_time_s'), row[key + '_window_s']), None
    if field == 'lifetime':
        return compare_lifetime(value.get('duration_s'), row['placed_window_s'], row['ended_window_s']), None
    if field == 'world_position':
        if not row.get('world_coordinate_system') or row['world_coordinate_system'] != value.get('coordinate_basis'):
            return 'unjudgeable', 'world_coordinate_system_not_independently_aligned'
        return compare_world_position([value.get('x'), value.get('y')], row['world_position']), None
    if field == 'map_position':
        # A textual landmark declaration is not a geometric calibration certificate.
        return 'unjudgeable', 'independent_map_calibration_and_projection_required'
    if field == 'end_reason':
        observed, expected = row['end_reason'], value.get('end_kind')
        if observed == 'expired':
            return ('pass' if expected == 'expired' else 'unjudgeable' if expected in (None, 'unknown', 'unobserved')
                    else 'mismatch'), None
        if observed == 'match_end_censored':
            return 'unjudgeable', 'censoring_requires_independent_match_end_and_interval_evidence'
        if expected not in ('killed', 'expired'):
            return 'unjudgeable', 'program_end_unknown'
        if expected != 'killed':
            return 'mismatch', None
        killer = row.get('observed_killer')
        killer_team = row.get('observed_killer_team')
        if (not isinstance(killer, str) or not killer or type(killer_team) is not int
                or killer_team not in (2, 3)):
            return 'unjudgeable', 'killed_is_not_evidence_of_enemy_deward_vs_deny'
        if killer != value.get('killer'):
            return 'mismatch', 'independently_observed_actor_differs'
        relation = 'deny' if killer_team == row['observed_team'] else 'enemy_deward'
        return ('pass' if observed == relation else 'mismatch'), None
    raise ValueError('unknown_comparison_field')


def compare_batch(package, batch):
    receipt, observed, checked, batch_body = load_batch(package, batch)
    manifest, package_sha, blind = load_blind(package)
    # No answers opened until all frozen observations and frames have passed checks.
    answers, answer_body = read_json(Path(package) / 'sealed-answers/answers.json', 16 * 1024 * 1024)
    if sha(answer_body) != manifest['files']['sealed-answers/answers.json']:
        raise ValueError('answers_fingerprint_mismatch')
    blind_by_id = {r['sample_id']: r for r in blind}
    if (not isinstance(answers, list) or len(answers) != len(blind)
            or len({r['sample_id'] for r in answers}) != len(answers)
            or {r['sample_id'] for r in answers} != set(blind_by_id)):
        raise ValueError('complete_unique_answer_roster_required')
    selected = {r['sample_id']: r for r in answers if r['sample_id'] in receipt['sample_ids']}
    inputs = {r['sample_id']: r for r in observed['samples']}
    before = {r['sample_id']: r['fields'] for r in checked['samples']}
    result_rows = []
    for key in receipt['sample_ids']:
        answer, row = selected[key], inputs[key]
        if answer.get('record_index') != row['record_index'] or answer.get('match_id') != receipt['match_id']:
            raise ValueError('selected_answer_identity_mismatch')
        fields, reasons = {}, {}
        for field in FIELDS:
            state = before[key][field]
            if state != 'ready_for_comparison':
                fields[field] = state
                if state == 'unjudgeable':
                    reasons[field] = row['unjudgeable_fields'][field]
                continue
            fields[field], reason = compare_field(field, row, answer['record'])
            if reason:
                reasons[field] = reason
        result_rows.append({'sample_id': key, 'record_index': row['record_index'],
            'group': blind_by_id[key]['group'], 'fields': fields, 'reasons': reasons,
            'field_evidence': row['field_evidence'],
            'program_values': {k: answer['record'].get(k) for k in ('ward_type', 'team',
                'placed_game_time_s', 'ended_game_time_s', 'duration_s', 'end_kind', 'killer')}})
    counts = Counter(v for row in result_rows for v in row['fields'].values())
    by_group = {group: dict(Counter(v for row in result_rows if row['group'] == group
                                  for v in row['fields'].values())) for group in ('random', 'special')}
    # Check the whole frozen snapshot again after reading answers.
    if load_batch(package, batch)[3] != batch_body:
        raise ValueError('batch_changed_during_comparison')
    return {'schema_version': 1, 'kind': 'ward_review_comparison',
        'status': 'partial_review_compared' if len(result_rows) < len(blind) else 'review_compared',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'match_id': receipt['match_id'], 'role': receipt['role'], 'cohort_key': receipt['cohort_key'],
        'source_freeze_sha256': receipt['source_freeze_sha256'], 'package_sha256': package_sha,
        'batch_sha256': sha(batch_body), 'observations_sha256': receipt['observations_sha256'],
        'observer_prior_exposure': receipt.get('observer_prior_exposure', 'not_declared'),
        'answer_file_sha256': sha(answer_body), 'comparison_code_sha256': sha(Path(__file__).read_bytes()),
        'checker_code_sha256': sha(Path(__file__).with_name('ward_observation_check.py').read_bytes()),
        'counts': dict(counts), 'counts_by_group': by_group, 'sample_count': len(result_rows),
        'package_sample_count': len(blind), 'remaining_samples': len(blind) - len(result_rows),
        'map_calibration_verified': False, 'independent_validation_passed': False,
        'scope': 'field comparison against declared client observations; no automatic certification of screenshots or map',
        'samples': result_rows}


def render_comparison(result, batch, output):
    output, batch = Path(output), Path(batch)
    if output.exists():
        raise ValueError('new_report_directory_required')
    output.mkdir(parents=True)
    observed, _ = read_json(batch / 'observations.json')
    frames = {r['evidence_id']: r for r in observed['evidence']}
    write_new(output / 'comparison.json', result)
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>眼位独立核验</title>',
        '<style>body{font:16px system-ui;max-width:1150px;margin:30px auto;padding:16px;background:#17222c;color:#edf2f6}table{border-collapse:collapse;width:100%}td,th{border:1px solid #7d8a95;padding:9px}a{color:#8cd6ff}img{max-width:100%}details{margin:14px 0}</style>',
        '<h1>眼位独立观察分项对照</h1>',
        '<p>整体验证未通过；地图尚未认证。本页比较文件中已提交的观察，不能证明截图语义真实或观察者此前未见答案。</p>',
        f'<p>比赛 {html.escape(result["match_id"])}；本批{result["sample_count"]}枚；本包尚余{result["remaining_samples"]}枚未进入本批。</p>',
        '<p>随机与专项分开计数，不计算合并准确率。<a href="comparison.json">完整分项与指纹</a></p>',
        '<pre>' + html.escape(json.dumps(result['counts_by_group'], ensure_ascii=False, indent=2)) + '</pre>',
        '<table><tr><th>样本/分组</th><th>字段</th><th>结果</th><th>原因及证据</th></tr>']
    labels = {'pass': '一致', 'mismatch': '不符', 'pending': '待核对', 'unjudgeable': '无法判断'}
    used = set()
    for row in result['samples']:
        for field, verdict in row['fields'].items():
            refs = row['field_evidence'].get(field, [])
            used.update(ref for ref in refs if ref in frames)
            links = ' '.join(
                f'<a href="#{html.escape(ref, quote=True)}">{html.escape(ref)}</a>'
                if ref in frames else html.escape(str(ref) + '（证据引用无效）')
                for ref in refs)
            parts.append('<tr><td>' + html.escape(row['sample_id'] + ' / ' + row['group']) + '</td><td>' +
                html.escape(field) + '</td><td>' + labels[verdict] + '</td><td>' +
                html.escape(row['reasons'].get(field, '')) + ' ' + links + '</td></tr>')
    parts.append('</table><h2>原始截图</h2>')
    for key in sorted(used):
        frame = frames[key]
        relative = Path('frames') / (frame['sha256'] + Path(frame['file']).suffix.lower())
        target = output / relative
        target.parent.mkdir(exist_ok=True)
        source = batch / frame['file']
        if sha(source.read_bytes()) != frame['sha256']:
            raise ValueError('report_frame_changed')
        if not target.exists():
            shutil.copyfile(source, target)
        url = relative.as_posix()
        parts.append(f'<details id="{html.escape(key, quote=True)}"><summary>{html.escape(key)} / 比赛秒 {frame["game_time_s"]}</summary>'
                     f'<a href="{url}"><img loading="lazy" src="{url}"></a><p>SHA256 {frame["sha256"]}</p></details>')
    (output / 'index.html').write_text(''.join(parts) + '</html>', encoding='utf-8')


def review_queue(packages, *, remaining_ledger=None):
    """Chronological navigation from blind tables only; no end-time answers leaked."""
    rows, seen, blind_sources = [], set(), {}
    for package in packages:
        manifest, fingerprint, blind = load_blind(package)
        blind_sources[str((Path(package) / 'review/blind.json').resolve())] = manifest['files']['review/blind.json']
        for row in blind:
            identity = (manifest['match_id'], row['sample_id'])
            if identity in seen:
                raise ValueError('duplicate_queue_sample')
            seen.add(identity)
            rows.append({'match_id': manifest['match_id'], 'sample_id': row['sample_id'],
                'record_index': row['record_index'], 'group': row['group'],
                'package_sha256': fingerprint, 'navigation_game_s': row['navigation_game_s'],
                'placement_status': 'pending_client_review', 'end_navigation_game_s': None,
                'end_status': 'requires_independent_observation_not_program_answer'})
    selection = None
    if remaining_ledger is not None:
        ledger, body = read_json(remaining_ledger)
        if (ledger.get('kind') != 'existing_development_ward_review_ledger'
                or ledger.get('schema_version') != 1 or ledger.get('answers_read') is not False
                or ledger.get('new_sampling_performed') is not False
                or ledger.get('development_total') != len(rows)
                or not isinstance(ledger.get('remaining'), list)
                or not isinstance(ledger.get('previously_compared'), list)):
            raise ValueError('remaining_ledger_invalid')
        root = Path(__file__).resolve().parents[1]
        sources = ledger.get('sources', [])
        if (not isinstance(sources, list) or len(sources) != len(blind_sources)
                or any(not isinstance(s, dict) or not isinstance(s.get('file'), str)
                       or not isinstance(s.get('sha256'), str) for s in sources)
                or {str((root / s['file']).resolve()): s['sha256'] for s in sources} != blind_sources):
            raise ValueError('remaining_ledger_source_binding')
        universe = {(r['match_id'], r['sample_id']): r for r in rows}
        included, excluded, selected = set(), set(), set()
        for name, expected, destination in (
            ('remaining', 'pending_client_observation', included),
            ('previously_compared', 'previously_compared_not_blind', excluded),
        ):
            for item in ledger[name]:
                key = item['match_id'], item['sample_id']
                if (key in selected or key not in universe or item.get('review_state') != expected
                        or any(item.get(field) != universe[key][field] for field in
                            ('record_index', 'group', 'navigation_game_s'))):
                    raise ValueError('remaining_ledger_roster_or_navigation_changed')
                selected.add(key)
                destination.add(key)
        if selected != set(universe) or not included:
            raise ValueError('remaining_ledger_incomplete_roster')
        if dict(Counter(universe[k]['group'] for k in included)) != ledger.get('remaining_counts'):
            raise ValueError('remaining_ledger_group_count')
        selection = {'kind': 'remaining_development_only', 'ledger_sha256': sha(body),
                     'original_sample_count': len(rows),
                     'excluded_previously_compared': [universe[k] for k in sorted(excluded)],
                     'exclusion_basis': 'ledger_declares_prior_exposure_not_new_validation'}
        rows = [r for r in rows if (r['match_id'], r['sample_id']) in included]
    rows.sort(key=lambda r: ((0 if r['group'] == 'random' else 1) if selection else 0,
        r['match_id'], r['navigation_game_s'] if number(r['navigation_game_s']) else float('inf'), r['record_index']))
    result = {'schema_version': 1, 'kind': 'blind_ward_review_queue', 'sample_count': len(rows),
        'passes': ['placement_type_team_and_location', 'independently_located_end_events'],
        'rules': {'max_time_bracket_s': 2, 'coarse_review_is_not_strict_pass': True,
                  'stale_hud_requires_playback_refresh': True, 'map_calibration_first': True},
        'independent_validation_passed': False, 'samples': rows}
    if selection:
        result['selection'] = selection
    return result


def render_queue(result, output):
    """Show only blind navigation data, without opening sealed answers."""
    output = Path(output)
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        '<title>眼位盲核验导航</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;background:#17222c;color:#edf2f6}td,th{border:1px solid #8c9baa;padding:10px}table{border-collapse:collapse}a{color:#8cd6ff}</style>',
        '<h1>眼位盲核验导航</h1>' + ('<p>仅本次待观察清单：已暴露项按绑定台账排除，不重新抽样；先随机、后专项。此队列不表示验证已通过。</p>'
            if result.get('selection') else '<p>完整抽样名单，不代表剩余未核对数。已核对的两枚仍保留原位，请按观察回执识别已完成项。</p>'),
        '<p>先按时间顺序记录放置、眼型和阵营，再独立寻找结束事件。结束时间不从程序答案预填。事件区间最长2秒；跳转后先刷新播放，地图仍待独立标定。</p>',
        '<p><a href="queue.json">原始导航与分组</a></p>',
        '<table><tr><th>比赛</th><th>样本</th><th>分组</th><th>放置导航（比赛秒）</th><th>结束事件</th></tr>']
    for row in result['samples']:
        parts.append('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in
            (row['match_id'], row['sample_id'], row['group'], row['navigation_game_s'],
             '待独立观察')) + '</tr>')
    (output / 'index.html').write_text(''.join(parts) + '</table></html>', encoding='utf-8')
