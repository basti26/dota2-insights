"""One low priority Pi ward analysis at a time, independent of Windows."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


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
            store.update(identifier,analysis_status='partial_or_failed',reason=reason,analysis_location='pi')
            atomic(store.root/'worker_status.json',{'at_utc':utc(),'status':'partial_or_failed',
                'match_id':identifier,'reason':reason})
            return {'status':'processed','match_id':identifier,'analysis_status':'partial_or_failed','reason':reason}
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
