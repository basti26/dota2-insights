"""A single completed replay's placements, without preference or tree-clearance guesses."""
import base64
import hashlib
import json
import math
import re
from pathlib import Path

from .replay_holdout import refuse_locked_match


def height_fields(manifest,ward,index):
    value=ward.get('height_observation')
    if value is None:return {}
    from .replay_height import height_observation,FIELDS
    if not isinstance(value,dict) or not isinstance(value.get('components'),dict):
        raise ValueError('ward_reference_height_shape')
    if (value.get('centered_transform_verified') is not False or value.get('ground_height_verified') is not False
            or value.get('status')=='recorded' and (type(value.get('raw_z')) not in (int,float) or not math.isfinite(value['raw_z']))):
        raise ValueError('ward_reference_height_type_or_claim')
    computed=height_observation(value['components'])
    # Invalid raw types were already sanitized by the producer. Preserve its
    # invalid/missing distinction, but never a value or an accuracy claim.
    if value.get('status') in ('missing_components','invalid_components'):
        computed['status']=value['status']
    if value!=computed or set(value['components'])!=set(FIELDS):
        raise ValueError('ward_reference_height_value_or_claim')
    if (ward.get('height_association')!='same_native_placement_event_and_entity_callback'
            or any(type(ward.get(k)) is not int or not 0<=ward[k]<2**32 for k in ('height_entity_index','height_entity_serial'))
            or any(not isinstance(manifest.get(k),str) or not re.fullmatch('[a-f0-9]{64}',manifest[k])
                   for k in ('height_adapter_sha256','source_sha256'))):
        raise ValueError('ward_reference_height_source')
    return {'height_observation':computed,'height_source':{'ward_index':index,
        'entity_index':ward['height_entity_index'],'entity_serial':ward['height_entity_serial'],
        'association':ward['height_association']}}


def build(manifest, wards_raw, *, tree_reviews=None):
    identifier = str(manifest.get('match_id'))
    refuse_locked_match(identifier)
    if (manifest.get('complete') is not True or manifest.get('parse_error') is not None
            or manifest.get('independent_footer_matches_parser') is not True
            or manifest.get('parsed_match_id') != identifier):
        raise ValueError('ward_reference_complete_identity_required')
    spec = manifest.get('files', {}).get('wards.json', {})
    digest = hashlib.sha256(wards_raw).hexdigest()
    if spec.get('sha256') != digest or spec.get('bytes') != len(wards_raw):
        raise ValueError('ward_reference_source_changed')
    wards = json.loads(wards_raw)
    if not isinstance(wards, list) or len(wards) > 5000 or manifest.get('ward_count') != len(wards):
        raise ValueError('ward_reference_count_invalid')
    reviews = {}
    if tree_reviews is not None:
        if tree_reviews.get('match_id') != identifier or tree_reviews.get('wards_sha256') != digest:
            raise ValueError('tree_review_source_mismatch')
        for review in tree_reviews.get('points', []):
            key = review.get('id')
            if (key in reviews or review.get('clearance') not in ('required', 'not_required')
                    or not isinstance(review.get('evidence'), str) or not review['evidence'].strip()):
                raise ValueError('tree_review_explicit_evidence_required')
            reviews[key] = review
    points, unavailable = [], []
    for index, ward in enumerate(wards):
        key = f'{identifier}:{index}'
        if ward.get('match_id') != identifier:
            raise ValueError('ward_reference_cross_match')
        if ward.get('after_match_end') is True:
            unavailable.append({'id': key, 'reason': 'after_match_end'})
            continue
        x, y = ward.get('x'), ward.get('y')
        if (ward.get('ward_type') not in ('observer', 'sentry') or
                any(type(v) not in (float, int) or not math.isfinite(v) for v in (x,y))):
            unavailable.append({'id': key, 'reason': 'unplottable_position_or_type'})
            continue
        review = reviews.pop(key, None)
        points.append({'id': key, 'x': x, 'y': y, 'ward_type': ward['ward_type'],
            'team': ward.get('team'), 'placed_game_time_s': ward.get('placed_game_time_s'),
            'tree_clearance': review['clearance'] if review else 'unknown',
            'tree_evidence': review['evidence'] if review else None,
            **height_fields(manifest,ward,index)})
    if reviews:
        raise ValueError('tree_review_unknown_ward')
    return {'schema_version':1,'kind':'historical_ward_reference','match_id':identifier,
        'source_replay_sha256':manifest.get('source_sha256'),'source_wards_sha256':digest,
        'height_adapter_sha256':manifest.get('height_adapter_sha256'),
        'coordinate_basis':'gem_cell128_plus_vec_raw_coordinates','map_version':'unknown',
        'source_ward_count':len(wards),'points':points,'unavailable':unavailable,
        'tree_clearance_inferred':False,'preference_statistics':False,'world_projection_verified':False}


def collect(bridge, identifier, output):
    identifier = str(identifier)
    refuse_locked_match(identifier)
    catalog = bridge.catalog()
    record = catalog['matches'].get(identifier)
    if not record or record.get('analysis_status') != 'complete':
        raise ValueError('ward_reference_pi_analysis_not_complete')
    # Only this explicit completed match is transferred, not the entire Pi archive.
    script = '''import json,base64,hashlib
from pathlib import Path
p=PAYLOAD
root=Path(p['data']);catalog=json.loads((root/'catalog.json').read_text());r=catalog['matches'][p['match']]
settings=json.loads((root/'config.json').read_text());folder=Path(settings.get('analysis_directory',root/'analysis'))/p['match']
manifest=(folder/'manifest.json').read_bytes();m=json.loads(manifest)
if r.get('analysis_status')!='complete' or not m.get('complete') or m.get('source_sha256')!=r.get('sha256'):raise ValueError('incomplete_source')
f=folder/'wards.json'
if f.stat().st_size>2*1024**2:raise ValueError('ward_payload_budget')
raw=f.read_bytes()
if hashlib.sha256(raw).hexdigest()!=m['files']['wards.json']['sha256']:raise ValueError('ward_payload_changed')
if (folder/'manifest.json').read_bytes()!=manifest:raise ValueError('ward_manifest_changed')
print(json.dumps({'manifest':m,'wards_base64':base64.b64encode(raw).decode()}))
'''.replace('PAYLOAD',repr({'data':bridge.config['remote_data'],'match':identifier}))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    command = output/'fetch.py'
    command.write_text(script,encoding='utf-8')
    result = bridge.execute('python3 -', source=command, timeout=30)
    command.unlink()
    if result['manifest'].get('source_sha256') != record['sha256']:
        raise ValueError('ward_reference_catalog_source_changed')
    raw = base64.b64decode(result['wards_base64'], validate=True)
    packet = build(result['manifest'],raw)
    (output/'wards.json').write_bytes(raw)
    (output/'manifest.json').write_text(json.dumps(result['manifest'],ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'ward-reference.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return packet
