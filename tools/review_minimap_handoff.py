"""Review one closed standalone minimap run, without a launcher shutdown."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostics-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    from dota2_insights.history import checked_input
    from dota2_insights.minimap_handoff import review
    root = checked_input(ROOT, args.diagnostics_root)
    print(json.dumps(review(root, args.run), ensure_ascii=False))


if __name__ == '__main__':
    main()
