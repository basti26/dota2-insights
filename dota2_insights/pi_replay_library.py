"""SSH bridge for the dedicated replay archive and bounded Pi analysis."""
from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

from .replay_library_store import MAX_DEM, atomic, mid, verify

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'config'/'pi_replay_library.local.json'
WORK = ROOT/'diagnostics'/'pi-replay-library'


def configuration():
    if not CONFIG.exists():
        return {'enabled': False}
    data = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    if not data.get('enabled'):
        return {'enabled': False}
    import ipaddress
    address = ipaddress.ip_address(data['host'])
    if address.is_unspecified or not address.is_private:
        raise ValueError('explicit_private_pi_address_required')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', data['user']):
        raise ValueError('invalid_pi_user')
    root = PurePosixPath(data['remote_root'])
    if not root.is_absolute() or '..' in root.parts or not str(root).startswith('/home/'+data['user']+'/'):
        raise ValueError('pi_root_must_be_in_user_home')
    return data


class Bridge:
    def __init__(self, config=None, stop_event=None):
        self.config = configuration() if config is None else config
        self.stop_event = stop_event
        if not self.config.get('enabled'):
            raise ValueError('pi_library_disabled')
        self.root = self.config['remote_root']

    def guard(self):
        from .replay_archive import _windows_dota_running
        if self.stop_event is not None and self.stop_event.is_set():
            raise ValueError('cancelled')
        if sys.platform == 'win32' and _windows_dota_running():
            raise ValueError('game_running')

    def argv(self, command):
        c = self.config
        ssh = c.get('ssh_executable', r'C:\Windows\System32\OpenSSH\ssh.exe')
        args = [ssh, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=5',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
                '-i', c['identity_file']]
        if c.get('local_address'):
            args += ['-b', c['local_address']]
        return args + [c['user']+'@'+c['host'], command]

    def remote(self, *args):
        return shlex.join(['python3',self.config.get('remote_program',self.root+'/bin/library.py'),
            '--root',self.config.get('remote_data',self.root+'/data'),*map(str,args)])

    def execute(self, command, *, source=None, destination=None, timeout=240):
        self.guard()
        WORK.mkdir(parents=True, exist_ok=True)
        # File-backed streams prevent a large replay/response from filling RAM or blocking a pipe.
        with ExitStack() as stack:
            inp = stack.enter_context(Path(source).open('rb')) if source else subprocess.DEVNULL
            out = stack.enter_context(Path(destination).open('wb')) if destination else stack.enter_context(tempfile.TemporaryFile())
            err = stack.enter_context(tempfile.TemporaryFile())
            process = subprocess.Popen(self.argv(command), stdin=inp, stdout=out, stderr=err,
                                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            began = time.monotonic()
            try:
                while process.poll() is None:
                    self.guard()
                    if time.monotonic()-began > timeout:
                        raise ValueError('pi_operation_timeout')
                    if destination and out.tell() > MAX_DEM:
                        raise ValueError('export_size_limit')
                    time.sleep(.5)
                if process.returncode:
                    # Never echo remote arbitrary stderr, which might contain local configuration.
                    err.seek(0)
                    detail = ''
                    try:
                        failure = json.loads(err.read(4096))
                        candidate = failure.get('reason') or failure.get('error','')
                        if re.fullmatch(r'[A-Za-z_]{1,80}',candidate):detail = '_'+candidate
                    except (ValueError,AttributeError,TypeError):pass
                    raise ValueError('pi_command_failed_'+str(process.returncode)+detail)
                if destination:
                    return
                out.seek(0)
                raw = out.read(4*1024**2+1)
                if len(raw)>4*1024**2:
                    raise ValueError('pi_response_limit')
                return json.loads(raw)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)

    def catalog(self):
        return self.execute(self.remote('catalog'))

    def deploy(self):
        """Install only this user's files and an additive crontab boot entry."""
        import base64
        if self.config.get('managed_on_pi'):
            from .pi_deployment import deployment_sources
            payload={name:base64.b64encode(raw).decode() for name,raw in deployment_sources(ROOT).items()}
            code="""from pathlib import Path
import base64,json,subprocess
root=Path('/opt/dota2-replay')
for name,data in PAYLOAD.items():
 p=root/name;p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_suffix('.tmp');temp.write_bytes(base64.b64decode(data));temp.replace(p)
subprocess.run(['systemctl','--user','restart','dota2-replay.service'],check=True)
print(json.dumps({'installed':True,'runtime':'pi_systemd_user'}))
""".replace('PAYLOAD',repr(payload))
            WORK.mkdir(parents=True,exist_ok=True);script=WORK/('deploy-'+uuid.uuid4().hex+'.py')
            try:
                script.write_text(code,encoding='utf-8')
                return self.execute('python3 -',source=script,timeout=30)
            finally:script.unlink(missing_ok=True)
        payload={'root':self.root,'host':self.config['host'],
                 'source':base64.b64encode((ROOT/'dota2_insights'/'replay_library_store.py').read_bytes()).decode()}
        code='''import base64,json,os,signal,subprocess,time
from pathlib import Path
p=PAYLOAD
root=Path(p['root']);script=root/'bin/library.py';data=root/'data';logs=root/'logs'
for d in (script.parent,data,logs):d.mkdir(parents=True,exist_ok=True)
temp=script.with_suffix('.tmp');temp.write_bytes(base64.b64decode(p['source']));temp.chmod(0o600);temp.replace(script)
config=data/'config.json'
settings=json.loads(config.read_text()) if config.exists() else {'storage_limit_gib':12,'pro_daily_limit':3,'min_mmr':7000,'port':8877}
settings['bind_host']=p['host'];config.write_text(json.dumps(settings,indent=2));config.chmod(0o600)
old=subprocess.run(['crontab','-l'],capture_output=True,text=True)
if old.returncode not in (0,1):raise RuntimeError('cannot_read_crontab')
marker='# dota2-helper replay library'
if marker not in old.stdout:
    (root/'crontab-before.txt').write_text(old.stdout)
    import shlex
    line='@reboot /usr/bin/nice -n 15 /usr/bin/python3 '+shlex.quote(str(script))+' --root '+shlex.quote(str(data))+' run >> '+shlex.quote(str(logs/'daemon.log'))+' 2>&1 '+marker+'\\n'
    subprocess.run(['crontab','-'],input=old.stdout.rstrip()+'\\n'+line,text=True,check=True)
for proc in Path('/proc').iterdir():
    try:
        if not proc.name.isdecimal() or proc.stat().st_uid!=os.getuid():continue
        args=(proc/'cmdline').read_bytes().split(b'\\0')
        if str(script).encode() in args and str(data).encode() in args and b'run' in args:
            os.kill(int(proc.name),signal.SIGTERM)
    except (OSError,ProcessLookupError):pass
time.sleep(1)
with (logs/'daemon.log').open('ab') as log:
    process=subprocess.Popen(['/usr/bin/nice','-n','15','/usr/bin/python3',str(script),'--root',str(data),'run'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
print(json.dumps({'installed':True,'pid':process.pid,'boot':'user_crontab','port':settings['port']}))
'''.replace('PAYLOAD',repr(payload))
        script=WORK/('deploy-'+uuid.uuid4().hex+'.py')
        WORK.mkdir(parents=True,exist_ok=True)
        try:
            script.write_text(code,encoding='utf-8')
            return self.execute('python3 -',source=script,timeout=30)
        finally:script.unlink(missing_ok=True)

    def archive(self, path, source='own'):
        path = Path(path)
        self.guard()
        value = verify(path, path.stem)
        result = self.execute(self.remote('receive', path.stem, '--source', source,
            '--bytes', value['bytes'], '--sha256', value['sha256']), source=path)
        if result.get('sha256') != value['sha256']:
            raise ValueError('archive_receipt_mismatch')
        return result

    def analysis(self, folder):
        import base64
        folder = Path(folder)
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        identifier = mid(manifest['match_id'])
        payload = WORK/('upload-'+uuid.uuid4().hex+'.json')
        try:
            value={'manifest': manifest, 'wards_base64': base64.b64encode((folder/'wards.json').read_bytes()).decode()}
            if manifest.get('postgame_context') is not None:
                value['postgame_context_base64']=base64.b64encode((folder/'postgame_context.json').read_bytes()).decode()
            atomic(payload,value)
            return self.execute(self.remote('analysis', identifier), source=payload)
        finally:
            payload.unlink(missing_ok=True)

    def sync(self, limit=1):
        data=self.catalog()
        rows = data['matches']
        paths = list((ROOT/'diagnostics'/'replay-downloads').glob('*.dem'))
        for directory in self.config.get('own_replay_directories', []):
            paths += list(Path(directory).glob('*.dem'))
        results = []
        for path in sorted(paths, key=lambda p:p.name, reverse=True):
            if not path.stem.isdecimal() or time.time()-path.stat().st_mtime < 30:
                continue
            row = rows.get(path.stem, {})
            if row.get('download_status') == 'verified' and 'own' in row.get('sources',[]):
                continue
            results.append(self.archive(path))
            rows[path.stem] = {'download_status':'verified','sources':['own']}
            if len(results)>=limit:
                break
        return results

    def work_one(self):
        WORK.mkdir(parents=True,exist_ok=True)
        with (WORK/'parser.lock').open('a+b') as lease:
            try:
                if sys.platform=='win32':
                    import msvcrt
                    lease.seek(0);lease.write(b'0');lease.flush();lease.seek(0)
                    msvcrt.locking(lease.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:return {'analysis_status':'parser_busy'}
            return self._work_one()

    def _work_one(self):
        self.guard()
        data=self.catalog()
        if data.get('runtime',{}).get('local_analysis'):
            return {'analysis_status':'managed_on_pi'}
        rows = data['matches']
        pending = [r for r in rows.values() if r.get('download_status')=='verified' and r.get('analysis_status')=='pending']
        for row in sorted(pending, key=lambda r:int(r['match_id']), reverse=True):
            identifier = mid(row['match_id'])
            # Reuse completed exports before parsing anything again.
            candidates = list((ROOT/'diagnostics').glob('replay-ward-review-*/'+identifier+'/manifest.json'))
            candidates += list((WORK/'analysis'/identifier).glob('*/manifest.json'))
            for candidate in candidates:
                m = json.loads(candidate.read_text(encoding='utf-8'))
                if m.get('complete') is True and m.get('source_sha256') == row['sha256']:
                    return self.analysis(candidate.parent)
            local_paths=[ROOT/'diagnostics'/'replay-downloads'/(identifier+'.dem')]
            local_paths += [Path(p)/(identifier+'.dem') for p in self.config.get('own_replay_directories',[])]
            own = next((p for p in local_paths if p.is_file()),local_paths[0])
            cached = WORK/'cache'/(identifier+'.dem')
            source = own if own.exists() else cached
            if not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                if (shutil.disk_usage(source.parent).free < 4*1024**3+row['bytes']
                    or sum(p.stat().st_size for p in source.parent.glob('*.dem'))+row['bytes']>4*1024**3):
                    raise ValueError('local_cache_storage_reserve')
                temporary = source.with_suffix('.'+uuid.uuid4().hex+'.part')
                try:
                    self.execute(self.remote('export', identifier), destination=temporary)
                    value = verify(temporary,identifier)
                    if value['sha256'] != row['sha256']:
                        raise ValueError('export_digest_mismatch')
                    temporary.replace(source)
                finally:temporary.unlink(missing_ok=True)
            if verify(source,identifier)['sha256'] != row['sha256']:
                raise ValueError('parse_source_mismatch')
            out = WORK/'analysis'/identifier/uuid.uuid4().hex
            self.guard()
            log = WORK/('parser-'+identifier+'.log')
            with log.open('ab') as stream:
                parser = subprocess.Popen([sys.executable, str(ROOT/'tools'/'extract_replay_wards.py'),str(source),
                    '--output',str(out),'--max-seconds','180'],stdout=stream,stderr=stream,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                began = time.monotonic()
                try:
                    while parser.poll() is None:
                        self.guard()
                        if time.monotonic()-began>200:
                            raise ValueError('parse_wall_limit')
                        time.sleep(1)
                    if parser.returncode:
                        atomic(WORK/'parse-status'/f'{identifier}.json',{'match_id':identifier,'status':'partial_or_failed','output':str(out)})
                        return self.execute(self.remote('analysis-failed',identifier))
                finally:
                    if parser.poll() is None:
                        parser.terminate();parser.wait(timeout=5)
            result = self.analysis(out)
            # Pro DEM stays on Pi; release the temporary Windows replay cache.
            if source == cached:
                cached.unlink(missing_ok=True)
            return result
        return {'analysis_status':'queue_empty'}


def maintenance(stop_event=None):
    try:
        config = configuration()
        if not config.get('enabled') or getattr(sys,'frozen',False):
            return
        bridge = Bridge(config,stop_event)
        archived = bridge.sync(limit=1)
        analyzed = bridge.work_one()
        atomic(WORK/'worker-status.json', {'archived':archived,'analyzed':analyzed,'at_unix':time.time()})
    except Exception as exc:
        reason=str(exc) if re.fullmatch(r'[A-Za-z_0-9]{1,120}',str(exc)) else None
        atomic(WORK/'worker-status.json',{'error':type(exc).__name__,'reason':reason,'at_unix':time.time()})
