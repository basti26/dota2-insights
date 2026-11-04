"""Serial postgame economy review from independently admitted, closed sources."""
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import subprocess
import shutil
import sys
import time
import uuid

from .history import checked_output, history_roots
from .replay_holdout import refuse_locked_match

ROOT = Path(__file__).resolve().parents[1]


def utc():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, limit=2 * 1024**2):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('automatic_review_json_limit')
    return json.loads(raw)


def guard():
    from .replay_workbench import game_running
    if game_running():
        raise InterruptedError('game_running')


def endings(roots):
    """Download queue is a durable end notification, never an analysis permit."""
    from .match_journal_index import history_bases
    result = {}
    for _, _, base in history_bases(roots):
        path = base / 'replay-downloads/queue.json'
        if not path.is_file():
            continue
        value = read_json(path)
        tasks = value.get('tasks')
        if not isinstance(tasks, dict) or len(tasks) > 10000:
            raise ValueError('automatic_review_queue_invalid')
        for mid, row in tasks.items():
            if not isinstance(mid, str) or not mid.isdecimal() or not 1 <= len(mid) <= 20:
                continue
            try:
                refuse_locked_match(mid)
            except ValueError:
                continue
            ended = row.get('ended_at_utc') if isinstance(row, dict) else None
            if not isinstance(ended, str):
                continue
            stamp = datetime.fromisoformat(ended.replace('Z', '+00:00'))
            if stamp.utcoffset() is None:
                continue
            ended = stamp.astimezone(timezone.utc).isoformat()
            if mid not in result or ended > result[mid]['ended_at_utc']:
                result[mid] = {'match_id': mid, 'ended_at_utc': ended, 'base': str(base),
                    'download_state': row.get('state'), 'download_reason': row.get('reason')}
    return result


def inputs(root, roots, mid):
    refuse_locked_match(mid)
    from .observation_index import sync as sync_items
    from .match_journal_index import sync as sync_gsi
    equipment = sync_items(root, roots, latest=64)
    gsi = sync_gsi(root, roots, latest=64)
    return {kind: [e for e in index['entries'] if e.get('state') == 'ready_with_gaps' and e.get('match_id') == mid]
            for kind, index in (('equipment', equipment), ('gsi', gsi))}


def replay_path(task, directories):
    mid = task['match_id']
    refuse_locked_match(mid)
    candidates = [Path(task['base']) / 'replay-downloads' / (mid + '.dem')]
    candidates += [Path(p) / (mid + '.dem') for p in directories]
    for path in candidates:
        if path.is_file() and not path.is_symlink() and 16 <= path.stat().st_size <= 1024**3:
            # The parser independently checks actual footer identity before use.
            return path.resolve()
    return None


def reusable_archive(root, mid, source, task, config):
    """Reuse validated data exports, never another checkout's Python runtime."""
    allowed = root / 'diagnostics'
    configured = config.get('archive_directories', [])
    if not isinstance(configured, list) or len(configured) > 32:
        raise ValueError('automatic_archive_reuse_limit')
    candidates = ([task['archive']] if task.get('archive') else []) + configured
    source_sha = None
    for value in candidates:
        path = Path(value).resolve()
        if not path.is_relative_to(allowed) or not (path / 'manifest.json').is_file():
            continue
        manifest = read_json(path / 'manifest.json', 1024**2)
        if (manifest.get('match_id') != mid or manifest.get('parsed_match_id') != mid
                or manifest.get('source_unchanged_stat') is not True
                or manifest.get('export_complete') is not True or manifest.get('stop_reason') != 'replay_eof'
                or manifest.get('parse_error') is not None):
            continue
        if source_sha is None:
            with source.open('rb') as stream:
                source_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if source_sha == manifest.get('source_sha256'):
            return path  # Payload hashes are rechecked by the comparison reader.
    return None


def parse_replay(root, source, output, heartbeat, check=guard):
    check()
    if shutil.disk_usage(root).free < 2 * 1024**3:
        raise ValueError('automatic_replay_low_disk_space')
    argv = [sys.executable, str(root / 'tools/parse_dota_replay.py'), str(source), '--output', str(output),
        '--sample-hz', '1', '--start-game-time', '-120', '--end-game-time', '10800',
        '--max-seconds', '600', '--max-output-mib', '256']
    with output.with_suffix('.log').open('wb') as log:
        child = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        began = time.monotonic()
        try:
            while child.poll() is None:
                check()
                heartbeat('parsing_replay')
                if time.monotonic() - began > 630:
                    raise TimeoutError('automatic_replay_wall_limit')
                time.sleep(.5)
            check()
            if child.returncode:
                raise ValueError('automatic_replay_parse_incomplete')
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=3)


def record_review(root, roots, admitted, output, check=guard):
    from .record_content_review import item_catalogue
    from .observation_handoff import review as review_items
    from .observation_index import resolve_reference as resolve_items
    from .match_journal_index import resolve_reference as resolve_gsi
    from .match_journal_review import review as review_gsi
    from .item_review_evidence import prepare_observation
    from .equipment_continuity_evidence import prepare as prepare_continuity
    if not admitted['equipment'] and not admitted['gsi']:
        check()
        return {'equipment': [], 'gsi': [], 'catalogue_source': None}
    catalogue, source = item_catalogue(root)
    result = {'equipment': [], 'gsi': [], 'catalogue_source': source}
    for entry in admitted['gsi']:
        check()
        result['gsi'].append(review_gsi(resolve_gsi(entry, roots), expected_package_id=entry['package_id']))
    for entry in admitted['equipment']:
        check()
        base, session = resolve_items(entry, roots)
        review = review_items(base, session, expected_package_id=entry['package_id'],
            source_reference=entry, catalogue=catalogue, catalogue_source=source)
        folder = output / ('equipment-' + entry['package_id'])
        review['crops'] = prepare_observation(entry, roots, review['item_review_priorities'], folder / 'priority')
        check()
        continuity = prepare_continuity(entry, roots, catalogue, folder / 'continuity')
        review['continuity_page'] = (folder / 'continuity/index.html').relative_to(output).as_posix()
        review['continuity_source_verified'] = continuity['source_verified_before_and_after']
        result['equipment'].append(review)
    check()
    return result


def compare_replay(roots, admitted, archive, output, check=guard, root=ROOT):
    from .observation_index import resolve_reference
    from .observation_handoff import open_rows
    from .replay_economy_review import compare
    from tools.prepare_replay_item_audit import prepare
    from tools.review_replay_last_visible_inventory import analyze
    from .record_content_review import item_catalogue
    catalogue, source = item_catalogue(root)
    results = []
    for entry in admitted['equipment']:
        check()
        base, session = resolve_reference(entry, roots)
        with open_rows(base, session, expected_package_id=entry['package_id']) as (manifest, rows):
            for _ in rows:
                pass
            if manifest['payload'] is None or manifest['payload']['rows'] == 0:
                results.append({'package_id': entry['package_id'], 'status': 'no_equipment_observations'})
                continue
            observations = base / 'matches' / manifest['payload']['path']
            try:
                report = compare(observations, archive / 'hero_samples.jsonl', max_observations=2000)
            except ValueError as exc:
                if str(exc) != 'no_comparable_observations':
                    raise
                results.append({'package_id': entry['package_id'], 'status': 'no_comparable_observations'})
                continue
            target = output / ('comparison-' + entry['package_id'] + '.json')
            atomic(target, report)
            high = []
            for row in report['rows']:
                for slot in row.get('comparisons', []):
                    ref = slot.get('replay') or {}
                    item = (ref.get('item_key') or '').removeprefix('item_')
                    cost = catalogue.get(item, {}).get('cost')
                    if slot['comparison'] == 'recognition_unknown' and type(cost) is int and cost >= 2000:
                        high.append({'line': row['observation_line'], 'hero_id': row['hero_id'], 'slot': slot['slot'],
                            'game_time_s': row['game_time_s'], 'replay_item': item, 'catalogue_cost': cost,
                            'visibility': row.get('replay_visibility_raw')})
            details = {'package_id': entry['package_id'], 'status': 'compared', 'counts': report['counts'],
                'excluded': report['excluded'], 'multiset_counts': report['inventory_item_multiset_counts'],
                'high_value_unknown_observations': len(high), 'high_value_unknown_examples': high[:60],
                'high_value_examples_limited': len(high) > 60, 'catalogue_source': source,
                'comparison_file': target.name, 'last_visible_review': None}
            disputed = [r for r in report['rows'] if any(s['comparison'] in
                {'item_id_disagreement', 'recognized_empty_replay_occupied'} for s in r.get('comparisons', []))]
            if disputed:
                # Fixed first 60 source frames, not best matching items/times.
                bounded = output / ('disputed-' + entry['package_id'] + '.json')
                atomic(bounded, {**report, 'rows': disputed[:60]})
                case = prepare(bounded, observations, archive)
                for frame in case['frames']:
                    frame['classification'] = 'image_vs_omniscient_stable_unresolved'
                frames = output / ('windows-' + entry['package_id'] + '.json')
                atomic(frames, {'cases': [case], 'visual_reviews_complete': False,
                    'window_policy': 'fixed_plus_minus_3_seconds', 'limited': len(disputed) > 60})
                check()
                last = analyze(frames, [archive])
                last_path = output / ('last-visible-' + entry['package_id'] + '.json')
                atomic(last_path, last)
                details['last_visible_review'] = last_path.name
                details['disputed_frames_limited'] = len(disputed) > 60
                details['past_inventory_candidate_frames'] = sum(
                    f['last_prior_raw_visible_comparison'].get('all_9_slot_item_multisets_equal') is True
                    for c in last['cases'] for f in c['frames'])
            results.append(details)
        check()
    return results


def publish(output, report):
    """Publish only after all admitted source contexts have exited successfully."""
    output = Path(output)
    report.update(schema_version=1, generated_at_utc=utc(), accuracy_verified=False,
        visual_review_complete=False, meaning='automatic_evidence_review_not_ground_truth_or_live_correction')
    atomic(output / 'report.json', report)
    lines = [f'# 比赛 {report["match_id"]} · 自动经济复查', '', f'状态：{report["state_label"]}', '',
        '结果为记录和录像旁证，不是人工图像真值；未知不补零、不自动更改线上识别或本人GSI。', '']
    records = report.get('records', {})
    for row in records.get('gsi', []):
        audit = row['self_economy_audit']
        component = audit['component_checks']; display = audit['display_checks']
        lines += [f'本人GSI记录 {audit["rows"]} 行；分项可比 {component["comparable_rows"]}，不符 {component["mismatches"]}。',
            f'同包显示可比 {display["normal_comparable_rows"]}，不符 {display["amount_mismatches"]}；无显示记录时不认证UI金额。', '']
    if not records.get('gsi'):
        lines += ['本人GSI闭合包未到齐；未当作金额一致或零问题。', '']
    for row in records.get('equipment', []):
        lines += [f'装备记录 {row["rows"]} 行；格状态：{row["slot_status_counts"]}。',
            f'大件中断：{row["equipment_continuity"]["counts"]}。',
            f'[打开大件前后图片]({row["continuity_page"]})', '']
    if not records.get('equipment'):
        lines += ['装备闭合包未到齐；未当作识别通过。', '']
    for row in report.get('replay_comparisons', []):
        lines += [f'录像对照状态：{row["status"]}。', '']
        if row['status'] == 'compared':
            lines += [f'对照计数：{row["counts"]}；排除原因：{row["excluded"]}。',
                f'目录价≥2000的未知格候选 {row["high_value_unknown_observations"]} 条（连续观测，不是独立错误数）。',
                f'固定过去可见库存相容候选 {row.get("past_inventory_candidate_frames", 0)} 帧；不自动判成识别错误或通过。', '']
    if not report.get('archive') and not report.get('replay_available'):
        lines += ['缺同场录像；保留记录报告，下载器仍按原10分钟一次策略，手动录像到齐后自动补对照。', '']
    elif not report.get('archive'):
        lines += ['同场录像已存在，但来源/解析/对照未完成；记录报告保留，后台稍后重试。', '']
    lines += ['仍需确认：物品像素真值、敌方HUD缓存/合并眼语义、精确同tick与屏幕应用、完整净资产。']
    text = '\n'.join(lines)
    (output / 'REPORT.md').write_text(text, encoding='utf-8')
    # Simple local page with real links; JSON retains detailed provenance.
    html = ['<!doctype html><meta charset="utf-8"><title>自动经济复查</title>',
        '<style>body{background:#152027;color:#d7e2e6;font:16px system-ui;max-width:1000px;margin:32px auto}a{color:#9acdbb}pre{white-space:pre-wrap}</style>',
        '<pre>' + escape(text) + '</pre>']
    for row in records.get('equipment', []):
        html.append('<p><a href="' + escape(row['continuity_page'], quote=True) + '">查看大件前后图片</a></p>')
    (output / 'index.html').write_text(''.join(html), encoding='utf-8')
    return report


def run_task(task, *, root=ROOT, config=None, heartbeat=lambda _: None, check=guard):
    root = Path(root).resolve(); config = config or {}
    roots = history_roots(root / 'config/history.local.json')
    mid = task['match_id']; refuse_locked_match(mid); check()
    admitted = inputs(root, roots, mid)
    source = replay_path(task, config.get('replay_directories', []))
    signature = hashlib.sha256(json.dumps({'inputs': admitted, 'source':
        [str(source), source.stat().st_size, source.stat().st_mtime_ns] if source else None,
        'workflow_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'version': 1}, sort_keys=True).encode()).hexdigest()
    if signature == task.get('input_signature') and task.get('report'):
        return task
    output = checked_output(root, root / 'diagnostics/automatic-economy/matches' / mid / uuid.uuid4().hex)
    output.mkdir(parents=True)
    heartbeat('reviewing_records')
    records = record_review(root, roots, admitted, output, check)
    result = {'match_id': mid, 'source_packages': admitted, 'records': records, 'archive': None,
        'replay_comparisons': [], 'download_state': task.get('download_state'), 'replay_available': source is not None}
    if source is not None and admitted['equipment']:
        archive = output / 'archive'
        try:
            archive = reusable_archive(root, mid, source, task, config) or archive
            if archive == output / 'archive':
                parse_replay(root, source, archive, heartbeat, check)
            check(); heartbeat('comparing_equipment')
            result['replay_comparisons'] = compare_replay(roots, admitted, archive, output, check, root)
            result['archive'] = str(archive)
        except (ValueError, OSError, InterruptedError, TimeoutError) as exc:
            # The records stage is useful even when DEM parsing fails or a
            # new game interrupts it. Never lose it behind a replay dependency.
            result.update(state='replay_review_failed', state_label='记录已复查，录像复查待重试',
                replay_error=type(exc).__name__)
            publish(output, result)
            return {**task, 'state': 'replay_review_failed', 'error': type(exc).__name__,
                'report': str(output / 'index.html'), 'reviewed_at_utc': result['generated_at_utc']}
    complete = (bool(admitted['equipment']) and bool(admitted['gsi']) and bool(result['replay_comparisons'])
        and all(r['status'] == 'compared' and r['counts'].get('paired_observations', 0) > 0
                for r in result['replay_comparisons']))
    state = ('waiting_sources_or_comparable_records' if not admitted['equipment'] or not admitted['gsi']
             else 'review_complete_evidence_only' if complete else 'waiting_replay' if source is None
             else 'waiting_sources_or_comparable_records')
    result.update(state=state, state_label={'review_complete_evidence_only':'自动复查完成，图像真值待确认',
        'waiting_replay':'记录已复查，缺同场录像', 'waiting_sources_or_comparable_records':'闭合来源或可比记录未齐'}[state])
    check(); publish(output, result)
    return {**task, 'state': state, 'input_signature': signature, 'report': str(output / 'index.html'),
        'archive': result['archive'], 'reviewed_at_utc': result['generated_at_utc'], 'error': None}
