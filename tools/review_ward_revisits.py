"""Describe same-region revisits using an existing immutable postgame facts DB."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_revisits import analyze, render


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database', type=Path, required=True)
    cli.add_argument('--map', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    root = (ROOT / 'diagnostics').resolve()
    source, output = args.database.resolve(), args.output.resolve()
    if not source.is_relative_to(root) or not output.is_relative_to(root) or output == root or output.exists():
        cli.error('Use an existing diagnostics database and a new diagnostics output directory.')
    result = analyze(source, output)
    render(result, output, args.map)
    summary = {k: result[k] for k in ('schema_version', 'database_sha256', 'matches', 'input_wards', 'in_game_coordinate_wards', 'exclusions')}
    summary['status'] = 'complete'
    summary['primary_review_candidates'] = sum(r['grid_offset'] == 0 and r['horizon_s'] == 600 and r['review_candidate'] for r in result['aggregates'])
    summary['source_code_sha256'] = hashlib.sha256((ROOT / 'dota2_insights/ward_revisits.py').read_bytes()).hexdigest()
    (output / 'run-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
