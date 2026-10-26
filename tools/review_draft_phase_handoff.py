"""Read a sealed BP prefix without requiring the observer to stop."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostics-root', type=Path, required=True)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    from dota2_insights.history import checked_input
    from dota2_insights.draft_phase_handoff import review
    print(json.dumps(review(checked_input(ROOT, args.diagnostics_root), args.session), ensure_ascii=False))


if __name__ == '__main__':
    main()
