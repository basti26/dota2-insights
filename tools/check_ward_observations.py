"""Prepare/check independent observation metadata offline, without game access."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_observation_check import preflight, template


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('mode', choices=('template', 'check'))
    cli.add_argument('--package', type=Path, required=True)
    cli.add_argument('--observations', type=Path)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / 'diagnostics') or out == ROOT / 'diagnostics' or out.exists():
        cli.error('Use a new diagnostics output file.')
    if args.mode == 'check' and args.observations is None:
        cli.error('--observations is required for check.')
    checker_path = ROOT / 'dota2_insights/ward_observation_check.py'
    checker_code = checker_path.read_bytes()
    result = template(args.package) if args.mode == 'template' else preflight(args.package, args.observations)
    if checker_path.read_bytes() != checker_code:
        raise ValueError('observation_checker_changed')
    if args.mode == 'check':
        result['checker_sha256'] = hashlib.sha256(checker_code).hexdigest()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.mode == 'check':
        counts = {}
        for row in result['samples']:
            for state in row['fields'].values():
                counts[state] = counts.get(state, 0) + 1
        print(json.dumps({'status': result['status'], 'field_states': counts,
                          'independent_validation_passed': False}))
    else:
        print(json.dumps({'status': 'blank_independent_observations', 'samples': len(result['samples'])}))


if __name__ == '__main__':
    main()
