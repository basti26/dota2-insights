"""Independent postgame economy worker, launched by the configured Foresight bridge."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--service', action='store_true')
    mode.add_argument('--once', action='store_true')
    mode.add_argument('--retry-match', metavar='MATCH_ID', help='review only this existing queue entry')
    args = parser.parse_args()
    from dota2_insights.automatic_economy_service import main as run, retry_match
    if args.retry_match is not None:
        try:
            state = retry_match(ROOT, args.retry_match)
        except (OSError, ValueError) as exc:
            print(f'Review not started: {type(exc).__name__}', file=sys.stderr)
            return 1
        print(state)
        if state in {'waiting_replay', 'waiting_sources_or_comparable_records', 'review_complete_evidence_only'}:
            return 0
        return 1
    return run(ROOT, once=args.once)


if __name__ == '__main__':
    raise SystemExit(main())
