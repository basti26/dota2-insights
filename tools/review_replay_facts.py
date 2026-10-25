"""Review an existing local archive without loading a parser or fetching data."""
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--facts', type=Path)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args(argv)
    from dota2_insights.replay_fact_review import review_archive
    try:
        summary = review_archive(args.archive, args.output, facts=args.facts, receipt=args.receipt)
    except Exception as exc:
        print(json.dumps({'complete': False, 'error_type': type(exc).__name__,
                          'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
