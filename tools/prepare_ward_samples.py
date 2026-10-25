"""Create separated blind/answer drafts offline; does not admit or freeze matches."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_validation_sampling import prepare_samples


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--wards', type=Path, required=True)
    cli.add_argument('--manifest', type=Path, help='Optional bound completed extractor manifest, for match-end candidates')
    cli.add_argument('--replay-sha256', required=True)
    cli.add_argument('--two-match-plan', action='store_true')
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'diagnostics') or output == ROOT / 'diagnostics' or output.exists():
        cli.error('Use a new diagnostics output directory.')
    if args.wards.stat().st_size > 16 * 1024 * 1024:
        cli.error('Ward JSON exceeds bounded input limit.')
    source = args.wards.read_bytes()
    records = json.loads(source)
    if not isinstance(records, list) or len(records) > 5000 or any(not isinstance(r, dict) for r in records):
        cli.error('Expected an extracted ward list, at most 5000 records.')
    random_count, special_count = (16, 8) if args.two_match_plan else (12, 6)
    manifest_bytes = None
    match_end = None
    if args.manifest:
        manifest_bytes = args.manifest.read_bytes()
        manifest = json.loads(manifest_bytes)
        if (manifest.get('complete') is not True
                or manifest.get('source_sha256') != args.replay_sha256
                or manifest.get('files', {}).get('wards.json', {}).get('sha256') != hashlib.sha256(source).hexdigest()
                or any(str(r.get('match_id')) != str(manifest.get('match_id')) for r in records)):
            cli.error('Manifest must bind this completed ward export and source replay.')
        match_end = manifest.get('match_end_game_time_s')
    result = prepare_samples(records, args.replay_sha256, random_count, special_count, match_end_game_s=match_end)
    if args.wards.read_bytes() != source:
        raise ValueError('source_changed')
    output.mkdir(parents=True)
    blind, answers = result.pop('blind'), result.pop('answers')
    (output / 'review').mkdir()
    (output / 'sealed-answers').mkdir()
    for path, value in ((output / 'review/blind.json', blind),
                        (output / 'sealed-answers/answers.json', answers)):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    result['source_wards_sha256'] = hashlib.sha256(source).hexdigest()
    result['extractor_manifest_sha256'] = hashlib.sha256(manifest_bytes).hexdigest() if manifest_bytes is not None else None
    result['sampler_sha256'] = hashlib.sha256((ROOT / 'dota2_insights/ward_validation_sampling.py').read_bytes()).hexdigest()
    result['blind_sha256'] = hashlib.sha256((output / 'review/blind.json').read_bytes()).hexdigest()
    result['answers_sha256'] = hashlib.sha256((output / 'sealed-answers/answers.json').read_bytes()).hexdigest()
    (output / 'receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'random_selected', 'special_selected')}))


if __name__ == '__main__':
    main()
