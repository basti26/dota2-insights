"""Recoverable orchestration of existing bounded analysis tools, with native exits."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys

from .postgame_store import bounded
from .replay_holdout import refuse_locked_match

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('collect_pi_analysis_snapshot.py','merge_analysis_batches.py','build_postgame_patterns.py',
         'review_ward_phase_profile.py','build_postgame_context_store.py','review_ward_event_windows.py',
         'review_ward_revisits.py','run_professional_review.py')


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path, limit=256*1024**2):
    h = hashlib.sha256(); count = 0
    with Path(path).open('rb') as stream:
        for raw in iter(lambda: stream.read(1024**2), b''):
            count += len(raw)
            if count > limit:
                raise ValueError('professional_review_file_budget')
            h.update(raw)
    return h.hexdigest()


def atomic(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def lease(folder):
    with (folder/'pipeline.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'1');stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('professional_review_already_running') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def process_identity(pid):
    """OS creation identity, not a stale PID/state-file liveness assumption."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() in (87, 1168):
                return None
            raise ValueError('professional_review_process_identity_unavailable')
        try:
            code = wintypes.DWORD()
            kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD))
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):
                raise ValueError('professional_review_process_query_failed')
            if code.value != 259:
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *(ctypes.POINTER(wintypes.FILETIME),)*4)
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in times)):
                raise ValueError('professional_review_process_times_failed')
            return str(times[0].dwHighDateTime*2**32+times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    path = Path('/proc')/str(pid)/'stat'
    try:
        fields = path.read_text().rsplit(')',1)[1].split()
    except FileNotFoundError:
        return None
    return None if fields[0] == 'Z' else fields[19]


def code_fingerprint(root=ROOT):
    files = [*root.joinpath('dota2_insights').rglob('*.py'),
             *(root/'tools'/name for name in TOOLS), root/'data/validation-locks.json']
    return {str(p.relative_to(root)):digest(p, 2*1024**2) for p in sorted(files)}


def input_fingerprint(config):
    paths = {Path(config['map'])}
    folders = [Path(p) for p in config['batches']]
    if config['previous']:
        p = Path(config['previous']);paths.add(p)
        old = json.loads(bounded(p,4*1024**2))
        if old.get('kind') != 'completed_analysis_batch_registry':
            raise ValueError('professional_review_previous_kind')
        folders += [Path(ref['snapshot_path']).parent for ref in old['batches']]
    for folder in set(folders):
        sp = folder/'snapshot.json';paths.update((sp,folder/'receipt.json'))
        frozen = json.loads(bounded(sp,32*1024**2))
        for entry in frozen['matches']:
            refuse_locked_match(entry['match_id'])
            cp = folder/f"capture-{entry['match_id']}.json"
            if cp.is_file():paths.add(cp)
    if len(paths)>1024:
        raise ValueError('professional_review_input_budget')
    return {str(p.resolve()):digest(p,32*1024**2) for p in sorted(paths)}


class WaitingForStage(Exception):
    pass


class Pipeline:
    def __init__(self, folder, config, *, resume=False, root=ROOT, runner=None):
        self.folder, self.root = Path(folder).resolve(), Path(root).resolve()
        self.path = self.folder/'pipeline.json'; self.runner = runner
        if resume:
            self.state = json.loads(bounded(self.path,4*1024**2))
            if self.state.get('kind') != 'recoverable_professional_review' or self.state.get('schema_version') != 1:
                raise ValueError('professional_review_state_kind')
            if config is not None and config != self.state['config']:
                raise ValueError('professional_review_resume_config_changed')
        else:
            if self.folder.exists():raise ValueError('professional_review_new_output_required')
            self.folder.mkdir(parents=True)
            self.state = {'kind':'recoverable_professional_review','schema_version':1,'created_at_utc':utc(),
                          'status':'prepared','config':config,'stages':{},
                          'inputs':input_fingerprint(config),'code':code_fingerprint(self.root)}
        self.config = self.state['config']

    def save(self):
        self.state['updated_at_utc'] = utc();atomic(self.path,self.state)

    def unchanged(self):
        if input_fingerprint(self.config) != self.state['inputs']:
            raise ValueError('professional_review_inputs_changed')
        if code_fingerprint(self.root) != self.state['code']:
            raise ValueError('professional_review_code_changed_use_new_run')

    def native_run(self, argv, log, attempt):
        with log.open('xb') as stream:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                attempt.update(pid=child.pid, process_identity=process_identity(child.pid) if child.poll() is None else None)
                self.save()
                return child.wait(timeout=1800)
            except BaseException:
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=5)
                    except subprocess.TimeoutExpired:child.kill();child.wait()
                raise

    def stage(self, name, tool, arguments, receipt_name):
        self.unchanged()
        stage = self.state['stages'].setdefault(name, {'attempts':[]})
        for attempt in reversed(stage['attempts']):
            if attempt['status'] == 'complete':
                if type(attempt.get('native_exit_code')) is not int or attempt['native_exit_code'] != 0:
                    raise ValueError('professional_review_native_exit_missing')
                for relative, expected in attempt['artifacts'].items():
                    path = self.folder/relative
                    if not path.resolve().is_relative_to(self.folder) or digest(path) != expected:
                        raise ValueError('professional_review_completed_artifact_changed')
                return self.folder/attempt['output']
            if attempt['status'] == 'running':
                current = process_identity(attempt['pid'])
                if current is not None and (attempt.get('process_identity') is None or current == attempt['process_identity']):
                    self.state.update(status='waiting_for_stage',waiting_stage=name)
                    self.save();raise WaitingForStage(name)
                attempt['status'] = 'interrupted_process_confirmed_gone'
            if attempt['status'] == 'launching' and not attempt.get('pid'):
                raise ValueError('professional_review_unconfirmed_launch_do_not_restart')
        number = len(stage['attempts'])+1
        relative = Path('stages')/name/f'attempt-{number:04d}'
        output = self.folder/relative;output.parent.mkdir(parents=True,exist_ok=True)
        attempt = {'status':'running','output':relative.as_posix(),'started_at_utc':utc()}
        stage['attempts'].append(attempt)
        argv = [sys.executable,str(self.root/'tools'/tool),*arguments,'--output',str(output)]
        self.state.update(status='running',current_stage=name)
        # Until a native child exists, this attempt must not be mistaken for a live process.
        attempt['status']='launching';self.save()
        try:
            print(json.dumps({'stage':name,'attempt':number,'status':'starting'}),flush=True)
            if self.runner:
                code = self.runner(argv,output,attempt)
            else:
                attempt['status']='running'
                code = self.native_run(argv,output.with_suffix('.log'),attempt)
            attempt['native_exit_code']=code
            if code != 0:raise ValueError('professional_review_stage_exit_nonzero')
            receipt = json.loads(bounded(output/receipt_name,4*1024**2))
            if receipt.get('status') not in ('complete','complete_with_conflicts'):
                raise ValueError('professional_review_stage_not_complete')
            files = [p for p in output.rglob('*') if p.is_file()]
            if not files or len(files)>2048:raise ValueError('professional_review_artifact_budget')
            artifacts = {}
            for p in files:
                if not p.resolve().is_relative_to(output):raise ValueError('professional_review_artifact_outside_attempt')
                artifacts[p.relative_to(self.folder).as_posix()] = digest(p)
            self.unchanged()
            attempt.update(status='complete',finished_at_utc=utc(),artifacts=artifacts)
            self.save()
            return output
        except BaseException as exc:
            attempt.update(status='failed',error_type=type(exc).__name__,finished_at_utc=utc())
            self.state.update(status='failed',failed_stage=name);self.save();raise

    def run(self):
        with lease(self.folder):
            try:
                return self._run_locked()
            except WaitingForStage:
                raise
            except BaseException as exc:
                self.state.update(status='failed',error_type=type(exc).__name__)
                self.save()
                raise

    def _run_locked(self):
        self.unchanged();self.save();c=self.config
        batches = list(c['batches'])
        if c['collect_pi']:
            collected = self.stage('collect','collect_pi_analysis_snapshot.py',
                ['--source','tier1','--limit',str(c['limit'])],'receipt.json')
            batches.append(str(collected))
        args = [v for p in batches for v in ('--batch',p)]
        if c['previous']:args+=['--previous',c['previous']]
        for mid, source in c['selection'].items():args+=['--select',mid+'='+source]
        registry = self.stage('merge','merge_analysis_batches.py',args,'registry.json')
        r=json.loads((registry/'registry.json').read_text(encoding='utf-8'))
        if not r['selected_matches']:
            self.state.update(status='needs_selection',conflicting_matches=r['conflicting_matches']);self.save()
            return self.state
        snapshot=str(registry/'snapshot.json');mp=c['map']
        facts=self.stage('patterns','build_postgame_patterns.py',['--snapshot',snapshot,'--map',mp],'run-status.json')
        phase=self.stage('map','review_ward_phase_profile.py',['--snapshot',snapshot,'--map',mp],'run-summary.json')
        args=['--database',str(facts/'facts/facts.sqlite')]
        captures=[v for v in r['versions'] if v['selected'] and v['capture_alias_sha256']]
        for v in captures:args+=['--captured-context',str(registry/f"capture-{v['match_id']}.json")]
        if c['reconcile_pauses']:args+=['--reconcile-paused-metric-clocks']
        if c['retain_missing_event_times']:args+=['--retain-missing-event-times']
        context=self.stage('context','build_postgame_context_store.py',args,'import-receipt.json')
        events=self.stage('events','review_ward_event_windows.py',
            ['--database',str(context/'facts.sqlite'),'--map',mp],'run-summary.json')
        revisits=self.stage('revisits','review_ward_revisits.py',
            ['--database',str(facts/'facts/facts.sqlite'),'--map',mp],'run-summary.json')
        self.unchanged()
        # Batch observations are historical, never a live Pi status claim.
        selected_ids={v['match_id'] for v in r['versions'] if v['selected']}
        incomplete, remainder = {}, []
        for batch in r['batches']:
            sp=Path(batch['snapshot_path']);frozen=json.loads(bounded(sp,32*1024**2))
            for row in frozen.get('excluded_catalog_rows',[]):
                if row['match_id'] in selected_ids:continue
                incomplete.setdefault(row['match_id'],[]).append({
                    'snapshot_sha256':batch['snapshot_sha256'],
                    'download_status':row.get('download_status'),'analysis_status':row.get('analysis_status')})
            receipt=json.loads(bounded(sp.parent/'receipt.json',1024**2))
            if receipt.get('complete_rows_outside_batch_limit',0):
                remainder.append({'snapshot_sha256':batch['snapshot_sha256'],
                    'reported_count':receipt['complete_rows_outside_batch_limit']})
        gaps={'conflicting_matches':r['conflicting_matches'],
              'selected_matches_without_context':r['selected_matches']-len(captures),
              'recorded_incomplete_unselected_matches':incomplete,
              'batch_remainder_observations_not_summed':remainder}
        self.unchanged()
        self.state.update(status='complete_with_gaps' if any(gaps.values()) else 'complete',
            finished_at_utc=utc(),summary={'matches':r['selected_matches'],'duplicate_entries':r['duplicate_entries'],
                'gaps':gaps,'current_map_certified':False})
        links=[('分时段/阵营眼位地图',phase/'report.html'),('重复区域与再次放眼',revisits/'report.html'),
               ('比赛事件前后的眼位',events/'report.html'),('固定网格使用频率',facts/'patterns/report.html')]
        page='<html lang="zh-CN"><meta charset="utf-8"><title>职业眼位分析</title><body style="font:18px system-ui;margin:36px"><h1>职业眼位分析</h1>'
        page+=f"<p>累计{r['selected_matches']}盘；重复输入{r['duplicate_entries']}条未重复计数。</p>"
        page+='<p>当前地图仍为示意投影；统计频率不等于最佳眼位。未录到/未完成的内容不算零。</p><ul>'
        for label,path in links:page+=f'<li><a href="{html.escape(path.relative_to(self.folder).as_posix(),quote=True)}">{label}</a></li>'
        page+='</ul><pre>'+html.escape(json.dumps(gaps,ensure_ascii=False,indent=2))+'</pre></body></html>'
        (self.folder/'index.html').write_text(page,encoding='utf-8');self.save()
        return self.state
