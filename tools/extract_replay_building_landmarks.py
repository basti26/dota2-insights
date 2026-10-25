"""Read a bounded replay prefix for building landmarks, without parsing a full match."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('source', type=Path)
    cli.add_argument('--match-id', required=True)
    cli.add_argument('--expected-sha256', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--max-tick', type=int, default=5000)
    cli.add_argument('--max-seconds', type=int, default=60)
    args = cli.parse_args()
    if args.output.exists() or not 1 <= args.max_tick <= 12000 or not 1 <= args.max_seconds <= 120:
        cli.error('new output and bounded tick/wall budgets required')
    sys.path[:0] = [str(ROOT), str(ROOT / '.replay-deps')]
    from dota2_insights.replay_archive import (GEM_VERSION, GEM_COMMIT, _footer_match_id,
                                             _windows_dota_running, sha256_file)
    if sys.platform == 'win32' and _windows_dota_running():
        cli.error('game_running_offline_parse_refused')
    if importlib.metadata.version('gem-dota') != GEM_VERSION:
        cli.error('unexpected_parser_version')
    source = args.source.resolve(strict=True)
    stat = source.stat()
    if not 0 < stat.st_size <= 512 * 1024**2:
        cli.error('source_size_budget')
    digest = sha256_file(source)
    mid = _footer_match_id(source)
    if digest != args.expected_sha256 or mid != args.match_id:
        cli.error('original_replay_hash_or_footer_mismatch')
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.SetPriorityClass.restype = wintypes.BOOL
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000):
            cli.error('below_normal_priority_failed')
    else:
        os.nice(10)
    from gem.parser import ReplayParser
    import gem.extractors._snapshots as snapshots
    from dota2_insights.replay_building_landmarks import Collector, SCHEMA, LandmarkLimit
    parser = ReplayParser(source)
    collector = Collector(parser)
    began = time.monotonic()
    last_game_check = began

    def guard(*_args):
        nonlocal last_game_check
        if time.monotonic() - began > args.max_seconds:
            raise LandmarkLimit('landmark_wall_time_limit')
        if sys.platform == 'win32' and time.monotonic() - last_game_check >= 2:
            last_game_check = time.monotonic()
            if _windows_dota_running():
                raise LandmarkLimit('game_started_offline_parse_stopped')

    parser._on_packet_end(guard)
    parser.stop_after_tick(args.max_tick)
    stop = 'prefix_tick_limit_or_eof'
    try:
        parser.parse()
    except LandmarkLimit as exc:
        stop = str(exc)
    final_stat = source.stat()
    after = sha256_file(source)
    unchanged = digest == after and (stat.st_size, stat.st_mtime_ns) == (final_stat.st_size, final_stat.st_mtime_ns)
    report = {'schema_version': SCHEMA, 'match_id': mid, 'source_sha256': digest,
              'source_sha256_after': after, 'source_unchanged': unchanged,
              'source_bytes': stat.st_size, 'max_tick': args.max_tick,
              'max_seconds': args.max_seconds, 'last_tick': parser.tick,
              'elapsed_seconds': time.monotonic() - began, 'stop_reason': stop,
              'parse_error_type': type(parser.parse_error).__name__ if parser.parse_error else None,
              'truncated_at_tick': parser.truncated_at_tick,
              'parser_version': GEM_VERSION, 'parser_commit': GEM_COMMIT,
              'snapshot_helper_sha256': sha256_file(Path(snapshots.__file__)),
              'collector_sha256': sha256_file(ROOT / 'dota2_insights/replay_building_landmarks.py'),
              'adapter_sha256': sha256_file(Path(__file__)),
              'game_clock': asdict(parser.game_clock),
              'observed_classes': sorted(collector.classes),
              'deleted_callbacks': collector.deleted, 'records': collector.rows,
              'limits': ['Prefix observations only, not all buildings or complete replay events.',
                         'Entity class name is not a unique building; retain index and serial.',
                         'Coordinates apply only to this source, no transfer to other builds or matches.',
                         'No combat-log death is automatically joined to an entity or position.',
                         'Offline omniscient evidence never enters the live assistant.']}
    if not unchanged:
        raise ValueError('source_changed_no_landmark_output')
    raw = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
    if len(raw) > 1024**2:
        raise ValueError('landmark_output_budget')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as f:
        f.write(raw)
    print(json.dumps({'status': 'prefix_observations_saved', 'records': len(collector.rows),
                      'stop_reason': stop, 'source_sha256': digest,
                      'output_sha256': hashlib.sha256(raw).hexdigest()}))


if __name__ == '__main__':
    main()
