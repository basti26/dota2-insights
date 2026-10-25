"""Freeze the next small metadata batch without choosing by observed outcome."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.postgame_metadata_store import resolved_metadata


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database', type=Path, required=True)
    cli.add_argument('--producer-cohort', required=True)
    cli.add_argument('--source-group', choices=('own', 'd2pt'), required=True)
    cli.add_argument('--seed', required=True)
    cli.add_argument('--count', type=int, default=8)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    diagnostics = (ROOT / 'diagnostics').resolve()
    database, output = args.database.resolve(), args.output.resolve()
    if (not database.is_relative_to(diagnostics) or not output.is_relative_to(diagnostics)
            or output == diagnostics or output.exists() or not 1 <= args.count <= 8
            or len(args.producer_cohort) != 64 or not args.seed or len(args.seed) > 128):
        cli.error('Use diagnostics sources, a new output, an exact cohort and count 1..8.')
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    c = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        resolved = resolved_metadata(c)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        attempted = ({r[0] for r in c.execute('SELECT DISTINCT match_id FROM replay_metadata_probes')}
                     if 'replay_metadata_probes' in tables else set())
        population, exclusions, candidates = [], [], []
        for row in c.execute('SELECT match_id,replay_sha256 FROM matches WHERE source_group=? AND producer_cohort=? ORDER BY match_id',
                             (args.source_group, args.producer_cohort)):
            value = dict(row)
            mid = value['match_id']
            population.append(value)
            reason = ('previous_probe_no_automatic_retry' if mid in attempted else
                      'build_already_recorded' if resolved[mid]['game_build'] is not None else
                      'source_fingerprint_missing' if not value['replay_sha256'] else None)
            if reason:
                exclusions.append({**value, 'reason': reason})
            else:
                candidates.append({**value, 'selection_hash': hashlib.sha256((args.seed + ':' + mid).encode()).hexdigest()})
    finally:
        c.close()
    if not population:
        raise ValueError('metadata_plan_explicit_cohort_not_found')
    if hashlib.sha256(database.read_bytes()).hexdigest() != before:
        raise ValueError('metadata_plan_database_changed')
    candidates.sort(key=lambda r: (r['selection_hash'], r['match_id']))
    result = {'schema_version': 1, 'kind': 'fixed_bounded_replay_metadata_batch',
              'database_sha256': before, 'source_group': args.source_group,
              'producer_cohort': args.producer_cohort, 'seed': args.seed,
              'population': population, 'exclusions': exclusions,
              'ranked_candidates': candidates, 'selected': candidates[:args.count],
              'requested_count': args.count, 'maximum_probes': 8,
              'source_selection': 'unread_unknown_build_within_explicit_cohort',
              'selection_does_not_use_outcome_duration_or_probe_results': True,
              'no_automatic_retry_or_replacement': True,
              'probe_source_sha256': hashlib.sha256((ROOT / 'dota2_insights/replay_metadata_probe.py').read_bytes()).hexdigest(),
              'planner_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'status': 'plan_frozen', 'population': len(population),
                      'excluded': len(exclusions), 'candidates': len(candidates),
                      'selected': [r['match_id'] for r in result['selected']]}))


if __name__ == '__main__':
    main()
