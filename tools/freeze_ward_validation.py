"""Freeze admitted DEM sources offline without loading ward records or the game."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.replay-deps'))
sys.path.insert(0, str(ROOT))
from dota2_insights.replay_metadata_probe import inspect_source
from dota2_insights.ward_validation_freeze import freeze_sources
from dota2_insights.replay_archive import _windows_dota_running


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--request', type=Path, required=True)
    cli.add_argument('--inventory', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    if sys.platform == 'win32' and _windows_dota_running():
        cli.error('Dota is running; source freezing refused.')
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'diagnostics') or output == ROOT / 'diagnostics' or output.exists():
        cli.error('Use a new diagnostics output directory.')
    request_bytes = args.request.read_bytes()
    inventory_bytes = args.inventory.read_bytes()
    protocol = ROOT / 'docs/WARD_INDEPENDENT_VALIDATION.md'
    protocol_bytes = protocol.read_bytes()
    code_paths = [ROOT / 'dota2_insights' / name for name in
                  ('ward_validation_freeze.py', 'replay_metadata_probe.py')]
    code_paths += [ROOT / 'tools/extract_replay_wards.py', Path(__file__)]
    code_paths += [ROOT / '.replay-deps/gem/parser.py', ROOT / '.replay-deps/gem/extractors/wards.py']
    code_bytes = {p.name: p.read_bytes() for p in code_paths}
    result = freeze_sources(json.loads(request_bytes), args.inventory,
                            protocol, inspect_source)
    if (inventory_bytes != args.inventory.read_bytes() or protocol_bytes != protocol.read_bytes()
            or request_bytes != args.request.read_bytes()
            or any(p.read_bytes() != code_bytes[p.name] for p in code_paths)
            or hashlib.sha256(inventory_bytes).hexdigest() != result['inventory_sha256']
            or hashlib.sha256(protocol_bytes).hexdigest() != result['protocol_sha256']):
        raise ValueError('freeze_inputs_changed')
    result['request_sha256'] = hashlib.sha256(request_bytes).hexdigest()
    result['code_sha256'] = {name: hashlib.sha256(body).hexdigest() for name, body in code_bytes.items()}
    output.mkdir(parents=True)
    (output / 'evidence').mkdir()
    for name, saved in {'request.json': request_bytes, 'inventory.json': inventory_bytes,
                        'protocol.md': protocol_bytes, **code_bytes}.items():
        (output / 'evidence' / name).write_bytes(saved)
    body = json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8')
    (output / 'freeze.json').write_bytes(body)
    (output / 'freeze.sha256').write_text(hashlib.sha256(body).hexdigest() + '\n', encoding='ascii')
    print(json.dumps({'status': result['status'], 'rejection_reasons': result['rejection_reasons']}))
    return 2 if result['status'] == 'rejected' else 0


if __name__ == '__main__':
    raise SystemExit(main())
