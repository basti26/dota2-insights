"""Sync closed records and review explicit game/session PresentMon windows offline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(root=ROOT, *, roots=None, latest=3):
    from dota2_insights.history import history_roots, checked_output
    from dota2_insights.helper_handoff import sync
    from dota2_insights.closed_journals import read_package
    from dota2_insights.performance_review import review, render_markdown
    from dota2_insights import performance_review, presentmon_metrics
    root = Path(root).resolve()
    roots = roots if roots is not None else history_roots(root / 'config/history.local.json')
    index = sync(root, roots, latest=latest)
    reports, skipped = [], []
    for entry in index['runs']:
        if entry['state'] not in ('ready','ready_with_gaps'):
            skipped.append({'state':entry['state'], 'reason':'closed_package_not_ready'})
            continue
        package = read_package(root / 'diagnostics/helper-handoff/packages' / (entry['package_id'] + '.json'))
        if package['launcher_run_id'] != entry['launcher_run_id']:
            raise ValueError('performance_index_identity_changed')
        result = review(package, roots)
        result['package_id'] = entry['package_id']
        reports.append(result)
    output = checked_output(root, root / 'diagnostics/performance-review' /
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    result = {'schema_version':1, 'kind':'closed_performance_review', 'reports':reports, 'skipped':skipped,
        'source_hashes':{Path(module.__file__).name:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                         for module in (performance_review,presentmon_metrics)},
        'assistant_causality_verified':False, 'whole_match_coverage_verified':False}
    (output / 'REPORT.md').write_text(render_markdown(reports), encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'output':str(output), 'launcher_runs':len(reports),
        'game_scopes':sum(len(r['matches']) for r in reports), 'skipped':skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(run(latest=args.latest), ensure_ascii=False))


if __name__ == '__main__':
    main()
