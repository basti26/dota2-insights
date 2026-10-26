"""Observe full-crop official minimap icons first; compare only frozen observations."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dota2_insights.map_glyph_review import observe, compare
    from dota2_insights.history import checked_input, checked_output
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    for mode in ('observe', 'compare'):
        p = sub.add_parser(mode)
        p.add_argument('--package', type=Path, required=True)
        p.add_argument('--icons' if mode == 'observe' else '--batch', type=Path, required=True)
        p.add_argument('--output', type=Path, required=True)
        if mode=='observe':p.add_argument('--profile',choices=('basic','buildings','buildings_tint'),default='basic')
    args = parser.parse_args()
    package = checked_input(ROOT, args.package)
    output = checked_output(ROOT, args.output)
    r = observe(package, args.icons, output,profile=args.profile) if args.mode == 'observe' else compare(
        package, checked_input(ROOT, args.batch), output)
    print(args.mode, len(r['rows']), 'rows; map_certified=false')


if __name__ == '__main__':
    main()
