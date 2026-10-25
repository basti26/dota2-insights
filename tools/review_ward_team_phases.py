"""Review paired team placement states on complete and common ten-minute phases."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_team_phase_states import analyze, render
from dota2_insights.ward_placement_sequences import analyze as analyze_sequences
from dota2_insights.ward_sequence_context import analyze as analyze_context
from dota2_insights.postgame_store import sha


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database', type=Path, required=True)
    cli.add_argument('--phases', type=Path, required=True)
    cli.add_argument('--map', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    diagnostics = (ROOT / 'diagnostics').resolve()
    if any(not p.resolve().is_relative_to(diagnostics) or p.resolve() == diagnostics
           for p in (args.database, args.phases, args.output)):
        cli.error('Use diagnostics inputs and a new diagnostics output directory.')
    names = ('ward_team_phase_states.py', 'ward_placement_sequences.py', 'ward_sequence_context.py',
             'postgame_event_types.py', 'postgame_capture_review.py', 'ward_interval_phases.py', 'ward_interval_context.py',
             'ward_patterns.py', 'postgame_store.py', 'postgame_metadata_store.py', 'replay_metadata_probe.py')
    code = {n: (ROOT / 'dota2_insights' / n).read_bytes() for n in names}
    entry = Path(__file__).read_bytes()
    result = analyze(args.database, args.phases, args.output)
    sequences = analyze_sequences(args.database, args.output / 'team-phase-states.json', args.output)
    context = analyze_context(args.database, args.output / 'placement-sequences.json', args.output)
    render(result, args.output, args.map, sequences, context)
    if Path(__file__).read_bytes() != entry or any((ROOT / 'dota2_insights' / n).read_bytes() != b for n, b in code.items()):
        raise ValueError('team_phase_code_changed')
    (args.output / 'source-code').mkdir()
    for n, b in {**code, Path(__file__).name: entry}.items():
        (args.output / 'source-code' / n).write_bytes(b)
    receipt = {'status': 'complete', 'database_sha256': result['database_sha256'],
               'source_phase_sha256': result['source_phase_sha256'],
               'summary_strata': len(result['summaries']),
               'summary_cells': sum(len(r['cells']) for r in result['summaries']),
               'paired_strata': len(result['paired_phase_states']),
               'paired_cells': sum(len(r['cells']) for r in result['paired_phase_states']),
               'placement_sequence_counts': sequences['counts'],
               'sequence_target_context_counts': context['counts'],
               'source_code_sha256': {n: sha(b) for n, b in code.items()}, 'entrypoint_sha256': sha(entry)}
    (args.output / 'run-summary.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
