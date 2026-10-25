"""Prepare pixel-only map holdouts, freeze observations, then compare."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dota2_insights.map_holdout import prepare, freeze, compare
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--atlas', type=Path, required=True)
    p.add_argument('--request', type=Path, required=True)
    for mode in ('freeze', 'compare'):
        p = sub.add_parser(mode)
        p.add_argument('--package', type=Path, required=True)
        p.add_argument('--observations' if mode == 'freeze' else '--batch', type=Path, required=True)
    for p in sub.choices.values():
        p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / 'diagnostics').resolve()):
        parser.error('output must be a new diagnostics directory')
    try:
        if args.mode == 'prepare':
            r = prepare(args.atlas, args.request, args.output)
            print('prepared', len(r['points']), 'points', len(r['frames']), 'frames')
        elif args.mode == 'freeze':
            r = freeze(args.package, args.observations, args.output)
            print('observations frozen; predictions not read')
        else:
            r = compare(args.package, args.batch, args.output)
            print(r['counts'], 'p2_supported=', r['p2_supported'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('failed:', type(exc).__name__, str(exc)[:200])
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
