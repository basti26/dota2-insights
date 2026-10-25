"""Build descriptive, source-bound building-neighborhood ward tables."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dota2_insights.ward_building_regions import analyze

cli = argparse.ArgumentParser(description=__doc__)
for name in ('database', 'geometry', 'team-phases', 'output'):
    cli.add_argument('--'+name, type=Path, required=True)
args = cli.parse_args()
d = analyze(args.database, args.geometry, args.team_phases, args.output)
print(json.dumps({'status': 'complete', 'coverage_rows': len(d['coverage']),
                  'landmarks': len(d['landmarks']), 'condition_rows': len(d['records']),
                  'adjacent_phase_pairs': len(d['adjacent_phase_pairs'])}))
