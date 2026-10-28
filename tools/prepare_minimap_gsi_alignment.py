"""Sync closed native sources and prepare exact-ID coordinate review evidence."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(run_id, *, root=ROOT, roots=None, latest=25):
    from dota2_insights.history import history_roots, checked_output
    from dota2_insights.match_journal_index import sync as sync_gsi
    from dota2_insights.minimap_index import sync as sync_minimap
    from dota2_insights.minimap_gsi_alignment import prepare, render_markdown
    root = Path(root).resolve()
    roots = roots if roots is not None else history_roots(root/'config/history.local.json')
    minimap = sync_minimap(root, roots, latest=latest)
    native = sync_gsi(root, roots, latest=latest)
    entries = [e for e in minimap['entries'] if e.get('run_id') == run_id]
    if len(entries) != 1:
        raise ValueError('alignment_run_not_uniquely_indexed_in_scan')
    report = prepare(entries[0], native['entries'], roots)
    output = checked_output(root, root/'diagnostics/minimap-gsi-alignment'/
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    if any(output.is_relative_to(Path(p).resolve()) for p in roots.values()):
        raise ValueError('alignment_output_inside_history')
    output.mkdir(parents=True, exist_ok=False)
    (output/'REPORT.md').write_text(render_markdown(report), encoding='utf-8')
    temporary = output/'report.json.tmp'
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(output/'report.json')  # Completion artifact is published only after source rechecks.
    return {'output': str(output), 'sample_rows': report['sample_rows'], 'outcomes': report['outcomes']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, help='closed minimap run ID')
    parser.add_argument('--latest', type=int, default=25, help='bounded scan, 1–128')
    args = parser.parse_args()
    print(json.dumps(run(args.run, latest=args.latest), ensure_ascii=False))


if __name__ == '__main__':
    main()
