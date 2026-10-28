"""Independent reader for one stopped, writer-sealed PresentMon capture."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re

from .helper_handoff import utc
from .match_journals import _json, _read
from .performance_review import BINARY_SHA256, VERSION, ContextDecoder, game_windows
from .presentmon_metrics import MAX_CSV_BYTES, read_presentmon_csv
from .replay_holdout import refuse_locked_match

LIMITS = {'capture.json': 65536, 'frames.csv': MAX_CSV_BYTES,
          'context.jsonl': 32*1024**2, 'collector.log': 1024**2+65536}
FIELDS = {'match_id', 'game_session_id', 'record_session_id'}


def safe(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('performance_match_link_refused')
    return path


def digest(path, limit):
    path = safe(path)
    before = path.stat()
    if before.st_size > limit:
        raise ValueError('performance_match_source_budget')
    checksum, size = hashlib.sha256(), 0
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024**2), b''):
            size += len(block)
            if size > limit:
                raise ValueError('performance_match_source_budget')
            checksum.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or size != before.st_size:
        raise ValueError('performance_match_source_changed')
    return {'bytes': size, 'sha256': checksum.hexdigest()}


def verify(folder, expected_package_id=None, *, source_budget=None):
    folder = safe(folder)
    if folder.name != 'closed-match' or not re.fullmatch(r'run_[a-f0-9]{32}', folder.parent.name):
        raise ValueError('performance_match_folder_invalid')
    raw = _read(folder/'record-handoff.json', 32768)
    document = _json(raw)
    bound = document.get('scope')
    if not isinstance(bound, dict) or set(bound) != FIELDS:
        raise ValueError('performance_match_scope_invalid')
    mid = bound['match_id']
    if mid is not None:
        if not isinstance(mid, str) or not re.fullmatch(r'[1-9][0-9]{0,31}', mid):
            raise ValueError('performance_match_id_invalid')
        refuse_locked_match(mid)  # Identity gate before opening CSV/context/log.
    for key, prefix in (('game_session_id', 'game_'), ('record_session_id', 'run_')):
        if not re.fullmatch(prefix+r'[a-f0-9]{32}', str(bound[key])):
            raise ValueError('performance_match_session_invalid')
    package_id = hashlib.sha256(raw).hexdigest()
    if expected_package_id is not None and package_id != expected_package_id:
        raise ValueError('performance_match_package_changed')
    if _read(folder/'COMPLETE', 66) != (package_id+'\n').encode('ascii'):
        raise ValueError('performance_match_incomplete')
    if (type(document.get('schema_version')) is not int or document['schema_version'] != 1
            or document.get('kind') != 'foresight_performance_match_handoff'
            or document.get('capture_run_id') != folder.parent.name[4:]
            or document.get('end_state') != 'DOTA_GAMERULES_STATE_POST_GAME'
            or document.get('collector_closed_before_publication') is not True
            or document.get('coverage') != 'recorded_capture_only_not_whole_match'
            or document.get('gameplay_metrics') != 'require_fresh_foreground_QPC_windows_from_context'):
        raise ValueError('performance_match_contract_invalid')
    utc(document.get('closed_at_utc'))
    utc(document.get('end_provider_at_utc'))
    sources = document.get('sources')
    if not isinstance(sources, dict) or set(sources) != set(LIMITS):
        raise ValueError('performance_match_sources_invalid')
    for name, ref in sources.items():
        if (not isinstance(ref, dict) or set(ref) != {'bytes', 'sha256'}
                or type(ref.get('bytes')) is not int or not 0 <= ref['bytes'] <= LIMITS[name]
                or not re.fullmatch(r'[a-f0-9]{64}', str(ref.get('sha256')))):
            raise ValueError('performance_match_source_spec_invalid')
    total_bytes = sum(ref['bytes'] for ref in sources.values())
    if source_budget is not None and (type(source_budget) is not int or total_bytes > source_budget):
        raise ValueError('performance_match_source_budget')
    for name, ref in sources.items():
        if digest(folder.parent/name, min(LIMITS[name], ref['bytes'])) != ref:
            raise ValueError('performance_match_source_changed')
    capture_raw = _read(folder.parent/'capture.json', LIMITS['capture.json'])
    if hashlib.sha256(capture_raw).hexdigest() != sources['capture.json']['sha256']:
        raise ValueError('performance_match_capture_changed')
    capture = _json(capture_raw)
    if (type(capture.get('schema_version')) is not int or capture['schema_version'] != 1
            or capture.get('kind') != 'presentmon_capture'
            or capture.get('run_id') != document['capture_run_id']
            or capture.get('source_kind') != 'windows' or capture.get('complete') is not True
            or capture.get('shutdown_complete') is not True or capture.get('error') is not None
            or type(capture.get('exitcode')) is not int or capture['exitcode'] != 0
            or capture.get('etw_loss_warning') is not False
            or capture.get('presentmon_version') != VERSION or capture.get('binary_sha256') != BINARY_SHA256
            or capture.get('metric_mode') != 'v1' or capture.get('clock') != 'raw_QPC_not_UTC'
            or capture.get('application_filter') != 'dota2.exe'
            or capture.get('context_format') not in (None, 'configuration_refs_v1')):
        raise ValueError('performance_match_capture_not_verified')
    diagnostic = _read(folder.parent/'collector.log', LIMITS['collector.log']).decode('utf-8', errors='replace')
    if ('failed to start trace session: access denied' in diagnostic.casefold()
            or re.search(r'(?:[1-9][0-9]* ETW (?:buffers|events) were lost|[1-9][0-9]* overflowed present events)', diagnostic)):
        raise ValueError('performance_match_collector_diagnostic_failure')
    return {'package_id': package_id, 'document': document, 'capture': capture, 'folder': folder}


@contextmanager
def open_package(folder, expected_package_id=None, *, source_budget=None):
    package = verify(folder, expected_package_id, source_budget=source_budget)
    yield package
    after = verify(folder, package['package_id'], source_budget=source_budget)
    if after['document'] != package['document'] or after['capture'] != package['capture']:
        raise ValueError('performance_match_changed_during_read')


def review(folder, expected_package_id=None, *, source_budget=None):
    with open_package(folder, expected_package_id, source_budget=source_budget) as package:
        document, capture = package['document'], package['capture']
        bound, contexts, decoder = document['scope'], [], ContextDecoder()
        with safe(package['folder'].parent/'context.jsonl').open('rb') as handle:
            total = 0
            while line := handle.readline(1024**2+1):
                total += len(line)
                if len(line) > 1024**2 or not line.endswith(b'\n') or total > LIMITS['context.jsonl'] or len(contexts) >= 16384:
                    raise ValueError('performance_match_context_budget_or_tail')
                row = _json(line)
                if row.get('run_id') != document['capture_run_id']:
                    raise ValueError('performance_match_context_run_conflict')
                if row.get('match_id') is not None:
                    refuse_locked_match(row['match_id'])
                contexts.append(decoder.decode(row))
        frequency = capture.get('qpc_frequency_hz')
        windows = game_windows(contexts, **bound, frequency=frequency)
        result = {**bound, 'capture_run_id': document['capture_run_id'],
            'package_id': package['package_id'], 'source_kind': 'native_performance_match',
            'qpc_frequency_hz': frequency, 'qpc_windows': windows,
            'covered_game_seconds': sum((b-a)/frequency for a,b in windows),
            'coverage': 'recorded_adjacent_fresh_in_game_foreground_QPC_windows_only',
            'streams': None, 'status': 'insufficient_game_context',
            'assistant_causality_verified': False, 'whole_match_coverage_verified': False}
        if windows:
            parsed = read_presentmon_csv(package['folder'].parent/'frames.csv',
                                        qpc_windows=windows, qpc_frequency_hz=frequency)
            ref = document['sources']['frames.csv']
            if parsed['source_sha256'] != ref['sha256'] or parsed['source_bytes'] != ref['bytes']:
                raise ValueError('performance_match_csv_changed')
            result.update(streams=parsed, status='available')
    return {'capture_run_id': document['capture_run_id'], 'matches': [result],
            'source_files': document['sources'],
            'unavailable': [], 'source_verified_before_and_after': True,
            'observation_shutdown_required': False}


def render_markdown(reports, skipped, *, index_state):
    from .performance_review import render_markdown as render_metrics
    text = '\n'+render_metrics(reports, title='逐局性能复核')
    text += '\n新旧格式分别列出，重合记录不可相加；来源身份/SHA一致不证明整局覆盖或助手造成掉帧。\n'
    if index_state == 'missing_not_treated_as_zero':
        text += '\n逐局性能索引缺失：未知，不作为零FPS。\n'
    elif not reports and not skipped:
        text += '\n本次索引没有逐局性能包；不代表没有游戏帧。\n'
    for report in reports:
        text += f"\n采集 {report['capture_run_id']}："+('与旧闭合报告重合，不计为新增采样。' if report['also_in_closed_launcher_scope'] else '已复验独立逐局包。')+'\n'
    if skipped:
        text += f'\n{len(skipped)}份逐局性能包不可用，不作为零FPS；原因见JSON。\n'
    return text
