"""Bind developer ward samples to immutable source admission and extraction."""
import csv
import hashlib
import json
from pathlib import Path

from dota2_insights.ward_validation_sampling import prepare_samples


def sha(body):
    return hashlib.sha256(body).hexdigest()


def admitted_development(freeze_path, match_id):
    path = Path(freeze_path)
    body = path.read_bytes()
    if sha(body) != path.with_suffix('.sha256').read_text(encoding='ascii').strip():
        raise ValueError('freeze_fingerprint_mismatch')
    frozen = json.loads(body)
    if frozen.get('status') != 'sources_frozen_pending_samples':
        raise ValueError('source_admission_required')
    rows = [r for r in frozen['matches'] if r['match_id'] == str(match_id)]
    if len(rows) != 1 or rows[0].get('role') != 'development':
        raise ValueError('heldout_locked_or_match_not_admitted')
    evidence = path.parent / 'evidence'
    required = {'protocol.md': frozen['protocol_sha256'], 'inventory.json': frozen['inventory_sha256'],
                'request.json': frozen['request_sha256'], **frozen['code_sha256']}
    for name, fingerprint in required.items():
        if Path(name).name != name or sha((evidence / name).read_bytes()) != fingerprint:
            raise ValueError('freeze_evidence_fingerprint_mismatch')
    return frozen, rows[0], body


def bind_extraction(frozen, match, extraction):
    extraction = Path(extraction)
    manifest_bytes = (extraction / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    code = frozen['code_sha256']
    if (manifest.get('complete') is not True or manifest.get('parse_error') is not None
            or manifest.get('stop_reason') != 'replay_eof'
            or str(manifest.get('match_id')) != match['match_id']
            or str(manifest.get('parsed_match_id')) != match['match_id']
            or manifest.get('source_sha256') != match['replay_sha256']
            or manifest.get('source_unchanged_stat') is not True
            or manifest.get('independent_footer_matches_parser') is not True
            or manifest.get('adapter_sha256') != code['extract_replay_wards.py']
            or manifest.get('parser_sha256') != code['parser.py']
            or manifest.get('ward_extractor_sha256') != code['wards.py']
            or manifest.get('parser_version') != match['metadata']['gem_version']):
        raise ValueError('completed_bound_extraction_required')
    wards_path = extraction / 'wards.json'
    if wards_path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('ward_json_budget')
    body = wards_path.read_bytes()
    file_entry = manifest['files']['wards.json']
    if sha(body) != file_entry['sha256'] or len(body) != file_entry['bytes']:
        raise ValueError('ward_export_fingerprint_mismatch')
    records = json.loads(body)
    if (not isinstance(records, list) or len(records) > 5000 or len(records) != manifest.get('ward_count')
            or any(not isinstance(r, dict) or str(r.get('match_id')) != match['match_id'] for r in records)):
        raise ValueError('ward_export_identity_or_count_mismatch')
    if (extraction / 'manifest.json').read_bytes() != manifest_bytes or wards_path.read_bytes() != body:
        raise ValueError('extraction_changed')
    return manifest, manifest_bytes, records, body


def build_development_package(freeze_path, match_id, extraction, output):
    frozen, match, freeze_bytes = admitted_development(freeze_path, match_id)
    output = Path(output)
    if output.exists():
        raise ValueError('new_output_required')
    manifest, manifest_bytes, records, ward_bytes = bind_extraction(frozen, match, extraction)
    sampling_code = Path(__file__).with_name('ward_validation_sampling.py').read_bytes()
    package_code = Path(__file__).read_bytes()
    result = prepare_samples(records, match['replay_sha256'], frozen['random_per_match'],
                             frozen['special_per_match'], match_end_game_s=manifest.get('match_end_game_time_s'))
    blind, answers = result.pop('blind'), result.pop('answers')
    for row in blind + answers:
        row.update(match_id=match['match_id'], role=match['role'], cohort_key=match['cohort_key'])
    output.mkdir(parents=True)
    for folder in ('review', 'sealed-answers', 'evidence'):
        (output / folder).mkdir()
    for name, value in (('review/blind.json', blind), ('sealed-answers/answers.json', answers)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'review/blind.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = ('sample_id', 'match_id', 'record_index', 'group', 'role', 'navigation_game_s',
                  'observed_type', 'observed_team', 'placed_game_s', 'ended_game_s', 'lifetime_s',
                  'end_reason', 'world_x', 'world_y', 'map_u', 'map_v', 'reviewer', 'reviewed_at_utc',
                  'unjudgeable_reason', 'status')
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(blind)
    for name, body in (('freeze.json', freeze_bytes), ('manifest.json', manifest_bytes)):
        (output / 'evidence' / name).write_bytes(body)
    (output / 'evidence/ward_validation_sampling.py').write_bytes(sampling_code)
    (output / 'evidence/ward_validation_package.py').write_bytes(package_code)
    if (sampling_code != Path(__file__).with_name('ward_validation_sampling.py').read_bytes()
            or package_code != Path(__file__).read_bytes()):
        raise ValueError('sampling_code_changed')
    result.update(status='samples_frozen_pending_visual_review', match_id=match['match_id'], role='development',
                  cohort_key=match['cohort_key'], source_freeze_sha256=sha(freeze_bytes),
                  source_wards_sha256=sha(ward_bytes), extractor_manifest_sha256=sha(manifest_bytes),
                  protocol_sha256=frozen['protocol_sha256'], visual_review_complete=False,
                  sampler_sha256=sha(sampling_code), package_builder_sha256=sha(package_code),
                  files={name: sha((output / name).read_bytes()) for name in
                         ('review/blind.json', 'review/blind.csv', 'sealed-answers/answers.json')})
    body = json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8')
    (output / 'package.json').write_bytes(body)
    (output / 'package.sha256').write_text(sha(body) + '\n', encoding='ascii')
    return result
