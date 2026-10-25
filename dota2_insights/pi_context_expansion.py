"""Small, fixed, serial replay-context backfills on the dedicated Pi."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3

from .pi_replay_library import Bridge,ROOT
from .postgame_store import HEX,bounded,mid,sha

FILES={'extract_replay_wards.py':ROOT/'tools/extract_replay_wards.py',
       'dota2_insights/replay_archive.py':ROOT/'dota2_insights/replay_archive.py',
       'dota2_insights/replay_postgame_context.py':ROOT/'dota2_insights/replay_postgame_context.py'}
SEED='postgame-context-expansion-v1-20261021'


def plan(database,output,*,count=3,seed=SEED,game_build=None):
    database,output=Path(database).resolve(),Path(output).resolve()
    if output.exists() or not 1<=count<=3 or not isinstance(seed,str) or len(seed)>100:
        raise ValueError('context_expansion_new_plan_and_count1_to3_required')
    if game_build is not None and (type(game_build) is not int or game_build<=0):
        raise ValueError('context_expansion_positive_build_filter_required')
    raw=bounded(database,128*1024**2)
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        cohort=c.execute("SELECT producer_cohort,COUNT(*) n FROM matches WHERE source_group='d2pt' GROUP BY producer_cohort ORDER BY n DESC,producer_cohort LIMIT 1").fetchone()
        if cohort is None:raise ValueError('context_expansion_no_d2pt_cohort')
        rows=[dict(r) for r in c.execute("""SELECT m.match_id,m.replay_sha256,m.wards_sha256,m.duration_s,m.producer_cohort,
            s.sha256 manifest_sha256 FROM matches m JOIN sources s ON s.match_id=m.match_id AND s.kind='manifest.json'
            LEFT JOIN context_sources x ON x.match_id=m.match_id WHERE m.source_group='d2pt' AND m.producer_cohort=? AND x.source_id IS NULL""",(cohort['producer_cohort'],))]
        build_evidence=None
        if game_build is not None:
            from .postgame_metadata_store import resolved_metadata
            resolved=resolved_metadata(c)
            build_evidence=[{'match_id':r['match_id'],'resolved_game_build':resolved[r['match_id']]['game_build'],
                             'build_sources':resolved[r['match_id']]['field_sources']['game_build']}
                            for r in sorted(rows,key=lambda r:r['match_id'])]
            rows=[r for r in rows if resolved[r['match_id']]['game_build']==game_build]
    finally:c.close()
    for r in rows:r['selection_hash']=sha((seed+':'+r['match_id']).encode())
    rows.sort(key=lambda r:(r['selection_hash'],r['match_id']))
    if len(rows)<count or sha(bounded(database,128*1024**2))!=sha(raw):raise ValueError('context_expansion_candidate_or_source_changed')
    result={'schema_version':1,'kind':'bounded_fixed_pi_context_expansion','database_sha256':sha(raw),'seed':seed,
        'selection':'lowest_hash_of_main_d2pt_producer_cohort_without_selected_context_no_outcome_or_target_filter',
        'candidate_count':len(rows),'selected':rows[:count],'ranked_candidates':rows,'max_parses':count,
        'per_parse_max_seconds':2700,'per_parse_wrapper_max_seconds':2820,'cpu_quota_percent':80,
        'memory_high_mib':600,'memory_max_mib':700,'sampled_rss_stop_mib':600,'serial_only':True,
        'overwrite_canonical':False,'download_quota_unchanged':True,'scope':'retrospective_context_expansion_not_pristine_holdout',
        'source_code_sha256':{n:sha(p.read_bytes()) for n,p in FILES.items()}}
    if game_build is not None:
        result.update({'selection':'lowest_hash_of_explicit_resolved_build_in_main_d2pt_cohort_without_selected_context',
                       'resolved_game_build_filter':game_build,'build_candidate_decisions':build_evidence,
                       'unfiltered_candidate_count':len(build_evidence),
                       'filter_is_retrospective_known_build_population_not_random_all_matches':True})
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return result


WRAPPER=r'''import fcntl,hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
stage=Path(__file__).resolve().parent
job=json.loads((stage/'job.json').read_text())
sys.path.insert(0,job['runtime'])
from library import Store
s=Store(job['state']);out=Path(job['output']);plan=job['plan'];results=[]
def write(path,value):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def replay_sha(path):
 digest=hashlib.sha256()
 with path.open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
 return digest.hexdigest()
write(out/'batch-status.json',{'status':'started','pid':os.getpid(),'selected':[r['match_id'] for r in plan['selected']]})
for row in plan['selected']:
 mid=row['match_id'];folder=out/mid
 if (folder/'result.json').is_file():
  previous=json.loads((folder/'result.json').read_text())
  if previous.get('status')!='complete' or previous.get('source_sha256')!=row['replay_sha256'] or sha(folder/'export/wards.json')!=row['wards_sha256']:
   raise ValueError('context_expansion_existing_result_not_reusable')
  if sha(s.analysis/mid/'manifest.json')!=row['manifest_sha256'] or sha(s.analysis/mid/'wards.json')!=row['wards_sha256']:
   raise ValueError('context_expansion_completed_baseline_changed')
  results.append(previous);continue
 if (folder/'export').exists():raise ValueError('context_expansion_partial_parse_requires_review_no_retry')
 folder.mkdir(exist_ok=True)
 with (s.root/'local_parser.lock').open('a') as lease:
  wait_start=time.monotonic()
  while True:
   try:fcntl.flock(lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);break
   except BlockingIOError:
    elapsed=time.monotonic()-wait_start
    if elapsed>=60:
     write(out/'batch-status.json',{'status':'stopped_lock_busy_no_automatic_retry','results':results,'not_started_from':mid});raise SystemExit(3)
    write(out/'batch-status.json',{'status':'waiting_for_parser_lock','pid':os.getpid(),'not_started_from':mid,'wait_elapsed_s':round(elapsed,2),'results':results})
    time.sleep(1)
  catalog=s.catalog()
  if any(r.get('analysis_status')=='pending' for r in catalog['matches'].values()):
   write(out/'batch-status.json',{'status':'yielded_to_pending_natural_queue','results':results,'not_started_from':mid});raise SystemExit(0)
  canonical=s.analysis/mid;before={name:sha(canonical/name) for name in ('manifest.json','wards.json')}
  current=catalog['matches'].get(mid,{})
  if current.get('sha256')!=row['replay_sha256'] or current.get('analysis_status')!='complete' or before!={'manifest.json':row['manifest_sha256'],'wards.json':row['wards_sha256']}:
   raise ValueError('context_expansion_canonical_source_changed')
  # Catalog/size/stat equality cannot prove DEM bytes are still the same.
  actual_source=replay_sha(s.replays/(mid+'.dem'))
  if actual_source!=row['replay_sha256']:
   write(out/'batch-status.json',{'status':'stopped_source_bytes_changed_before_parse',
    'results':results,'not_started_from':mid,'expected_source_sha256':row['replay_sha256'],
    'observed_source_sha256':actual_source,'automatic_retry':False})
   raise SystemExit(4)
  environment=os.environ.copy();environment['DOTA2_REPLAY_RUNTIME']=str(stage);environment['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION']='python'
  start=time.monotonic();samples=0;max_rss=0;max_hwm=0;reason=None;process=None
  try:
   with (folder/'parse.log').open('ab') as log,(folder/'resource-samples.jsonl').open('a',encoding='utf-8') as resource:
    process=subprocess.Popen([sys.executable,str(stage/'extract_replay_wards.py'),str(s.replays/(mid+'.dem')),
      '--output',str(folder/'export'),'--max-seconds',str(plan['per_parse_max_seconds']),'--with-context'],
      env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
    while process.poll() is None:
     elapsed=time.monotonic()-start;rss=hwm=None
     try:
      fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/'+str(process.pid)+'/status').read_text().splitlines() if ':' in line}
      rss=int(fields['VmRSS'].split()[0]);hwm=int(fields['VmHWM'].split()[0]);max_rss=max(max_rss,rss);max_hwm=max(max_hwm,hwm)
     except (OSError,KeyError,ValueError):pass
     sample={'elapsed_s':round(elapsed,3),'child_pid':process.pid,'rss_kib':rss,'hwm_kib':hwm}
     resource.write(json.dumps(sample)+'\n');resource.flush();samples+=1
     progress={}
     try:progress=json.loads((folder/'export/progress.json').read_text())
     except (OSError,ValueError):pass
     write(out/'batch-status.json',{'status':'parsing','pid':os.getpid(),'current_match_id':mid,'child_pid':process.pid,
       'elapsed_s':round(elapsed,3),'progress':progress,'resource_sample':sample,'results':results})
     if elapsed>plan['per_parse_wrapper_max_seconds']:reason='wrapper_wall_limit';break
     if rss is not None and rss>plan['sampled_rss_stop_mib']*1024:reason='sampled_memory_limit';break
     time.sleep(10)
    if reason and process.poll() is None:
     process.terminate()
     try:process.wait(timeout=10)
     except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
   manifest={}
   try:manifest=json.loads((folder/'export/manifest.json').read_text())
   except (OSError,ValueError):pass
   old=json.loads((canonical/'manifest.json').read_text())
   unchanged=before=={name:sha(canonical/name) for name in before}
   same_wards=(folder/'export/wards.json').is_file() and sha(folder/'export/wards.json')==row['wards_sha256']
   complete=process.returncode==0 and manifest.get('complete') is True and manifest.get('source_sha256')==row['replay_sha256'] and unchanged and same_wards and manifest.get('game_clock')==old.get('game_clock')
   result={'match_id':mid,'parser_unit':job['unit'],'status':'complete' if complete else 'incomplete_or_not_bound','returncode':process.returncode,
      'stop_reason':reason or manifest.get('stop_reason'),'manifest_complete':manifest.get('complete'),
      'elapsed_s':round(time.monotonic()-start,3),'canonical_unchanged':unchanged,'ward_bytes_equal':same_wards,
      'game_clock_equal':manifest.get('game_clock')==old.get('game_clock'),'source_sha256':manifest.get('source_sha256'),
      'resource_samples':samples,'max_sampled_rss_kib':max_rss,'max_observed_hwm_kib':max_hwm}
   write(folder/'result.json',result);results.append(result)
   if not complete:
    write(out/'batch-status.json',{'status':'stopped_after_incomplete_source','results':results});raise SystemExit(2)
  finally:
   if process is not None and process.poll() is None:
    process.terminate()
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
write(out/'batch-status.json',{'status':'complete','results':results,'canonical_results_modified':False})
'''


REMOTE=r'''import base64,hashlib,json,os,subprocess,sys
from pathlib import Path
request=REQUEST
sys.path.insert(0,request['runtime'])
from library import Store
s=Store(request['state']);plan=request['plan'];identifier=request['plan_sha256'][:12]
stage=s.root/('context-expansion-'+identifier);out=s.analysis/('context-expansion-'+identifier);unit='dota2-context-expansion-'+identifier
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def state():
 raw=subprocess.run(['systemctl','--user','show',unit,'--property=ActiveState,SubState,MainPID,ExecMainStatus,MemoryCurrent,CPUUsageNSec'],capture_output=True,text=True)
 if raw.returncode:raise ValueError('context_expansion_unit_unavailable')
 return dict(line.split('=',1) for line in raw.stdout.splitlines() if '=' in line)
if request['action']=='launch':
 if stage.exists() or out.exists():raise ValueError('context_expansion_attempt_exists_poll_existing_handle')
 for p in Path('/proc').iterdir():
  try:
   if not p.name.isdecimal() or p.stat().st_uid!=os.getuid():continue
   cmd=(p/'cmdline').read_bytes().split(b'\0')
   if any(b'extract_replay_wards.py' in x or b'context-pilot-wrapper.py' in x or b'context-expansion-wrapper.py' in x for x in cmd):raise RuntimeError('context_expansion_parser_already_running')
  except (OSError,ValueError):pass
 for name,digest in plan['source_code_sha256'].items():
  if sha(Path(request['runtime'])/name)!=digest:raise ValueError('context_expansion_deployed_code_changed')
 catalog=s.catalog()
 for row in plan['selected']:
  mid=row['match_id'];saved=catalog['matches'].get(mid,{})
  if saved.get('sha256')!=row['replay_sha256'] or saved.get('analysis_status')!='complete' or not (s.replays/(mid+'.dem')).is_file():raise ValueError('context_expansion_candidate_not_available')
  for name,digest in [('manifest.json',row['manifest_sha256']),('wards.json',row['wards_sha256'])]:
   if sha(s.analysis/mid/name)!=digest:raise ValueError('context_expansion_baseline_bytes_changed')
 stage.mkdir();out.mkdir();(stage/'dota2_insights').mkdir();(stage/'dota2_insights/__init__.py').write_text('')
 (stage/'.replay-deps').symlink_to(Path(request['runtime'])/'.replay-deps',target_is_directory=True)
 for name,encoded in request['programs'].items():
  body=base64.b64decode(encoded,validate=True)
  if hashlib.sha256(body).hexdigest()!=plan['source_code_sha256'][name]:raise ValueError('context_expansion_stage_code_digest')
  compile(body,name,'exec');(stage/name).write_bytes(body)
 wrapper=base64.b64decode(request['wrapper_base64'],validate=True);compile(wrapper,'context-expansion-wrapper.py','exec')
 (stage/'context-expansion-wrapper.py').write_bytes(wrapper)
 job={k:request[k] for k in ('plan','plan_sha256','runtime','state')};job.update(output=str(out),unit=unit)
 (stage/'job.json').write_text(json.dumps(job,indent=2),encoding='utf-8')
 subprocess.run(['systemd-run','--user','--unit='+unit,'--property=Nice=15','--property=CPUQuota=80%',
  '--property=MemoryHigh=600M','--property=MemoryMax=700M','--property=LimitAS=700M','--property=RuntimeMaxSec='+str(2820*len(plan['selected'])+300),
  '--property=StandardOutput=append:'+str(out/'wrapper.log'),'--property=StandardError=append:'+str(out/'wrapper.log'),
  sys.executable,str(stage/'context-expansion-wrapper.py')],check=True,capture_output=True)
 print(json.dumps({'unit':unit,'state':state(),'plan_sha256':request['plan_sha256'],'max_parses':len(plan['selected']),
  'canonical_results_modified':False,'wrapper_sha256':hashlib.sha256(wrapper).hexdigest()}))
elif request['action']=='resume':
 saved=json.loads((stage/'job.json').read_text())
 if saved['plan_sha256']!=request['plan_sha256']:raise ValueError('context_expansion_resume_identity')
 unit=saved['unit'];old_state=state()
 if int(old_state.get('MainPID','0')) or old_state.get('ActiveState') in ('active','activating','deactivating'):
  raise ValueError('context_expansion_original_unit_still_live_poll_it')
 batch=json.loads((out/'batch-status.json').read_text())
 if batch.get('status') not in ('stopped_lock_busy_no_automatic_retry','yielded_to_pending_natural_queue'):
  raise ValueError('context_expansion_resume_only_verified_not_started_work')
 for p in Path('/proc').iterdir():
  try:
   if not p.name.isdecimal() or p.stat().st_uid!=os.getuid():continue
   cmd=(p/'cmdline').read_bytes()
   if b'extract_replay_wards.py' in cmd or b'context-expansion-wrapper.py' in cmd:raise RuntimeError('context_expansion_another_parser_active')
  except (OSError,ValueError):pass
 completed={};remaining=[]
 for row in plan['selected']:
  mid=row['match_id'];folder=out/mid
  if (folder/'result.json').is_file():
   r=json.loads((folder/'result.json').read_text())
   if r.get('status')!='complete' or sha(folder/'export/wards.json')!=row['wards_sha256']:raise ValueError('context_expansion_incomplete_result_no_retry')
   completed[mid]=r.get('parser_unit') or saved.get('completed_source_units',{}).get(mid,unit)
  else:
   if (folder/'export').exists():raise ValueError('context_expansion_partial_output_no_retry')
   remaining.append(mid)
 for name,digest in plan['source_code_sha256'].items():
  if sha(stage/name)!=digest:raise ValueError('context_expansion_resume_parser_code_changed')
 history=saved.get('attempts',[])
 if not remaining or len(history)>=3:raise ValueError('context_expansion_resume_nothing_pending_or_attempt_limit')
 attempts=out/'attempts';attempts.mkdir(exist_ok=True)
 (attempts/('state-'+str(len(history))+'.json')).write_text(json.dumps({'unit':unit,'state':old_state,'batch':batch},indent=2))
 old_wrapper=(stage/'context-expansion-wrapper.py').read_bytes()
 (attempts/('wrapper-'+hashlib.sha256(old_wrapper).hexdigest()+'.py')).write_bytes(old_wrapper)
 history.append({'unit':unit,'terminal_state':old_state,'completed':list(completed),'not_started':remaining})
 unit='dota2-context-expansion-'+identifier+'-resume-'+str(len(history))
 wrapper=base64.b64decode(request['wrapper_base64'],validate=True);compile(wrapper,'context-expansion-wrapper.py','exec')
 (stage/'context-expansion-wrapper.py').write_bytes(wrapper)
 saved.update(unit=unit,attempts=history,completed_source_units=completed)
 temporary=stage/'job.tmp';temporary.write_text(json.dumps(saved,indent=2),encoding='utf-8');temporary.replace(stage/'job.json')
 subprocess.run(['systemd-run','--user','--unit='+unit,'--property=Nice=15','--property=CPUQuota=80%',
  '--property=MemoryHigh=600M','--property=MemoryMax=700M','--property=LimitAS=700M',
  '--property=RuntimeMaxSec='+str(2820*len(remaining)+300),
  '--property=StandardOutput=append:'+str(out/'wrapper.log'),'--property=StandardError=append:'+str(out/'wrapper.log'),
  sys.executable,str(stage/'context-expansion-wrapper.py')],check=True,capture_output=True)
 print(json.dumps({'unit':unit,'state':state(),'plan_sha256':request['plan_sha256'],'completed_sources_reused':list(completed),
  'not_started_sources_resumed':remaining,'wrapper_sha256':hashlib.sha256(wrapper).hexdigest(),'canonical_results_modified':False}))
elif request['action']=='poll':
 saved=json.loads((stage/'job.json').read_text())
 if saved['plan_sha256']!=request['plan_sha256']:raise ValueError('context_expansion_job_identity_changed')
 unit=saved['unit']
 result={'unit':unit,'state':state(),'plan_sha256':saved['plan_sha256']}
 if (out/'batch-status.json').is_file():result['batch']=json.loads((out/'batch-status.json').read_text())
 print(json.dumps(result))
elif request['action']=='collect':
 saved=json.loads((stage/'job.json').read_text());mid=request['match_id']
 if saved['plan_sha256']!=request['plan_sha256'] or mid not in {r['match_id'] for r in plan['selected']}:raise ValueError('context_expansion_export_identity')
 folder=out/mid;result=json.loads((folder/'result.json').read_text());manifest=json.loads((folder/'export/manifest.json').read_text())
 unit=result.get('parser_unit') or saved.get('completed_source_units',{}).get(mid,saved['unit'])
 if result['status']!='complete' or not result['canonical_unchanged'] or not result['ward_bytes_equal']:raise ValueError('context_expansion_export_not_bound')
 raws={name:(folder/'export'/name).read_bytes() for name in ('manifest.json','wards.json','postgame_context.json')}
 for name in ('wards.json','postgame_context.json'):
  if sha(folder/'export'/name)!=manifest['files'][name]['sha256']:raise ValueError('context_expansion_export_file_changed')
 from datetime import datetime,timezone
 value={'schema_version':1,'kind':'isolated_postgame_capture_export','frozen_at_utc':datetime.now(timezone.utc).isoformat(),
  'unit':unit,'expansion_plan_sha256':request['plan_sha256'],
  **{key:base64.b64encode(raws[name]).decode() for key,name in [('manifest_base64','manifest.json'),('wards_base64','wards.json'),('context_base64','postgame_context.json')]}}
 body=json.dumps(value).encode()
 if len(body)>20*1024**2:raise ValueError('context_expansion_export_size_limit')
 sys.stdout.buffer.write(body)
else:raise ValueError('context_expansion_action_unknown')
'''


def request(plan_path,action,*,match_id=None,output=None):
    plan_path=Path(plan_path).resolve();raw=bounded(plan_path,256*1024);value=json.loads(raw)
    if (value.get('kind')!='bounded_fixed_pi_context_expansion' or value.get('schema_version')!=1
            or not 1<=len(value['selected'])<=3 or value.get('max_parses')!=len(value['selected'])
            or value.get('per_parse_max_seconds')!=2700 or value.get('per_parse_wrapper_max_seconds')!=2820
            or value.get('sampled_rss_stop_mib')!=600):raise ValueError('context_expansion_plan_limits')
    if (value.get('serial_only') is not True or value.get('overwrite_canonical') is not False
            or not isinstance(value.get('seed'),str) or len(value['seed'])>100
            or not HEX.fullmatch(str(value.get('database_sha256','')))
            or not 1<=value.get('candidate_count',0)<=256
            or len(value.get('ranked_candidates',[]))!=value['candidate_count']):
        raise ValueError('context_expansion_plan_identity')
    ranked=value['ranked_candidates'];identifiers=[]
    for row in ranked:
        identifiers.append(mid(row['match_id']))
        if (any(not HEX.fullmatch(str(row.get(k,''))) for k in ('replay_sha256','wards_sha256','manifest_sha256','producer_cohort'))
                or row.get('selection_hash')!=sha((value['seed']+':'+row['match_id']).encode())):
            raise ValueError('context_expansion_candidate_identity')
    if (len(set(identifiers))!=len(identifiers) or ranked!=sorted(ranked,key=lambda r:(r['selection_hash'],r['match_id']))
            or value['selected']!=ranked[:value['max_parses']]):raise ValueError('context_expansion_fixed_ranking_required')
    if 'resolved_game_build_filter' in value:
        build=value['resolved_game_build_filter'];decisions=value.get('build_candidate_decisions',[])
        if (type(build) is not int or build<=0 or not 1<=len(decisions)<=256
                or value.get('unfiltered_candidate_count')!=len(decisions)
                or len({r['match_id'] for r in decisions})!=len(decisions)
                or {r['match_id'] for r in decisions if r['resolved_game_build']==build}!=set(identifiers)
                or value.get('selection')!='lowest_hash_of_explicit_resolved_build_in_main_d2pt_cohort_without_selected_context'):
            raise ValueError('context_expansion_build_population_binding')
    bridge=Bridge();runtime=str(Path(bridge.config.get('remote_program',bridge.root+'/bin/library.py')).parent).replace('\\','/')
    data={'action':action,'plan':value,'plan_sha256':sha(raw),'runtime':runtime,
          'state':bridge.config.get('remote_data',bridge.root+'/data')}
    if action=='launch':
        bodies={n:p.read_bytes() for n,p in FILES.items()}
        if {n:sha(b) for n,b in bodies.items()}!=value['source_code_sha256']:raise ValueError('context_expansion_plan_code_changed')
        data['programs']={n:base64.b64encode(b).decode() for n,b in bodies.items()};data['wrapper_base64']=base64.b64encode(WRAPPER.encode()).decode()
    if action=='resume':data['wrapper_base64']=base64.b64encode(WRAPPER.encode()).decode()
    if action=='collect':
        if match_id not in {r['match_id'] for r in value['selected']} or output is None:raise ValueError('context_expansion_selected_export_required')
        output=Path(output).resolve()
        if output.exists() or not output.parent.is_dir():raise ValueError('context_expansion_new_export_required')
        data['match_id']=match_id
    script=plan_path.parent/('remote-'+action+'-'+sha(raw)[:12]+'.py')
    script.write_text(REMOTE.replace('REQUEST',repr(data),1),encoding='utf-8',newline='\n')
    if action=='collect':
        temporary=output.with_name(output.name+'.part')
        if temporary.exists():raise ValueError('context_expansion_partial_export_exists')
        bridge.execute('python3 -',source=script,destination=temporary,timeout=60)
        frozen=json.loads(bounded(temporary,20*1024**2));manifest=json.loads(base64.b64decode(frozen['manifest_base64'],validate=True))
        if manifest.get('match_id')!=match_id or frozen.get('expansion_plan_sha256')!=sha(raw):raise ValueError('context_expansion_received_identity')
        temporary.replace(output)
        return {'status':'collected','match_id':match_id,'capture_sha256':sha(output.read_bytes()),'bytes':output.stat().st_size}
    return bridge.execute('python3 -',source=script,timeout=30)
