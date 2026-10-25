"""Fixed, bounded building-prefix job; explicit launch, poll and byte-preserving collect."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.pi_replay_library import Bridge

FILES = ('tools/extract_replay_building_landmarks.py', 'dota2_insights/replay_building_landmarks.py',
         'dota2_insights/postgame_target_names.py', 'dota2_insights/replay_archive.py')
sha = lambda raw: hashlib.sha256(raw).hexdigest()

WRAPPER = r'''import fcntl,hashlib,json,os,resource,subprocess,sys
from pathlib import Path
stage=Path(__file__).resolve().parent;job=json.loads((stage/'job.json').read_bytes())
sys.path.insert(0,job['runtime']);from library import Store
s=Store(job['state']);out=Path(job['output']);results=[]
resource.setrlimit(resource.RLIMIT_AS,(734003200,734003200))
def save(status):
 p=out/'status.json';q=out/'status.tmp';q.write_text(json.dumps({'status':status,'results':results,'pid':os.getpid(),'plan_sha256':job['plan_sha256']},indent=2));q.replace(p)
save('running')
for row in job['plan']['selected']:
 mid=row['match_id']
 with (s.root/'local_parser.lock').open('a') as lease:
  try:fcntl.flock(lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:save('yielded_parser_lock_busy_no_retry');raise SystemExit(0)
  catalog=s.catalog()
  if any(r.get('analysis_status')=='pending' for r in catalog['matches'].values()):save('yielded_to_natural_pending_no_retry');raise SystemExit(0)
  current=catalog['matches'].get(mid,{})
  if current.get('analysis_status')!='complete' or current.get('sha256')!=row['replay_sha256']:save('source_not_available_no_retry');raise SystemExit(2)
  for name,key in [('manifest.json','manifest_sha256'),('wards.json','wards_sha256')]:
   if hashlib.sha256((s.analysis/mid/name).read_bytes()).hexdigest()!=row[key]:save('canonical_baseline_changed');raise SystemExit(3)
  destination=out/('capture-'+mid+'.json')
  if destination.exists():save('existing_output_no_automatic_retry');raise SystemExit(4)
  env=os.environ.copy();env['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION']='python'
  argv=[sys.executable,str(stage/'tools/extract_replay_building_landmarks.py'),str(s.replays/(mid+'.dem')),'--match-id',mid,'--expected-sha256',row['replay_sha256'],'--output',str(destination),'--max-tick','5000','--max-seconds','120']
  try:
   run=subprocess.run(argv,env=env,capture_output=True,timeout=150)
   result={'match_id':mid,'returncode':run.returncode,'stderr_sha256':hashlib.sha256(run.stderr).hexdigest()}
  except subprocess.TimeoutExpired:results.append({'match_id':mid,'status':'wrapper_timeout_no_retry'});save('stopped');raise SystemExit(5)
  if run.returncode==0 and destination.exists():
   body=destination.read_bytes();d=json.loads(body);result.update(capture_sha256=hashlib.sha256(body).hexdigest(),records=len(d['records']),last_tick=d['last_tick'],stop_reason=d['stop_reason'],source_unchanged=d['source_unchanged'])
   result['status']='prefix_saved' if d['parse_error_type'] is None and d['source_unchanged'] and d['stop_reason']=='prefix_tick_limit_or_eof' else 'partial_requires_review'
  else:result['status']='failed_no_retry'
  results.append(result);save('running')
  if result['status']!='prefix_saved':save('stopped');raise SystemExit(6)
save('complete')
'''

REMOTE = r'''import base64,hashlib,json,subprocess,sys
from pathlib import Path
request=REQUEST
sys.path.insert(0,request['runtime']);from library import Store
s=Store(request['state']);digest=request['plan_sha256'];unit='dota2-building-prefix-'+digest[:12]
stage=Path(request['runtime'])/'building-prefix-stages'/digest[:12];out=s.analysis/('building-prefix-'+digest[:12])
def state():
 r=subprocess.run(['systemctl','--user','show',unit,'--property=MainPID,ExecMainStatus,ActiveState,SubState'],capture_output=True,text=True)
 return dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
if request['action']=='launch':
 if stage.exists() or out.exists() or state().get('MainPID','0')!='0':raise ValueError('existing_landmark_job_poll_do_not_restart')
 stage.mkdir(parents=True);out.mkdir()
 (stage/'dota2_insights').mkdir();(stage/'tools').mkdir();(stage/'dota2_insights/__init__.py').write_text('')
 (stage/'.replay-deps').symlink_to(Path(request['runtime'])/'.replay-deps',target_is_directory=True)
 for name,encoded in request['programs'].items():
  raw=base64.b64decode(encoded,validate=True)
  if hashlib.sha256(raw).hexdigest()!=request['plan']['source_code_sha256'][name]:raise ValueError('staged_code_hash')
  compile(raw,name,'exec');(stage/name).write_bytes(raw)
 wrapper=base64.b64decode(request['wrapper_base64'],validate=True);compile(wrapper,'wrapper.py','exec');(stage/'wrapper.py').write_bytes(wrapper)
 job={k:request[k] for k in ('runtime','state','plan','plan_sha256')};job['output']=str(out);(stage/'job.json').write_text(json.dumps(job,indent=2))
 subprocess.run(['systemd-run','--user','--unit='+unit,'--property=Nice=15','--property=CPUQuota=80%','--property=MemoryHigh=600M','--property=MemoryMax=700M','--property=LimitAS=700M','--property=RuntimeMaxSec=600',sys.executable,str(stage/'wrapper.py')],capture_output=True,check=True)
 print(json.dumps({'unit':unit,'plan_sha256':digest,'state':state(),'launched':True,'max_prefixes':len(request['plan']['selected'])}))
elif request['action']=='poll':
 saved=json.loads((out/'status.json').read_bytes()) if (out/'status.json').is_file() else None
 print(json.dumps({'unit':unit,'plan_sha256':digest,'state':state(),'saved_status':saved,'observation':'live_remote_systemd_poll'}))
elif request['action']=='collect':
 status=json.loads((out/'status.json').read_bytes());mid=request['match_id'];row=next(r for r in status['results'] if r['match_id']==mid)
 if row['status']!='prefix_saved':raise ValueError('incomplete_prefix_not_collectable')
 raw=(out/('capture-'+mid+'.json')).read_bytes()
 if len(raw)>1048576 or hashlib.sha256(raw).hexdigest()!=row['capture_sha256']:raise ValueError('capture_bytes_changed')
 print(json.dumps({'plan_sha256':digest,'match_id':mid,'capture_sha256':row['capture_sha256'],'capture_base64':base64.b64encode(raw).decode()}))
'''


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action', choices=('plan', 'launch', 'poll', 'collect'))
    cli.add_argument('--directory', type=Path, required=True)
    cli.add_argument('--context-plan', '--selection-plan', dest='selection_plan', type=Path)
    cli.add_argument('--match-id')
    args = cli.parse_args()
    out = args.directory.resolve()
    if not out.is_relative_to(ROOT/'diagnostics') or out == ROOT/'diagnostics':
        cli.error('diagnostics directory required')
    if args.action == 'plan':
        if out.exists() or args.selection_plan is None:
            cli.error('new directory and frozen source selection required')
        original = args.selection_plan.read_bytes()
        d = json.loads(original)
        if d['kind'] not in ('bounded_fixed_pi_context_expansion', 'fixed_building_prefix_candidate_selection') or not 1 <= len(d['selected']) <= 3:
            raise ValueError('fixed_original_source_selection_required')
        plan = {'schema_version': 1, 'kind': 'fixed_same_source_building_prefix_batch',
                'parent_selection_plan_sha256': sha(original), 'parent_selection_kind': d['kind'],
                'selected': d['selected'],
                'max_tick': 5000, 'max_seconds': 120, 'no_automatic_retry_or_replacement': True,
                'source_code_sha256': {name: sha((ROOT/name).read_bytes()) for name in FILES},
                'wrapper_sha256': sha(WRAPPER.encode())}
        out.mkdir(parents=True)
        (out/'plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8', newline='\n')
        (out/'source-code').mkdir()
        for name in FILES:
            (out/'source-code'/Path(name).name).write_bytes((ROOT/name).read_bytes())
        print(json.dumps({'status': 'fixed_plan_saved', 'prefixes': len(plan['selected'])}))
        return
    raw = (out/'plan.json').read_bytes()
    plan = json.loads(raw)
    if (plan['kind'] != 'fixed_same_source_building_prefix_batch' or not 1 <= len(plan['selected']) <= 3
            or any(not re.fullmatch(r'[0-9]{6,12}', r['match_id']) for r in plan['selected'])):
        raise ValueError('bounded_landmark_plan_required')
    bridge = Bridge()
    runtime = str(Path(bridge.config.get('remote_program', bridge.root+'/bin/library.py')).parent).replace('\\', '/')
    request = {'action': args.action, 'runtime': runtime,
               'state': bridge.config.get('remote_data', bridge.root+'/data'),
               'plan': plan, 'plan_sha256': sha(raw)}
    if args.action == 'launch':
        if plan['wrapper_sha256'] != sha(WRAPPER.encode()) or plan['source_code_sha256'] != {name: sha((ROOT/name).read_bytes()) for name in FILES}:
            raise ValueError('frozen_prefix_code_changed')
        request['programs'] = {name: base64.b64encode((ROOT/name).read_bytes()).decode() for name in FILES}
        request['wrapper_base64'] = base64.b64encode(WRAPPER.encode()).decode()
    if args.action == 'collect':
        if args.match_id not in {r['match_id'] for r in plan['selected']}:
            raise ValueError('only_frozen_match_collectable')
        request['match_id'] = args.match_id
    script = out/('remote-'+args.action+'.py')
    script.write_text(REMOTE.replace('REQUEST', repr(request), 1), encoding='utf-8', newline='\n')
    result = bridge.execute('python3 -', source=script, timeout=30)
    if args.action == 'collect':
        body = base64.b64decode(result.pop('capture_base64'), validate=True)
        if result['plan_sha256'] != sha(raw) or sha(body) != result['capture_sha256']:
            raise ValueError('collected_prefix_digest')
        path = out/('capture-'+args.match_id+'.json')
        with path.open('xb') as stream:
            stream.write(body)
    index = 1
    while (out/(args.action+'-'+str(index).zfill(4)+'.json')).exists():
        index += 1
    (out/(args.action+'-'+str(index).zfill(4)+'.json')).write_text(json.dumps(result, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
