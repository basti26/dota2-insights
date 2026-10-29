"""Offline ward pass with an optional bounded postgame context sidecar."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(os.environ.get('DOTA2_REPLAY_RUNTIME',Path(__file__).resolve().parents[1]))


def terminal_status(stop, error, parser_error):
    if parser_error is not None and stop == 'replay_eof':
        return 'parser_stream_error', error or type(parser_error).__name__
    return stop, error


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('source', type=Path)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--max-seconds', type=float, default=180)
    cli.add_argument('--with-context', action='store_true',help='Collect bounded events/counters in this same pass.')
    args = cli.parse_args()
    limit=21600 if sys.platform=='linux' else 600
    if args.output.exists() or not 1 <= args.max_seconds <= limit:
        cli.error(f'Use a new output directory and a wall limit of 1..{limit} seconds.')
    sys.path.insert(0, str(ROOT / '.replay-deps'))
    sys.path.insert(0, str(ROOT))
    from dota2_insights.minimap.offline_io import check_input_paths
    from dota2_insights.replay_holdout import refuse_locked_match
    check_input_paths(args.source,args.output)
    from dota2_insights.replay_archive import (
        GEM_VERSION, GEM_COMMIT, _footer_match_id, _windows_dota_running, sha256_file,
    )
    if importlib.metadata.version('gem-dota') != GEM_VERSION:
        cli.error('Unexpected parser version.')
    source = args.source.resolve(strict=True)
    source_stat = source.stat()
    mid = _footer_match_id(source)
    if not mid:
        cli.error('Missing independent replay match identity.')
    refuse_locked_match(mid)
    if sys.platform == 'win32' and _windows_dota_running():
        cli.error('Dota is running; offline parse refused.')
    priority = False
    if sys.platform=='linux':
        os.nice(5)
        priority=True
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        priority = bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
    from gem.parser import ReplayParser
    from gem.extractors.wards import WardsExtractor
    import gem.extractors.wards as wards_module
    import gem.parser as parser_module
    source_sha = sha256_file(source)
    args.output.mkdir(parents=True)
    parser = ReplayParser(source)
    from gem.errors import ReplayDataError
    from dota2_insights import replay_packet_diagnostics
    failure_packets = replay_packet_diagnostics.attach(parser, ReplayDataError)
    extractor = WardsExtractor()
    extractor.attach(parser)
    context_collector=None
    from dota2_insights.replay_postgame_context import Collector,ContextLimit,write_context
    if args.with_context:
        context_collector=Collector(mid)
        context_collector.attach(parser)
    began, checked = time.monotonic(), time.monotonic()
    stop, error = 'replay_eof', None

    class BoundedStop(Exception):
        pass

    def guard(*_args):
        nonlocal checked
        now = time.monotonic()
        if now - began > args.max_seconds:
            raise BoundedStop('wall_time_limit')
        if len(extractor.ward_events) > 5000:
            raise BoundedStop('ward_count_limit')
        if now - checked >= 5:
            checked = now
            if sys.platform=='linux':
                progress={'tick':parser.tick,'elapsed_seconds':round(now-began,1),
                          'ward_count':len(extractor.ward_events)}
                temporary=args.output/'progress.tmp'
                temporary.write_text(json.dumps(progress),encoding='utf-8')
                temporary.replace(args.output/'progress.json')
            if sys.platform == 'win32' and _windows_dota_running():
                raise BoundedStop('game_started')

    parser._on_packet_end(guard)
    try:
        parser.parse()
    except BoundedStop as exc:
        stop = str(exc)
    except ContextLimit:
        stop = 'postgame_context_limit'
    except Exception as exc:
        stop, error = 'parser_or_adapter_error', type(exc).__name__
    stop, error = terminal_status(stop, error, parser.parse_error)
    wards = extractor.finalize()
    clock = parser.game_clock
    match_end_tick = parser.post_game_tick
    match_end = clock.game_time_at(match_end_tick) if match_end_tick is not None else None
    rows = []
    for ward in wards:
        row = asdict(ward)
        if row.get('placer') and not row['placer'].startswith('npc_dota_hero_'):
            row['placer'] = None
        if row.get('killer') and not row['killer'].startswith(('npc_dota_', 'dota_')):
            row['killer'] = None
        # Unrestricted combat-log strings are not exported.
        row.pop('left_attacker', None)
        placed = clock.game_time_at(ward.tick)
        end_tick = ward.killed_tick if ward.killed_tick is not None else ward.expires_tick
        ended = clock.game_time_at(end_tick) if end_tick is not None else None
        row.update(match_id=mid, placed_game_time_s=placed, ended_game_time_s=ended,
            end_kind='killed' if ward.killed_tick is not None else 'expired' if ward.expires_tick is not None else 'unobserved',
            coordinate_basis='gem_cell128_plus_vec_raw_coordinates',
            duration_s=ended-placed if ended is not None and placed is not None else None,
            after_match_end=match_end is not None and placed is not None and placed > match_end,
            offline_only=True)
        rows.append(row)
    output = args.output / 'wards.json'
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    parse_error = type(parser.parse_error).__name__ if parser.parse_error else error
    unchanged = (source_stat.st_size, source_stat.st_mtime_ns) == (source.stat().st_size, source.stat().st_mtime_ns)
    identity_ok = str(parser.match_id) == mid if parser.match_id else False
    manifest = {'schema_version': 1, 'kind': 'offline_ward_only_export', 'match_id': mid,
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'source_file': source.name,
        'source_sha256': source_sha, 'source_unchanged_stat': unchanged, 'parsed_match_id': str(parser.match_id),
        'independent_footer_matches_parser': identity_ok, 'parser_version': GEM_VERSION, 'parser_commit': GEM_COMMIT,
        'parser_sha256': sha256_file(Path(parser_module.__file__)),
        'ward_extractor_sha256': sha256_file(Path(wards_module.__file__)),
        'adapter_sha256': sha256_file(Path(__file__)), 'stop_reason': stop, 'parse_error': parse_error,
        'failure_packet_diagnostics': failure_packets,
        'packet_diagnostics_sha256': sha256_file(Path(replay_packet_diagnostics.__file__)),
        'last_tick': parser.tick, 'match_end_game_time_s': match_end, 'game_clock': asdict(clock),
        'elapsed_seconds': round(time.monotonic()-began, 3), 'below_normal_priority': priority,
        'ward_count': len(rows), 'counts_by_team_type': dict(Counter(f"{r['team']}:{r['ward_type']}" for r in rows)),
        'complete': stop == 'replay_eof' and not parse_error and unchanged and identity_ok,
        'files': {'wards.json': {'bytes': output.stat().st_size, 'sha256': sha256_file(output)}},
        'limitations': ['Offline omniscient placements are not real-time player-visible knowledge.',
            'No terrain-aware vision polygon, map calibration, or ward effectiveness is inferred.',
            'Expiry/kill classification follows the pinned extractor, including its lifespan assumptions.',
            'Unobserved ends remain unknown; replay end is right-censoring, not a ward death.',
            'No player account IDs, names, chat or unrestricted entity dump is exported.']}
    if context_collector is not None:
        context=context_collector.finish(parser,complete=manifest['complete'])
        context['source_sha256']=source_sha
        context['producer_sha256']=sha256_file(Path(sys.modules[Collector.__module__].__file__))
        manifest['files']['postgame_context.json']=write_context(args.output,context)
        manifest['postgame_context']={'schema_version':context['schema_version'],'complete':context['complete'],
            'producer_sha256':context['producer_sha256'],'coverage':context['coverage']}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({k: manifest[k] for k in ('match_id', 'complete', 'ward_count', 'counts_by_team_type', 'elapsed_seconds', 'stop_reason', 'parse_error')}))
    return 0 if manifest['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
