"""Extract one admitted development match, or bind its existing completed export."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_validation_inventory import digest
from dota2_insights.ward_validation_package import admitted_development, build_development_package
from dota2_insights.replay_archive import _windows_dota_running


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--freeze', type=Path, required=True)
    cli.add_argument('--match-id', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--from-extraction', type=Path)
    cli.add_argument('--max-seconds', type=int, default=600)
    args = cli.parse_args()
    output = args.output.resolve()
    if (not output.is_relative_to(ROOT / 'diagnostics') or output == ROOT / 'diagnostics'
            or output.exists() or not 1 <= args.max_seconds <= 600):
        cli.error('Use a new diagnostics directory and 1..600 second parse budget.')
    frozen, match, _ = admitted_development(args.freeze, args.match_id)
    if sys.platform == 'win32' and _windows_dota_running():
        cli.error('Dota is running; offline preparation refused.')
    if digest(Path(match['source'])) != match['replay_sha256']:
        raise ValueError('replay_changed_since_source_freeze')
    extraction = args.from_extraction
    if extraction is None:
        code_paths = {'extract_replay_wards.py': ROOT / 'tools/extract_replay_wards.py',
                      'parser.py': ROOT / '.replay-deps/gem/parser.py',
                      'wards.py': ROOT / '.replay-deps/gem/extractors/wards.py'}
        if any(digest(path) != frozen['code_sha256'][name] for name, path in code_paths.items()):
            raise ValueError('extractor_changed_since_source_freeze')
        output.mkdir(parents=True)
        extraction = output / 'extraction'
        command = [sys.executable, str(ROOT / 'tools/extract_replay_wards.py'), match['source'],
                   '--output', str(extraction), '--max-seconds', str(args.max_seconds)]
        with (output / 'extraction.log').open('w', encoding='utf-8') as log:
            try:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                    timeout=args.max_seconds + 45,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            except subprocess.TimeoutExpired:
                (output / 'preparation.json').write_text(json.dumps({'status': 'parent_watchdog_timeout',
                    'match_id': match['match_id'], 'independent_validation_passed': False}), encoding='utf-8')
                print('Extraction watchdog stopped; outputs preserved without retry.')
                return 2
        if result.returncode != 0:
            (output / 'preparation.json').write_text(json.dumps({'status': 'extraction_incomplete',
                'returncode': result.returncode, 'match_id': match['match_id'],
                'independent_validation_passed': False}), encoding='utf-8')
            print('Extraction incomplete; preserved, not retried or admitted as samples.')
            return 2
        package_output = output / 'samples'
    else:
        package_output = output
    if digest(Path(match['source'])) != match['replay_sha256']:
        raise ValueError('replay_changed_during_preparation')
    result = build_development_package(args.freeze, args.match_id, extraction, package_output)
    print(json.dumps({k: result[k] for k in ('status', 'match_id', 'random_selected', 'special_selected')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
