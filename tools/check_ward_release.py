"""Freeze a validation revision or assess readiness without touching heldout data."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_review_batch import read_json, write_new
from dota2_insights.ward_release_readiness import revision_snapshot, release_readiness


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('mode', choices=('freeze-revision', 'check'))
    cli.add_argument('--freeze', type=Path, required=True)
    cli.add_argument('--label')
    cli.add_argument('--revision', type=Path)
    cli.add_argument('--reviews', type=Path)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / 'diagnostics') or out == ROOT / 'diagnostics' or out.exists():
        cli.error('Use a new diagnostics output file.')
    if args.mode == 'freeze-revision':
        if not args.label:
            cli.error('--label is required.')
        result = revision_snapshot(ROOT, args.freeze, args.label)
    else:
        if args.revision is None or args.reviews is None:
            cli.error('--revision and --reviews are required.')
        reviews, _ = read_json(args.reviews)
        result = release_readiness(ROOT, args.freeze, args.revision, reviews)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_new(out, result)
    print(result.get('status', result['kind']), '; heldout remains locked.')


if __name__ == '__main__':
    main()
