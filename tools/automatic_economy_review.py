"""Independent postgame economy worker, launched by the configured Foresight bridge."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not args.service and not args.once:
        parser.error('choose --service or --once')
    from dota2_insights.automatic_economy_service import main as run
    return run(ROOT, once=args.once)


if __name__ == '__main__':
    raise SystemExit(main())
