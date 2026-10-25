"""Stream known large history files, preserving the original inventory."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_validation_gap_scan import resolve_gaps
from dota2_insights.ward_validation_inventory import render_inventory
from dota2_insights.replay_archive import _windows_dota_running


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--inventory', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--max-seconds', type=int, default=180)
    args = cli.parse_args()
    output = args.output.resolve()
    if (not output.is_relative_to(ROOT / 'diagnostics') or output == ROOT / 'diagnostics'
            or output.exists() or not 1 <= args.max_seconds <= 300):
        cli.error('Use a new diagnostics output directory and 1..300 second budget.')
    checked = 0

    def guard():
        nonlocal checked
        now = time.monotonic()
        if now - checked >= 5:
            checked = now
            if sys.platform == 'win32' and _windows_dota_running():
                raise RuntimeError('Dota started; offline scan stopped.')

    source = args.inventory.read_bytes()
    guard()
    result = resolve_gaps(json.loads(source), max_seconds=args.max_seconds, guard=guard)
    result['parent_inventory_sha256'] = hashlib.sha256(source).hexdigest()
    result['stream_scanner_sha256'] = hashlib.sha256((ROOT / 'dota2_insights/ward_validation_gap_scan.py').read_bytes()).hexdigest()
    output.mkdir(parents=True)
    (output / 'inventory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    render_inventory(result, output)
    print(json.dumps({'stream_resolved_files': result['stream_resolved_file_count'],
                      'remaining_issues': len(result['scan_issues']), 'independent_validation_passed': False}))


if __name__ == '__main__':
    main()
