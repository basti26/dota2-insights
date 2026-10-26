"""Synchronize records, then prepare a bounded native/legacy minimap review."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare(root=ROOT, *, roots=None, latest=3, max_runs=3, max_images=12, selection='spread_plus_largest_capture_gap'):
    from dota2_insights.history import history_roots, checked_output
    from dota2_insights.closed_journals import read_package
    from dota2_insights.minimap_index import read_index
    from dota2_insights.minimap_review_evidence import prepare_native, prepare_legacy
    from tools.review_recordings import run
    if type(max_runs) is not int or not 1 <= max_runs <= 10:
        raise ValueError('minimap_review_run_limit_invalid')
    if type(max_images) is not int or not 2 <= max_images <= 24:
        raise ValueError('minimap_review_image_limit_invalid')
    if selection not in ('spread_plus_largest_capture_gap', 'continuous_windows'):
        raise ValueError('minimap_review_selection_invalid')
    root = Path(root).resolve()
    roots = roots if roots is not None else history_roots(root / 'config/history.local.json')
    pipeline = run(root, roots=roots, latest=latest)
    index = read_index(root / 'diagnostics/minimap-run-handoff/index.json')
    output = checked_output(root, root / 'diagnostics/minimap-image-review' /
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    results, seen = [], set()
    for entry in index['entries']:
        if entry['state'] != 'ready_with_gaps' or len(results) >= max_runs:
            continue
        prefix = PurePosixPath(entry['source']).parts[:-4]
        journal = PurePosixPath(*prefix, 'minimap-learning', 'run_' + entry['run_id'], 'samples.jsonl').as_posix()
        result = prepare_native(entry, roots, output / ('native-' + entry['package_id']), max_images=max_images, selection=selection)
        results.append({'directory': 'native-' + entry['package_id'], 'result': result})
        seen.add((entry['root_key'], journal))
    folder = root / 'diagnostics/helper-handoff'
    legacy = json.loads((folder / 'index.json').read_text(encoding='utf-8'))
    for entry in legacy['runs']:
        if entry['state'] not in ('ready', 'ready_with_gaps') or len(results) >= max_runs:
            continue
        package = read_package(folder / 'packages' / (entry['package_id'] + '.json'))
        if package['launcher_run_id'] != entry['launcher_run_id']:
            raise ValueError('minimap_review_legacy_index_identity_changed')
        for component in package['components']:
            if component['kind'] != 'minimap' or component['state'] != 'closed_verified':
                continue
            for journal in component.get('journals', []):
                ref = journal['source']
                key = ref['root_key'], ref['path']
                if PurePosixPath(ref['path']).name != 'samples.jsonl' or key in seen or len(results) >= max_runs:
                    continue
                directory = 'legacy-' + package['launcher_run_id'] + '-' + str(len(results))
                result = prepare_legacy(package, roots, ref, output / directory, max_images=max_images, selection=selection)
                results.append({'directory': directory, 'result': result})
                seen.add(key)
    output.mkdir(parents=True, exist_ok=True)
    summary = {'schema_version': 1, 'kind': 'minimap_image_review_batch', 'pipeline': pipeline,
        'runs': len(results), 'prepared': sum(e['status'] == 'prepared_for_visual_review' for r in results for e in r['result']['entries']),
        'unavailable': sum(e['status'] == 'unavailable' for r in results for e in r['result']['entries']),
        'source_rows': sum(r['result']['sample_rows'] for r in results),
        'results': results, 'match_association': 'unknown_no_time_inference', 'visual_verdicts_entered': False}
    (output / 'batch.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    lines = ['# 小地图抽样复核批次', '', '样本图像对应检查；不是英雄身份、地图标定或消失预测验收。', '']
    lines.extend(f"- [采样 {i + 1}]({entry['directory']}/REVIEW.md)" for i, entry in enumerate(results))
    if not results:
        lines.append('没有发现可供本批核验的已闭合小地图run；不代表没有采集。')
    (output / 'INDEX.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {key: summary[key] for key in ('runs', 'prepared', 'unavailable', 'source_rows')} | {'output': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', type=int, default=3)
    parser.add_argument('--max-runs', type=int, default=3)
    parser.add_argument('--max-images', type=int, default=12)
    parser.add_argument('--selection', choices=('spread_plus_largest_capture_gap', 'continuous_windows'),
                        default='spread_plus_largest_capture_gap')
    args = parser.parse_args()
    print(json.dumps(prepare(latest=args.latest, max_runs=args.max_runs, max_images=args.max_images, selection=args.selection), ensure_ascii=False))


if __name__ == '__main__':
    main()
