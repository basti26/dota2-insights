"""Freeze client observations, compare a frozen batch, or prepare a blind offline queue."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_review_batch import (
    compare_batch, freeze_batch, render_comparison, render_queue, review_queue, write_new,
)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='mode', required=True)
    freeze = sub.add_parser('freeze')
    freeze.add_argument('--package', type=Path, required=True)
    freeze.add_argument('--observations', type=Path, required=True)
    freeze.add_argument('--sample-id', action='append')
    freeze.add_argument('--prior-exposure', choices=('not_declared', 'blind_declared', 'previously_compared'),
                        default='not_declared')
    freeze.add_argument('--output', type=Path, required=True)
    compare = sub.add_parser('compare')
    compare.add_argument('--package', type=Path, required=True)
    compare.add_argument('--batch', type=Path, required=True)
    compare.add_argument('--output', type=Path, required=True)
    queue = sub.add_parser('queue')
    queue.add_argument('--package', type=Path, required=True, action='append')
    queue.add_argument('--remaining-ledger', type=Path,
                       help='explicit source-bound ledger of remaining vs previously exposed samples')
    queue.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / 'diagnostics') or out == ROOT / 'diagnostics' or out.exists():
        cli.error('Use a new diagnostics output directory.')
    if args.mode == 'freeze':
        result = freeze_batch(args.package, args.observations, out, args.sample_id,
                              prior_exposure=args.prior_exposure)
        print('Frozen observation batch:', len(result['sample_ids']), 'samples; no answers opened.')
    elif args.mode == 'compare':
        result = compare_batch(args.package, args.batch)
        render_comparison(result, args.batch, out)
        print('Compared:', result['sample_count'], 'samples;', result['counts'], '; overall not certified.')
    else:
        result = review_queue(args.package, remaining_ledger=args.remaining_ledger)
        out.mkdir(parents=True)
        write_new(out / 'queue.json', result)
        render_queue(result, out)
        # A recording belongs to one replay. Per-match files avoid accidentally
        # loading another match's navigation into the current recording.
        if args.remaining_ledger:
            for mid in sorted({r['match_id'] for r in result['samples']}):
                folder = out / ('match-' + mid)
                folder.mkdir()
                scoped = dict(result, samples=[r for r in result['samples'] if r['match_id'] == mid])
                scoped['sample_count'] = len(scoped['samples'])
                write_new(folder / 'queue.json', scoped)
                render_queue(scoped, folder)
        print('Prepared blind navigation queue:', result['sample_count'], 'samples; no answers opened.')


if __name__ == '__main__':
    main()
