"""Static, bounded source closure for future Pi deployment; never connects to a host."""
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
ALIASES={'library':'dota2_insights/replay_library_store.py',
    'pi_ward_worker':'tools/pi_ward_worker.py','extract_replay_wards':'tools/extract_replay_wards.py'}


def build_plan(root=ROOT):
    root=Path(root).resolve()
    queue=list(ALIASES)
    seen=set();files={};external=set()
    def source_for(name):
        if name in ALIASES:return root/ALIASES[name],name+'.py'
        base=root.joinpath(*name.split('.'))
        if base.with_suffix('.py').is_file():return base.with_suffix('.py'),name.replace('.','/')+'.py'
        if (base/'__init__.py').is_file():return base/'__init__.py',name.replace('.','/')+'/__init__.py'
        raise ValueError('pi_deployment_internal_module_missing:'+name)
    def enqueue(name):
        if name in ALIASES or name.startswith(('dota2_insights','tools')):
            source_for(name)
            if name not in seen:queue.append(name)
        elif name:
            top=name.split('.')[0]
            if top not in sys.stdlib_module_names and top!='__future__':external.add(top)
    while queue:
        name=queue.pop()
        if name in seen:continue
        seen.add(name)
        source,destination=source_for(name)
        resolved=source.resolve(strict=True)
        if not resolved.is_relative_to(root):raise ValueError('pi_deployment_source_outside_repo')
        raw=resolved.read_bytes()
        if len(raw)>512*1024:raise ValueError('pi_deployment_source_budget')
        tree=ast.parse(raw.decode('utf-8-sig'),filename=destination)
        files[destination]={'source':source.relative_to(root).as_posix(),'bytes':len(raw),
            'sha256':hashlib.sha256(raw).hexdigest()}
        if len(files)>96 or sum(r['bytes'] for r in files.values())>4*1024**2:
            raise ValueError('pi_deployment_bundle_budget')
        if name.startswith(('dota2_insights','tools')):
            parts=name.split('.')
            for i in range(1,len(parts)):enqueue('.'.join(parts[:i]))
        package=name.split('.') if source.name=='__init__.py' else name.split('.')[:-1]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                for alias in node.names:enqueue(alias.name)
            elif isinstance(node,ast.ImportFrom):
                if node.level:
                    if node.level>len(package):raise ValueError('pi_deployment_relative_import_escape')
                    prefix=package[:len(package)-node.level+1]
                    module='.'.join(prefix+([node.module] if node.module else []))
                else:module=node.module or ''
                enqueue(module)
                for alias in node.names:
                    child=module+'.'+alias.name
                    if child.startswith(('dota2_insights.','tools.')):
                        base=root.joinpath(*child.split('.'))
                        if base.with_suffix('.py').is_file() or (base/'__init__.py').is_file():enqueue(child)
    locks=root/'data/validation-locks.json'
    raw=locks.read_bytes()
    value=json.loads(raw)
    if (len(raw)>16384 or value.get('schema_version')!=1 or type(value['schema_version']) is not int
            or not isinstance(value.get('heldout_matches'),list)
            or any(not isinstance(mid,str) or not mid.isascii() or not mid.isdigit() for mid in value['heldout_matches'])):
        raise ValueError('pi_deployment_public_locks_invalid')
    files['data/validation-locks.json']={'source':'data/validation-locks.json',
        'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    return {'schema_version':1,'kind':'pi_managed_deployment_plan','files':dict(sorted(files.items())),
        'external_import_roots':sorted(external),'remote_deploy_executed':False,
        'third_party_runtime_verified_on_pi':False,'credentials_included':False,
        'meaning':'static_source_and_public_lock_closure_not_ARM_runtime_or_remote_acceptance'}


def deployment_sources(root=ROOT):
    root=Path(root).resolve()
    plan=build_plan(root)
    result={}
    for destination,ref in plan['files'].items():
        path=(root/ref['source']).resolve(strict=True)
        if not path.is_relative_to(root):raise ValueError('pi_deployment_source_outside_repo')
        raw=path.read_bytes()
        if len(raw)!=ref['bytes'] or hashlib.sha256(raw).hexdigest()!=ref['sha256']:
            raise ValueError('pi_deployment_sources_changed')
        result[destination]=raw
    return result
