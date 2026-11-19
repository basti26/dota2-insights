"""Build/import local listening notes; no capture, models, game or network."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.audio_listening_review import build_listening_page, import_listening_notes
from dota2_insights.history import checked_input, checked_output


def local_path(value):
    return checked_output(ROOT, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    build = actions.add_parser('build')
    build.add_argument('--review', required=True, type=lambda value: checked_input(ROOT, value))
    build.add_argument('--output', required=True, type=local_path)
    blind = actions.add_parser('sample-blind')
    blind.add_argument('--audio-match-package', required=True, type=lambda value: checked_input(ROOT, value))
    blind.add_argument('--expected-package-id', required=True)
    blind.add_argument('--output', required=True, type=local_path)
    blind.add_argument('--seed', required=True, type=int)
    blind.add_argument('--total', type=int, default=24)
    load = actions.add_parser('import')
    load.add_argument('--page', required=True, type=local_path)
    load.add_argument('--labels', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'build':
        output, count = build_listening_page(args.review, args.output)
        print(json.dumps({'page': str(output / 'index.html'), 'clips': count}, ensure_ascii=False))
    elif args.action == 'sample-blind':
        from dota2_insights.audio_blind_review import prepare_blind_match
        prepare_blind_match(args.audio_match_package, args.output, package_id=args.expected_package_id,
                            seed=args.seed, total=args.total)
        output, count = build_listening_page(args.output / 'review.json', args.output / 'listening-page')
        print(json.dumps({'page': str(output / 'index.html'), 'clips': count}, ensure_ascii=False))
    else:
        output, counts = import_listening_notes(args.page, args.labels)
        print(json.dumps({'receipt': str(output / 'notes.json'), 'counts': counts}, ensure_ascii=False))


if __name__ == '__main__':
    main()
