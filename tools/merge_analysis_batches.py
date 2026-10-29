"""Merge completed local Pi analysis batches into a new, deduplicated snapshot."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.analysis_batch_registry import merge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, action='append', default=[])
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--select', action='append', default=[], metavar='MATCH_ID=SOURCE_ID')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = [*args.batch, args.output]+([args.previous] if args.previous else [])
    diagnostics = ROOT/'diagnostics'
    if any(p.resolve() == diagnostics or not p.resolve().is_relative_to(diagnostics) for p in paths):
        parser.error('use explicit diagnostics batches, prior registry and a new output')
    selections = {}
    for item in args.select:
        identifier, sep, source = item.partition('=')
        if not sep or identifier in selections:
            parser.error('each selection must uniquely specify MATCH_ID=SOURCE_ID')
        selections[identifier] = source
    result = merge(args.batch, args.output, previous=args.previous, selection=selections)
    print(json.dumps({k: result[k] for k in ('status','unique_matches','selected_matches','conflicting_matches','duplicate_entries','capture_count')}))


if __name__ == '__main__':
    main()
