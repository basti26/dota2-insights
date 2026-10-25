"""Inventory explicitly supplied offline history, without launching Dota or networking."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_validation_inventory import inventory, render_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history-root', type=Path, action='append', required=True)
    parser.add_argument('--replay-root', type=Path, action='append', required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'diagnostics') or output == ROOT / 'diagnostics' or output.exists():
        parser.error('Use a new directory under project/diagnostics.')
    if any(output.is_relative_to(root.resolve()) for root in args.history_root):
        parser.error('Output must be outside historical scan roots.')
    result = inventory(args.history_root, args.replay_root, args.catalog)
    output.mkdir(parents=True)
    (output / 'inventory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    render_inventory(result, output)
    print(json.dumps({'status': result['status'], 'sources': len(result['history_sources']),
                      'local_replays': len(result['local_replays']), 'scan_issues': len(result['scan_issues'])}))


if __name__ == '__main__':
    main()
