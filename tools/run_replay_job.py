"""Explicit local PC parsing job; keeps the existing pinned Gem adapter."""
import argparse
from pathlib import Path
import sys

ROOT = (Path(sys.executable).resolve().parents[2] if getattr(sys, 'frozen', False)
        else Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))


def run(source, job):
    from dota2_insights.replay_workbench import source_identity, game_running, export_event_timeline, save_json
    source, mid = source_identity(source)
    job = Path(job).resolve(strict=True)
    if job.parent != (ROOT / 'diagnostics/replay-workbench').resolve():
        raise ValueError('job_directory_outside_workbench')
    cancel = job / 'cancel.request'

    def guard():
        if cancel.exists():
            raise ValueError('cancel_requested')
        if game_running():
            raise ValueError('game_running')

    guard()
    if getattr(sys, 'frozen', False):
        from dota2_insights.replay_runtime import read_runtime_manifest
        read_runtime_manifest(Path(sys.executable).parent, verify_files=True)
    else:
        deps = ROOT / '.replay-deps'
        if not (deps / 'gem').is_dir():
            raise ValueError('isolated_replay_dependencies_missing')
        sys.path.insert(0, str(deps))
    from dota2_insights.replay_archive import _footer_match_id, parse_archive, ArchiveOptions
    # Independent footer identity is checked before full-replay hashing/parsing.
    from dota2_insights.replay_holdout import refuse_locked_match
    actual = _footer_match_id(source)
    refuse_locked_match(actual)
    if actual != mid:
        raise ValueError('declared_match_differs_from_replay_footer')
    manifest = parse_archive(source, job / 'archive', ArchiveOptions(
        sample_hz=1, start_game_time=-120, end_game_time=10800, max_seconds=600,
        max_output_mib=256, cancel_file=str(cancel)))
    complete = (manifest.get('export_complete') is True and manifest.get('stop_reason') == 'replay_eof'
                and manifest.get('parse_error') is None and manifest.get('match_id') == mid)
    result = {'schema_version': 1, 'match_id': mid, 'complete': complete,
              'stop_reason': manifest['stop_reason'], 'timeline': None,
              'fact_review': None,
              'source_sha256': manifest['source_sha256']}
    if complete:
        result['timeline'] = export_event_timeline(job / 'archive', job / 'timeline.jsonl', guard=guard)
        from dota2_insights.replay_fact_review import review_archive
        result['fact_review'] = review_archive(job / 'archive', job / 'fact-review', guard=guard)
    guard()
    save_json(job / 'result.json', result)
    (job / '说明.txt').write_text(
        '本场解析完成。打开 fact-review/report.html 查看事实覆盖、结束区间及死亡事件口径。\n'
        if complete else '本场解析不完整，请查看result.json原因。\n', encoding='utf-8')
    return 0 if complete else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--job', type=Path)
    parser.add_argument('--offline-self-check', type=Path)
    args = parser.parse_args()
    try:
        if args.offline_self_check:
            if args.source or args.job:
                parser.error('self-check does not accept source/job')
            return self_check(args.offline_self_check)
        if args.source is None or args.job is None:
            parser.error('--source and --job are required')
        return run(args.source, args.job)
    except (OSError, ValueError, ImportError) as exc:
        print('Replay job failed: ' + type(exc).__name__ + ': ' + str(exc)[:300], flush=True)
        return 1


def self_check(output):
    import importlib.metadata
    import json
    from dota2_insights.replay_archive import GEM_VERSION, GEM_COMMIT
    from dota2_insights.replay_runtime import read_runtime_manifest
    if output.exists():
        raise ValueError('self_check_output_exists')
    if getattr(sys, 'frozen', False):
        runtime = read_runtime_manifest(Path(sys.executable).parent, verify_files=True)
        from dota2_insights.portable_replay import demo_file_info
        demo_file_info()
    else:
        sys.path.insert(0, str(ROOT / '.replay-deps'))
        runtime = None
    from gem.parser import ReplayParser
    from gem.catalog.heroes import HEROES
    from gem.catalog.items import ITEMS
    from gem.proto.dota_modifiers_pb2 import CDOTAModifierBuffTableEntry
    from gem.proto.dota_shared_enums_pb2 import (
        DOTA_GAMERULES_STATE_GAME_IN_PROGRESS, DOTA_GAMERULES_STATE_POST_GAME)
    from dota2_insights.replay_phase_evidence import IN_PROGRESS, POST_GAME
    import snappy, cramjam, zstandard
    if importlib.metadata.version('gem-dota') != GEM_VERSION:
        raise ValueError('gem_version_mismatch')
    if (DOTA_GAMERULES_STATE_GAME_IN_PROGRESS, DOTA_GAMERULES_STATE_POST_GAME) != (IN_PROGRESS, POST_GAME):
        raise ValueError('pinned_game_state_enum_contract_mismatch')
    # Codec and generated-message calls exercise compiled extensions, not
    # merely successful file collection. No DEM, game, or network is used.
    fixture = b'portable replay codec fixture' * 10
    if snappy.uncompress(snappy.compress(fixture)) != fixture:
        raise ValueError('snappy_roundtrip')
    if zstandard.ZstdDecompressor().decompress(zstandard.ZstdCompressor().compress(fixture)) != fixture:
        raise ValueError('zstd_roundtrip')
    CDOTAModifierBuffTableEntry().SerializeToString()
    result = {'kind': 'portable_replay_worker_offline_check', 'passed': True,
        'gem_version': GEM_VERSION, 'gem_commit': GEM_COMMIT, 'heroes': len(HEROES), 'items': len(ITEMS),
        'game_state_enums': {'in_progress': IN_PROGRESS, 'post_game': POST_GAME},
        'runtime_files_verified': len(runtime['files']) if runtime else None,
        'game_started': False, 'capture_started': False, 'network_used': False, 'dem_parsed': False}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
