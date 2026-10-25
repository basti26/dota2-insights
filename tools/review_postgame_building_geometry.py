"""Review bounded building captures against the exact same replay ward facts."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dota2_insights.postgame_building_geometry import analyze

cli = argparse.ArgumentParser(description=__doc__)
cli.add_argument('--database', required=True, type=Path)
cli.add_argument('--phase-context', required=True, type=Path)
cli.add_argument('--capture', required=True, action='append', type=Path)
cli.add_argument('--output', required=True, type=Path)
args = cli.parse_args()
result = analyze(args.database, args.phase_context, args.capture, args.output)
print(json.dumps({'status': 'complete', 'captures': len(result['captures']),
                  'anchors': len(result['anchors']), 'wards': len(result['ward_records']),
                  'target_candidates': result['event_candidate_statuses'],
                  'pairs': len(result['ward_target_pairs']),
                  'raw_location_coverage': result['all_bound_targets_raw_location_coverage']}))
