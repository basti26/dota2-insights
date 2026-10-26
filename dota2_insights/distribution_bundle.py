"""Immutable offline delivery of native draft data, with no automatic activation."""
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import re

from .helper_handoff import utc
from .contracts.native_statistics import validate_bundle
from .contracts.native_rules import validate_rules

MAX_BYTES = 16*1024**2
KINDS = {'statistics':('draft_statistics',1),'rules':('draft_capability_rules',2)}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_json(path,limit=MAX_BYTES):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('distribution_duplicate_json_key')
            result[key]=value
        return result
    def invalid(_):raise ValueError('distribution_nonfinite_json')
    path=Path(path)
    with path.open('rb') as stream:raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('distribution_byte_budget')
    value=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=pairs,parse_constant=invalid)
    if not isinstance(value,dict):raise ValueError('distribution_object_required')
    return value,raw


def native(kind,payload,*,allow_synthetic=False):
    if kind=='statistics':return validate_bundle(payload,allow_synthetic=allow_synthetic)
    if kind=='rules':return validate_rules(payload,allow_synthetic=allow_synthetic)
    raise ValueError('distribution_kind_unsupported')


def native_format(kind,payload):
    return 'draft_statistics_v1' if kind=='statistics' else f"draft_capability_rules_v{payload['schema_version']}"


def validate_manifest(value):
    fields={'schema_version','kind','package_id','artifact_kind','native_format','created_at_utc',
            'payload','applicability','validation','generator'}
    if not isinstance(value,dict) or set(value)!=fields or type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('distribution_manifest_schema')
    if value['kind']!='insights_distribution_bundle' or value['artifact_kind'] not in KINDS:
        raise ValueError('distribution_kind_unsupported')
    artifact=value['artifact_kind']
    if value['native_format'] not in ({'draft_statistics_v1'} if artifact=='statistics' else {'draft_capability_rules_v1','draft_capability_rules_v2'}):
        raise ValueError('distribution_native_format_unsupported')
    utc(value['created_at_utc'])
    generator=value['generator']
    if (not isinstance(generator,dict) or set(generator)!={'name','source_sha256'}
            or generator['name']!='insights_distribution_v1' or not isinstance(generator['source_sha256'],str)
            or not re.fullmatch('[a-f0-9]{64}',generator['source_sha256'])):
        raise ValueError('distribution_generator_schema')
    payload=value['payload']
    if (not isinstance(payload,dict) or set(payload)!={'file','bytes','sha256','native_sha256','source_document_sha256'}
            or payload['file']!='payload.json' or type(payload['bytes']) is not int or not 0<payload['bytes']<=MAX_BYTES
            or any(not isinstance(payload[k],str) or not re.fullmatch('[a-f0-9]{64}',payload[k]) for k in ('sha256','native_sha256','source_document_sha256'))):
        raise ValueError('distribution_payload_reference')
    expected=digest(canonical({k:v for k,v in value.items() if k!='package_id'}))
    if value['package_id']!=expected:raise ValueError('distribution_manifest_checksum')
    return value


def applicability(kind,payload):
    if kind=='statistics':
        return {'semantic_game_version':payload['scope']['game_version'],'scope':payload['scope'],
            'coverage_start_utc':payload['coverage_start_utc'],'coverage_end_utc':payload['coverage_end_utc'],
            'version_evidence':'provider_scope_declaration','source_build':None}
    return {'semantic_game_version':payload['applies_to_game_version'],
        'scope':{'purpose':'draft_capabilities','criteria_version':payload['criteria_version']},
        'coverage_start_utc':None,'coverage_end_utc':None,
        'version_evidence':'native_rule_version_evidence' if payload['version_evidence'] else 'not_verified',
        'source_build':payload['source_build']}


def publish(kind,source,output,*,created_at_utc=None,allow_synthetic=False):
    from .minimap.offline_io import check_input_paths
    from .history import checked_output
    root=Path(__file__).resolve().parents[1]
    if kind not in KINDS:raise ValueError('distribution_kind_unsupported')
    source,output=Path(source),checked_output(root,output)
    check_input_paths(source)
    raw,before=read_json(source,4*1024**2 if kind=='rules' else MAX_BYTES)
    payload=native(kind,raw,allow_synthetic=allow_synthetic)
    encoded=canonical(payload)+b'\n'
    if len(encoded)>MAX_BYTES:raise ValueError('distribution_byte_budget')
    fmt=native_format(kind,payload)
    module=Path(__file__)
    synthetic=payload.get('provider')=='synthetic' or payload.get('source_kind')=='test_fixture'
    manifest={'schema_version':1,'kind':'insights_distribution_bundle','artifact_kind':kind,
        'native_format':fmt,'created_at_utc':created_at_utc or datetime.now(timezone.utc).isoformat(),
        'payload':{'file':'payload.json','bytes':len(encoded),'sha256':digest(encoded),
            'native_sha256':payload['sha256'],'source_document_sha256':digest(before)},
        'applicability':applicability(kind,payload),
        'validation':{'native_structure':'passed','source_byte_binding':'not_performed_by_transport',
            'semantic_accuracy':'not_certified','synthetic':synthetic,'automatic_activation':False},
        'generator':{'name':'insights_distribution_v1','source_sha256':digest(module.read_bytes())}}
    manifest['package_id']=digest(canonical(manifest))
    validate_manifest(manifest)
    source_time=payload['downloaded_at_utc'] if kind=='statistics' else payload['reviewed_at_utc']
    if utc(source_time)>utc(manifest['created_at_utc']):raise ValueError('distribution_source_time_follows_package')
    # Payload source must still be the exact inspected bytes before publication.
    if read_json(source)[1]!=before:raise ValueError('distribution_source_changed')
    output.mkdir(parents=True,exist_ok=False)
    with (output/'payload.json').open('xb') as stream:stream.write(encoded)
    with (output/'manifest.json').open('xb') as stream:stream.write(canonical(manifest)+b'\n')
    # A missing COMPLETE means an interrupted package, never an accepted delivery.
    (output/'COMPLETE').write_bytes((manifest['package_id']+'\n').encode('ascii'))
    return manifest


def package_file(folder,name):
    path=(folder/name).resolve()
    if path.parent!=folder:raise ValueError('distribution_file_path_escape')
    from .minimap.offline_io import check_input_paths
    check_input_paths(path)
    return path


def inspect(folder,*,game_version=None,expected_scope=None,now_utc=None,max_age_s=86400,rule_source_bindings=None):
    folder=Path(folder).resolve()
    from .minimap.offline_io import check_input_paths
    check_input_paths(folder)
    manifest,_=read_json(package_file(folder,'manifest.json'),256*1024)
    validate_manifest(manifest)
    with package_file(folder,'COMPLETE').open('rb') as stream: complete=stream.read(81)
    if complete not in {(manifest['package_id']+'\n').encode('ascii'),(manifest['package_id']+'\r\n').encode('ascii')}:
        raise ValueError('distribution_incomplete_or_changed')
    payload_path=package_file(folder,'payload.json')
    payload,raw=read_json(payload_path)
    if len(raw)!=manifest['payload']['bytes'] or digest(raw)!=manifest['payload']['sha256']:
        raise ValueError('distribution_payload_checksum')
    # Fixture documents can be inspected, but can never be eligible for a live consumer.
    checked=native(manifest['artifact_kind'],payload,allow_synthetic=True)
    if (checked['sha256']!=manifest['payload']['native_sha256']
            or applicability(manifest['artifact_kind'],checked)!=manifest['applicability']
            or native_format(manifest['artifact_kind'],checked)!=manifest['native_format']):
        raise ValueError('distribution_payload_metadata_mismatch')
    source_time=checked['downloaded_at_utc'] if manifest['artifact_kind']=='statistics' else checked['reviewed_at_utc']
    if utc(source_time)>utc(manifest['created_at_utc']):raise ValueError('distribution_source_time_follows_package')
    validation=manifest['validation']
    if (not isinstance(validation,dict) or set(validation)!={'native_structure','source_byte_binding','semantic_accuracy','synthetic','automatic_activation'}
            or validation['native_structure']!='passed' or validation['automatic_activation'] is not False
            or validation['source_byte_binding']!='not_performed_by_transport' or validation['semantic_accuracy']!='not_certified'):
        raise ValueError('distribution_invalid_validation_claim')
    synthetic=checked.get('provider')=='synthetic' or checked.get('source_kind')=='test_fixture'
    if type(validation['synthetic']) is not bool or synthetic!=validation['synthetic']:
        raise ValueError('distribution_synthetic_claim_mismatch')
    version=manifest['applicability']['semantic_game_version']
    now=utc(now_utc) if now_utc else datetime.now(timezone.utc)
    if type(max_age_s) not in (int,float) or not math.isfinite(max_age_s) or max_age_s<=0:
        raise ValueError('distribution_max_age_invalid')
    if game_version is not None and (not isinstance(game_version,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,79}',game_version)):
        raise ValueError('distribution_game_version_invalid')
    if expected_scope is not None and (not isinstance(expected_scope,dict) or not expected_scope
            or set(expected_scope)-{'game_mode','rank','region'}
            or any(not isinstance(v,str) or not v.strip() or len(v)>100 for v in expected_scope.values())):
        raise ValueError('distribution_expected_scope_invalid')
    source_bytes_verified=False
    if rule_source_bindings is not None:
        if manifest['artifact_kind']!='rules':raise ValueError('distribution_rule_bindings_for_wrong_kind')
        from .rule_source_audit import audit
        source_audit=audit(payload_path,rule_source_bindings)
        source_bytes_verified=source_audit['all_sources_byte_bound']
    if synthetic:status='synthetic_not_for_live_use'
    elif now<utc(manifest['created_at_utc']):status='package_clock_skew'
    elif version is None or game_version is None:status='semantic_version_unverified'
    elif version!=game_version:status='semantic_version_mismatch'
    elif expected_scope is not None and any(checked.get('scope',{}).get(k)!=v for k,v in expected_scope.items()):status='scope_mismatch'
    elif manifest['artifact_kind']=='statistics':
        age=(now-utc(checked['coverage_end_utc'])).total_seconds()
        status='coverage_clock_skew' if age<0 else 'stale_statistics' if age>max_age_s else 'eligible_for_native_consumer_validation'
    else:status='eligible_for_native_consumer_validation' if source_bytes_verified else 'source_binding_required_before_native_consumer_validation'
    return {'schema_version':1,'package_id':manifest['package_id'],'artifact_kind':manifest['artifact_kind'],
        'native_format':manifest['native_format'],'transport_verified':True,'compatibility_status':status,
        'activation_allowed':False,'native_consumer_validation_required':True,
        'source_bytes_verified':source_bytes_verified,
        'semantic_game_version':version,'meaning':'verified_delivery_bytes_not_live_activation_or_quality_certification'}
