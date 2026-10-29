"""Collect a bounded batch of existing complete analyses without downloading DEMs."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.pi_replay_library import Bridge
from dota2_insights.pi_analysis_snapshot import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=('tier1', 'own', 'd2pt'), default='tier1')
    parser.add_argument('--limit', type=int, default=8)
    parser.add_argument('--before-match', help='Continue an older batch using the prior next_before_match cursor.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == ROOT/'diagnostics' or not args.output.resolve().is_relative_to(ROOT/'diagnostics'):
        parser.error('a new diagnostics output is required')
    result = collect(Bridge(), args.output, source=args.source, limit=args.limit, before_match=args.before_match)
    print(json.dumps({'status': result['status'], 'matches': len(result['matches']),
                      'incomplete': len(result['excluded_incomplete']), 'next_before_match': result['next_before_match']}))


if __name__ == '__main__':
    main()
