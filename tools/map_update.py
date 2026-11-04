"""Check, capture or prepare a local map revision. No automatic activation."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dota2_insights.history import checked_input, checked_output
    from dota2_insights.map_updates import inspect_installation, latest_revision, read_revision, read_prepared, compare, capture, prepare_revision, check_prototype
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    for name in ('check', 'capture'):
        sub = commands.add_parser(name)
        sub.add_argument('--game', type=Path, required=True)
        sub.add_argument('--store', type=Path, default=ROOT/'diagnostics/map-versions')
        if name == 'check':
            sub.add_argument('--prototype', type=Path)
            sub.add_argument('--output', type=Path)
    sub = commands.add_parser('diff')
    sub.add_argument('--before', type=Path, required=True)
    sub.add_argument('--after', type=Path, required=True)
    sub = commands.add_parser('prepare')
    sub.add_argument('--revision', type=Path, required=True)
    sub.add_argument('--decoder', type=Path, required=True)
    sub = commands.add_parser('verify')
    sub.add_argument('--extraction', type=Path, required=True)
    sub.add_argument('--revision', type=Path)
    args = cli.parse_args()
    if args.command == 'capture':
        path, snapshot = capture(args.game, args.store)
        result = {'revision': str(path), 'identity': snapshot['identity'], 'probe': snapshot['probe'], 'production_ready': False}
    elif args.command == 'check':
        store = checked_output(ROOT, args.store)
        prior = latest_revision(store)
        current = inspect_installation(args.game)
        result = compare(prior[1] if prior else None, current)
        result['probe'] = current['probe']
        if args.prototype:
            result['prototype'] = check_prototype(checked_input(ROOT, args.prototype), current)
        if args.output:
            from dota2_insights.map_holdout import save
            folder = checked_output(ROOT, args.output)
            folder.mkdir(parents=True, exist_ok=False)
            save(folder/'check.json', result)
    elif args.command == 'diff':
        result = compare(read_revision(checked_input(ROOT, args.before)), read_revision(checked_input(ROOT, args.after)))
    elif args.command == 'verify':
        result = read_prepared(checked_input(ROOT, args.extraction),
            checked_input(ROOT, args.revision) if args.revision else None)
    else:
        path, result = prepare_revision(checked_input(ROOT, args.revision), decoder=args.decoder)
        result = {'output': str(path), **result}
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
