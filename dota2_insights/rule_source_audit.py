"""Audit declared rule sources against local bytes without exporting those bytes or paths."""
from collections import Counter
from datetime import datetime,timezone
import hashlib
from pathlib import Path

from .distribution_bundle import read_json
from .contracts.native_rules import validate_rules
from .minimap.offline_io import check_input_paths


def fingerprint(path):
    s=path.stat()
    return s.st_size,s.st_mtime_ns,s.st_ino


def hash_source(path):
    before=fingerprint(path)
    if before[0]>64*1024**2:raise ValueError('rule_source_file_budget')
    digest=hashlib.sha256();total=0
    with path.open('rb') as stream:
        while chunk:=stream.read(65536):
            total+=len(chunk)
            if total>64*1024**2:raise ValueError('rule_source_file_budget')
            digest.update(chunk)
    if fingerprint(path)!=before:raise ValueError('rule_source_changed_during_read')
    return digest.hexdigest(),total,before


def audit(rules_path,bindings_path):
    rules_path,bindings_path=Path(rules_path),Path(bindings_path)
    check_input_paths(rules_path,bindings_path)
    raw,rules_bytes=read_json(rules_path,4*1024**2)
    rules=validate_rules(raw)
    bindings,binding_bytes=read_json(bindings_path,512*1024)
    if (set(bindings)!={'schema_version','sources'} or type(bindings['schema_version']) is not int
            or bindings['schema_version']!=1 or not isinstance(bindings['sources'],list)
            or len(bindings['sources'])>1024):raise ValueError('rule_source_binding_schema')
    expected={s['source_id']:s for s in rules['sources']}
    mapped={}
    for row in bindings['sources']:
        if (not isinstance(row,dict) or set(row)!={'source_id','file'} or row['source_id'] not in expected
                or row['source_id'] in mapped or not isinstance(row['file'],str) or not row['file'].strip()
                or len(row['file'])>4096 or '://' in row['file'] or '\x00' in row['file']):
            raise ValueError('rule_source_binding_identity')
        path=Path(row['file'])
        if not path.is_absolute():path=bindings_path.resolve().parent/path
        mapped[row['source_id']]=path.resolve()
    cache,rows={},[]
    total=0
    for sid,source in expected.items():
        result={'source_id':sid,'kind':source['kind'],'declared_sha256':source['sha256']}
        path=mapped.get(sid)
        if path is None:
            rows.append({**result,'status':'binding_missing'});continue
        check_input_paths(path)
        try:
            if path not in cache:
                if total+path.stat().st_size>512*1024**2:raise ValueError('rule_source_total_budget')
                cache[path]=hash_source(path)
                total+=cache[path][1]
            actual,size,_=cache[path]
            result.update(status='byte_bound' if actual==source['sha256'] else 'source_hash_mismatch',
                actual_sha256=actual,bytes=size)
        except OSError:
            result.update(status='source_unavailable')
        rows.append(result)
    for path,(actual,_,before) in cache.items():
        if fingerprint(path)!=before or hash_source(path)[0]!=actual:
            raise ValueError('rule_source_changed_after_audit')
    if (read_json(rules_path,4*1024**2)[1]!=rules_bytes
            or read_json(bindings_path,512*1024)[1]!=binding_bytes):
        raise ValueError('rule_source_inputs_changed')
    counts=dict(Counter(r['status'] for r in rows))
    return {'schema_version':1,'kind':'rule_source_byte_audit',
        'checked_at_utc':datetime.now(timezone.utc).isoformat(),
        'ruleset_id':rules['ruleset_id'],'native_sha256':rules['sha256'],
        'rules_document_sha256':hashlib.sha256(rules_bytes).hexdigest(),
        'binding_document_sha256':hashlib.sha256(binding_bytes).hexdigest(),
        'counts':counts,'all_sources_byte_bound':len(rows)>0 and counts.get('byte_bound',0)==len(rows),
        'applies_to_game_version':rules['applies_to_game_version'],
        'semantic_version_verified':False,'mechanic_accuracy_verified':False,'activation_allowed':False,
        'unique_files_checked':len(cache),'unique_bytes_checked':total,'rows':rows,
        'meaning':'current_local_source_bytes_only_not_semantic_patch_or_mechanic_certification'}
