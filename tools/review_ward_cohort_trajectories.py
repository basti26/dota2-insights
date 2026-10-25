"""Build a fixed-population multi-phase ward placement report offline."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_cohort_trajectories import analyze, render
from dota2_insights.postgame_store import bounded


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database', type=Path, required=True)
    cli.add_argument('--team-phases', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--map', type=Path, help='Optional pinned illustrative map; not certified per replay build.')
    cli.add_argument('--phase-context', type=Path, help='Optional matching phase-context.json ledger.')
    args = cli.parse_args()
    root = (ROOT / 'diagnostics').resolve()
    if any(not p.resolve().is_relative_to(root) or p.resolve() == root
           for p in (args.database, args.team_phases, args.output)):
        cli.error('Use diagnostics inputs and a new diagnostics output directory.')
    result = analyze(args.database, args.team_phases, args.output)
    phase_context = json.loads(bounded(args.phase_context, 32 * 1024**2)) if args.phase_context else None
    render(result, args.output, args.map, phase_context)
    print(json.dumps(result['counts']))


if __name__ == '__main__':
    main()
