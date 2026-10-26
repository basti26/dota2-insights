"""Explicit game/session QPC windows over verified closed PresentMon sources."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from .closed_journals import ClosedJournals
from .match_journals import _read, _json
from .presentmon_metrics import read_presentmon_csv

VERSION = '2.6.0'
BINARY_SHA256 = 'b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af'
CONFIGURATION_FIELDS = ('ocr_runtime', 'draft_ocr_runtime', 'cpu_scheduling')


class ContextDecoder:
    def __init__(self):
        self.known = {}

    def decode(self, row):
        if 'configuration_ref' not in row:
            return row
        reference = row['configuration_ref']
        if not isinstance(reference, str) or not re.fullmatch('[a-f0-9]{64}', reference):
            raise ValueError('performance_configuration_reference_invalid')
        present = set(row).intersection(CONFIGURATION_FIELDS)
        if present:
            if len(present) != len(CONFIGURATION_FIELDS):
                raise ValueError('performance_configuration_partial')
            configuration = {key: row[key] for key in CONFIGURATION_FIELDS}
            raw = json.dumps(configuration, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            if hashlib.sha256(raw).hexdigest() != reference or reference in self.known or len(self.known) >= 128:
                raise ValueError('performance_configuration_checksum_or_redefinition')
            self.known[reference] = deepcopy(configuration)
        elif reference not in self.known:
            raise ValueError('performance_configuration_unknown')
        return {**{key:value for key,value in row.items() if key != 'configuration_ref'}, **deepcopy(self.known[reference])}


def game_windows(contexts, *, match_id, game_session_id, record_session_id, frequency):
    if (type(frequency) is not int or not 0 < frequency <= 10**12
            or not re.fullmatch(r'game_[a-f0-9]{32}', str(game_session_id))
            or not re.fullmatch(r'run_[a-f0-9]{32}', str(record_session_id))):
        raise ValueError('performance_game_scope_invalid')
    result, previous = [], None
    for row in contexts:
        age, monotonic, ticks = row.get('game_provider_age_s'), row.get('monotonic_s'), row.get('qpc_ticks')
        valid = (row.get('match_id') == match_id and row.get('game_session_id') == game_session_id
            and row.get('record_session_id') == record_session_id
            and row.get('game_state') == 'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS' and row.get('foreground_dota') is True
            and type(age) in (int,float) and math.isfinite(age) and 0 <= age <= 5
            and type(monotonic) in (int,float) and math.isfinite(monotonic) and 0 <= monotonic <= 2**53
            and type(ticks) is int and 0 < ticks < 2**63)
        if not valid:
            previous = None
            continue
        if previous is not None:
            first, last = previous['qpc_ticks'], ticks
            elapsed = monotonic - previous['monotonic_s']
            if last <= first or elapsed <= 0:
                raise ValueError('performance_nonmonotonic_context')
            if elapsed <= 2.5 and abs((last - first) / frequency - elapsed) <= .25:
                if result and result[-1][1] == first:
                    result[-1][1] = last
                else:
                    result.append([first,last])
        previous = row
    return result


def _source(root, ref):
    root = Path(root).resolve()
    path = root / ref['path']
    if not path.resolve().is_relative_to(root):
        raise ValueError('performance_source_outside_history')
    for parent in (path, *path.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('performance_source_link_refused')
    return path


def review(package, roots):
    results, unavailable = [], []
    with ClosedJournals(package, roots) as reader:
        for component in package['components']:
            if component['kind'] != 'fps':
                continue
            if component['state'] != 'closed_verified':
                unavailable.append({'reason':'fps_component_not_closed_verified'})
                continue
            metadata = component['metadata']
            if metadata not in package['sources']:
                raise ValueError('performance_metadata_not_admitted')
            path = _source(roots[metadata['root_key']], metadata)
            raw = _read(path, 32768)
            if hashlib.sha256(raw).hexdigest() != metadata['sha256']:
                raise ValueError('performance_metadata_changed')
            manifest = _json(raw)
            run_id = manifest.get('run_id')
            partial = (manifest.get('complete') is False and manifest.get('ended_reason') in
                ('context_byte_limit','byte_limit','time_limit','disk_space_limit'))
            if (not re.fullmatch('[a-f0-9]{32}', str(run_id)) or path.parent.name != 'run_' + run_id
                    or manifest.get('presentmon_version') != VERSION or manifest.get('binary_sha256') != BINARY_SHA256
                    or manifest.get('metric_mode') != 'v1' or manifest.get('clock') != 'raw_QPC_not_UTC'
                    or manifest.get('source_kind') != 'windows' or manifest.get('application_filter') != 'dota2.exe'
                    or not (manifest.get('complete') is True or partial)
                    or manifest.get('shutdown_complete') is not True or manifest.get('error') is not None
                    or manifest.get('etw_loss_warning') is not False or type(manifest.get('exitcode')) is not int
                    or manifest['exitcode'] != 0 or manifest.get('context_format') not in (None,'configuration_refs_v1')):
                raise ValueError('performance_capture_not_verified')
            refs = {Path(j['source']['path']).name:j['source'] for j in component['journals']}
            if len(refs) != len(component['journals']) or not {'frames.csv','context.jsonl'} <= refs.keys():
                unavailable.append({'reason':'fps_csv_or_context_missing'})
                continue
            csv_ref, context_ref = refs['frames.csv'], refs['context.jsonl']
            if (csv_ref not in package['sources'] or context_ref not in package['sources']
                    or any(ref['root_key'] != metadata['root_key'] or Path(ref['path']).parent != Path(metadata['path']).parent
                           for ref in (csv_ref, context_ref))):
                raise ValueError('performance_component_sources_conflict')
            decoder, contexts = ContextDecoder(), []
            for _, row, _ in reader.rows(context_ref):
                if row.get('run_id') != run_id or len(contexts) >= 16384:
                    raise ValueError('performance_context_identity_or_limit')
                contexts.append(decoder.decode(row))
            for match in package['matches']:
                mid, game, session = match['match_id'], match['game_session_id'], match['record_session_id']
                frequency = manifest.get('qpc_frequency_hz')
                windows = game_windows(contexts, match_id=mid, game_session_id=game, record_session_id=session, frequency=frequency)
                result = {'match_id':mid, 'game_session_id':game, 'record_session_id':session,
                    'capture_run_id':run_id, 'csv_source':csv_ref, 'context_source':context_ref,
                    'manifest_sha256':metadata['sha256'], 'capture_complete':manifest['complete'],
                    'capture_ended_reason':manifest.get('ended_reason'), 'qpc_frequency_hz':frequency,
                    'qpc_windows':windows, 'covered_game_seconds':sum((last-first)/frequency for first,last in windows),
                    'coverage':'recorded_adjacent_fresh_in_game_foreground_QPC_windows_only',
                    'streams':None, 'status':'available_partial_capture' if partial else 'available',
                    'assistant_causality_verified':False, 'whole_match_coverage_verified':False}
                if not windows:
                    result['status'] = 'insufficient_game_context'
                else:
                    parsed = read_presentmon_csv(_source(roots[csv_ref['root_key']],csv_ref),
                        qpc_windows=windows, qpc_frequency_hz=frequency)
                    if parsed['source_sha256'] != csv_ref['sha256'] or parsed['source_bytes'] != csv_ref['bytes']:
                        raise ValueError('performance_csv_source_changed')
                    result['streams'] = parsed
                results.append(result)
    return {'launcher_run_id':package['launcher_run_id'], 'matches':results, 'unavailable':unavailable,
            'source_verified_before_and_after':True}


def render_markdown(reports):
    lines = ['# 对局性能复核', '', '只使用明确比赛/会话及原生QPC对局前台窗口；提交帧与显示帧分别统计，不合并不同swapchain。',
        '不是助手开/关因果实验，不把GPU毫秒当利用率，不把采样窗口当全局覆盖。', '',
        '| 比赛 | 状态 | 覆盖秒 | 流 | 提交FPS | 显示FPS | 显示1% low | 显示P99毫秒 | 显示>100ms间隔 |',
        '|---|---|---:|---|---:|---:|---:|---:|---:|']
    def value(doc, key):
        return '未知' if not isinstance(doc,dict) or doc.get(key) is None else str(doc[key])
    for report in reports:
        for game in report['matches']:
            streams = (game.get('streams') or {}).get('streams') or [None]
            for stream in streams:
                shown, submitted = (stream or {}).get('displayed_frames'), (stream or {}).get('submitted_frames')
                label = '未知' if stream is None else f"{stream['process_id']}/{stream['swap_chain']}"
                lines.append(f"| {game['match_id']} | {game['status']} | {game['covered_game_seconds']:.1f} | {label} | "
                    f"{value(submitted,'average_fps')} | {value(shown,'average_fps')} | {value(shown,'one_percent_low_fps')} | "
                    f"{value(shown,'p99_frame_ms')} | {value(shown,'intervals_over_100_ms')} |")
    return '\n'.join(lines) + '\n'
