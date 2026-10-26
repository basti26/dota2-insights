"""Read only sealed per-match own GSI records, without stopping the assistant."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


from dota2_insights.match_journal_review import review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(review(args.folder), ensure_ascii=False))


if __name__ == '__main__':
    main()
