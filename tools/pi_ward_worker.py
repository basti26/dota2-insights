"""One low priority Pi ward analysis at a time, independent of Windows."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


def failure_summary(output, expected_match_id, exitcode):
    """Bounded diagnostic metadata only; never promote a partial export."""
    result={'diagnostic_only':True,'eligible_for_analysis':False,'process_exitcode':exitcode,
            'manifest_status':'not_read_process_nonterminal' if exitcode is None else 'unavailable'}
    if exitcode is None:
        return result
    path=Path(output)/'manifest.json'
    try:
        if path.is_symlink():return result
        with path.open('rb') as stream:raw=stream.read(262145)
        if len(raw)>262144:
            result['manifest_status']='oversized';return result
        manifest=json.loads(raw)
        if not isinstance(manifest,dict):raise ValueError('not_object')
        identifier=manifest.get('match_id')
        if identifier!=expected_match_id:
            result['manifest_status']='identity_conflict';return result
        result.update(manifest_status='metadata_read',manifest_sha256=hashlib.sha256(raw).hexdigest())
        for key in ('parse_error','stop_reason'):
            value=manifest.get(key)
            result[key]=value if isinstance(value,str) and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,79}',value) else None
        for key in ('complete','independent_footer_matches_parser'):
            value=manifest.get(key);result[key]=value if type(value) is bool else None
        tick=manifest.get('last_tick')
        result['last_tick']=tick if type(tick) is int and 0<=tick<2**32 else None
        parsed=manifest.get('parsed_match_id')
        valid=lambda value:isinstance(value,str) and re.fullmatch('[1-9][0-9]{0,19}',value) is not None
        result['parsed_match_id']=parsed if valid(parsed) else None
        result['parsed_match_id_matches_only_low32']=(parsed!=expected_match_id and int(parsed)==(int(expected_match_id)&0xffffffff)
            if valid(parsed) and valid(expected_match_id) else None)
        packets=manifest.get('failure_packet_diagnostics')
        if isinstance(packets,list) and len(packets)<=1:
            from dota2_insights.replay_packet_diagnostics import public_summary
            result['failure_packet_diagnostics']=[summary for value in packets
                if (summary:=public_summary(value)) is not None]
        result['meaning']='metadata_diagnostic_not_a_parser_repair_or_identity_override'
    except (OSError,ValueError,TypeError):
        result['manifest_status']='unreadable'
    return result


def process_one(store):
    from library import atomic,utc,verify
    with store.locked('local_parser'):
        pending=[r for r in store.catalog()['matches'].values()
                 if r.get('download_status')=='verified' and r.get('analysis_status')=='pending']
        if not pending:return {'status':'queue_empty'}
        row=sorted(pending,key=lambda r:int(r['match_id']))[0]
        identifier=row['match_id']
        source=store.replays/(identifier+'.dem')
        output=store.root/'work'/(identifier+'-'+uuid.uuid4().hex)
        log=store.root/'logs'/(identifier+'.log');log.parent.mkdir(parents=True,exist_ok=True)
        timeout=max(60,min(21600,int(store.config.get('parse_timeout_seconds',21600))))
        script=Path(__file__).with_name('extract_replay_wards.py')
        environment=os.environ.copy();environment['DOTA2_REPLAY_RUNTIME']=str(script.parent)
        # Use the pinned portable protobuf implementation until an ARM build is installed.
        environment.setdefault('PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION','python')
        store.update(identifier,analysis_status='analyzing',analysis_started_at_utc=utc(),analysis_location='pi')
        process=None;reason=None;began=time.monotonic()
        try:
            if verify(source,identifier)['sha256']!=row['sha256']:
                raise ValueError('replay_digest_changed')
            with log.open('ab') as stream:
                command=[sys.executable,str(script),str(source),'--output',str(output),'--max-seconds',str(timeout)]
                if store.config.get('postgame_context') is True:command.append('--with-context')
                process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,
                    env=environment,start_new_session=True)
                while process.poll() is None:
                    elapsed=time.monotonic()-began
                    if elapsed>timeout+120:raise ValueError('parse_wall_limit')
                    rss=0
                    try:
                        status=Path(f'/proc/{process.pid}/status').read_text()
                        rss=int(next(line.split()[1] for line in status.splitlines() if line.startswith('VmRSS:')))*1024
                    except (OSError,StopIteration,ValueError):pass
                    if rss>600*1024**2:raise ValueError('parse_memory_limit')
                    heartbeat={}
                    try:heartbeat=json.loads((output/'progress.json').read_text())
                    except (OSError,ValueError):pass
                    atomic(store.root/'worker_status.json',{'at_utc':utc(),'match_id':identifier,
                        'status':'analyzing','elapsed_seconds':round(elapsed,1),'rss_bytes':rss,'progress':heartbeat})
                    time.sleep(5)
                if process.returncode:raise ValueError('parser_incomplete')
            manifest=json.loads((output/'manifest.json').read_text())
            payload={'manifest':manifest,'wards_base64':base64.b64encode((output/'wards.json').read_bytes()).decode()}
            if manifest.get('postgame_context') is not None:
                payload['postgame_context_base64']=base64.b64encode((output/'postgame_context.json').read_bytes()).decode()
            result=store.accept_analysis(identifier,payload)
            atomic(store.root/'worker_status.json',{'at_utc':utc(),'status':'complete',**result})
            # Remove only this attempt's temporary result; canonical output is in the report directory.
            import shutil
            if output.resolve().parent!=(store.root/'work').resolve():raise ValueError('unsafe_work_path')
            shutil.rmtree(output)
            return {'status':'processed',**result}
        except Exception as exc:
            reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            detail=failure_summary(output,identifier,process.poll() if process is not None else None)
            store.update(identifier,analysis_status='partial_or_failed',reason=reason,analysis_location='pi',analysis_failure=detail)
            atomic(store.root/'worker_status.json',{'at_utc':utc(),'status':'partial_or_failed',
                'match_id':identifier,'reason':reason,'analysis_failure':detail})
            return {'status':'processed','match_id':identifier,'analysis_status':'partial_or_failed','reason':reason}
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
