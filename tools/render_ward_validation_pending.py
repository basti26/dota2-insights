"""Render admitted pending sample packages without displaying answer values."""
import argparse
from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_validation_report import pending_report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--freeze', type=Path, required=True)
    cli.add_argument('--package', type=Path, action='append', required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    if not args.output.resolve().is_relative_to(ROOT / 'diagnostics') or args.output.resolve() == ROOT / 'diagnostics':
        cli.error('Use a new diagnostics output directory.')
    result = pending_report(args.freeze, args.package, args.output)
    print(json.dumps({k: result[k] for k in ('status', 'sample_count', 'planned_total', 'pending_count')}))


if __name__ == '__main__':
    main()
