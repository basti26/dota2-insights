"""User-started, serial PC replay jobs; no live assistant or network inputs."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


def save_json(path, value):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def source_identity(source):
    source = Path(source).resolve(strict=True)
    if (not source.is_file() or source.suffix.lower() != '.dem'
            or re.fullmatch(r'[0-9]{1,20}', source.stem) is None):
        raise ValueError('请选择以比赛编号命名的.dem录像文件')
    from .replay_holdout import refuse_locked_match
    refuse_locked_match(source.stem)  # Refuse before reading held-out replay bytes.
    if not 16 <= source.stat().st_size <= 1024 ** 3:
        raise ValueError('录像大小须在16字节至1GiB之间')
    with source.open('rb') as stream:
        if stream.read(8) != b'PBDEMS2\0':
            raise ValueError('不是Source 2录像文件')
    return source, source.stem


def game_running():
    if sys.platform != 'win32':
        return False
    from .replay_archive import _windows_dota_running
    return _windows_dota_running()


def dependency_status(root):
    root = Path(root)
    if getattr(sys, 'frozen', False):
        from .replay_runtime import read_runtime_manifest, worker_directory
        try:
            read_runtime_manifest(worker_directory(root))
        except (OSError, ValueError, KeyError, TypeError):
            return '录像解析环境缺失或损坏，请重新解压完整程序。'
        return None
    if not (root / '.replay-deps/gem').is_dir():
        return '本机录像解析环境未准备好；请先按录像解析说明安装独立依赖。'
    return None


def pid_running(pid):
    """Conservative read-only orphan check; never signal an unrelated process."""
    if type(pid) is not int or pid <= 0:
        return True
    if sys.platform != 'win32':
        return (Path('/proc') / str(pid)).exists()
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87  # Invalid PID is gone; access denied is unknown.
    try:
        code = wintypes.DWORD()
        return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
    finally:
        kernel.CloseHandle(handle)


class ReplayQueue:
    """Explicit start only; interrupted jobs are never silently restarted."""
    def __init__(self, root, *, probe=game_running, spawn=subprocess.Popen, clock=time.monotonic,
                 pid_probe=pid_running):
        self.root, self.probe, self.spawn, self.clock = Path(root).resolve(), probe, spawn, clock
        self.pid_probe, self.unreaped_pids = pid_probe, []
        self.folder = self.root / 'diagnostics/replay-workbench'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.jobs, self.process, self.active = [], None, None
        self.enabled, self.log, self.stopping_at = False, None, None
        self.started_at, self.notice = None, '添加本地录像后点击开始；不会自动下载或解析。'
        path = self.folder / 'queue.json'
        if path.exists():
            if path.stat().st_size > 1024 ** 2:
                raise ValueError('录像任务清单过大')
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('schema_version') != 1 or not isinstance(value.get('jobs'), list) or len(value['jobs']) > 100:
                raise ValueError('录像任务清单无效')
            for job in value['jobs']:
                if (not isinstance(job, dict) or not isinstance(job.get('id'), str)
                        or len(job['id']) != 32 or any(c not in '0123456789abcdef' for c in job['id'])
                        or not isinstance(job.get('source'), str)
                        or re.fullmatch(r'[0-9]{1,20}', str(job.get('match_id'))) is None
                        or job.get('state') not in {'queued', 'running', 'stopping', 'completed',
                            'interrupted', 'failed', 'incomplete'}
                        or any(j['id'] == job['id'] for j in self.jobs)):
                    raise ValueError('录像任务身份无效')
                if job.get('state') in {'running', 'stopping'} or job.get('unreaped_worker') is True:
                    self.unreaped_pids.append(job.get('worker_pid'))
                    job = dict(job, state='interrupted', unreaped_worker=True,
                               reason='上次窗口退出时未确认任务收尾；未自动重跑')
                self.jobs.append(job)

    def _save(self):
        save_json(self.folder / 'queue.json', {'schema_version': 1, 'jobs': self.jobs})

    def add(self, path):
        if len(self.jobs) >= 100:
            raise ValueError('本地任务最多保留100项')
        source, mid = source_identity(path)
        if any(j['source'] == str(source) and j['state'] in {'queued', 'running', 'stopping'} for j in self.jobs):
            return False
        self.jobs.append({'id': uuid.uuid4().hex, 'source': str(source), 'match_id': mid,
                          'state': 'queued', 'reason': '',
                          'added_at_utc': datetime.now(timezone.utc).isoformat()})
        self._save()
        return True

    def start(self):
        if any(self.pid_probe(pid) for pid in self.unreaped_pids):
            raise ValueError('上次任务进程尚未确认退出，请稍后再开始；不会结束未知进程。')
        self.unreaped_pids.clear()
        for job in self.jobs:
            job.pop('unreaped_worker', None)
        self._save()
        problem = dependency_status(self.root)
        if problem:
            raise ValueError(problem)
        self.enabled = True
        self.notice = '已开始排队；Dota运行时等待，不启动解析。'

    def stop(self, reason='user_stop'):
        self.enabled = False
        if self.process is not None and self.stopping_at is None:
            (self.folder / self.active['id'] / 'cancel.request').touch()
            self.active.update(state='stopping', reason=reason)
            self.stopping_at = self.clock()
            self._save()
        self.notice = '已停止排队；正在结束当前任务。' if self.process else '已停止排队。'

    def poll(self):
        if self.process is not None:
            code = self.process.poll()
            if code is None:
                try:
                    blocked = self.probe()
                except OSError:
                    blocked = True
                if blocked:
                    self.stop('game_running_or_check_unavailable')
                elif self.clock() - self.started_at > 660:
                    self.stop('supervisor_time_limit')
                if self.stopping_at is not None and self.clock() - self.stopping_at > 8:
                    try:
                        self.process.kill()  # Only the child created for this job.
                    except OSError:
                        pass
                return
            job = self.active
            self.log.close()
            self.process, self.log, self.active = None, None, None
            if self.stopping_at is not None:
                job['state'] = 'interrupted'
            else:
                try:
                    receipt = self.folder / job['id'] / 'result.json'
                    if receipt.stat().st_size > 65536:
                        raise ValueError('result_size')
                    result = json.loads(receipt.read_text(encoding='utf-8'))
                    complete = (code == 0 and result.get('complete') is True
                                and result.get('match_id') == job['match_id'])
                    job.update(state='completed' if complete else 'incomplete',
                               reason=result.get('stop_reason', '结果不完整'))
                except (OSError, ValueError):
                    job.update(state='failed', reason='子进程未交付有效结果；查看任务日志')
            job['exit_code'] = code
            self.stopping_at = None
            self._save()
            # Leave a completed state visible for at least one UI refresh.
            return
        if not self.enabled:
            return
        try:
            if self.probe():
                self.notice = 'Dota正在运行，等待退出游戏后再解析。'
                return
        except OSError:
            self.notice = '无法核对游戏进程，暂不启动解析。'
            return
        job = next((j for j in self.jobs if j['state'] == 'queued'), None)
        if job is None:
            self.enabled = False
            self.notice = '队列已处理完；未完成任务保留原因，不自动重试。'
            return
        try:
            source, mid = source_identity(job['source'])
            if mid != job['match_id']:
                raise ValueError('录像比赛编号变化')
            folder = self.folder / job['id']
            folder.mkdir(exist_ok=False)
            self.log = (folder / 'worker.log').open('x', encoding='utf-8')
            flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if sys.platform == 'win32' else 0
            from .replay_runtime import replay_command
            self.process = self.spawn(replay_command(self.root, source, folder), cwd=str(self.root),
                stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT, creationflags=flags)
            self.active, self.started_at = job, self.clock()
            job.update(state='running', reason='', worker_pid=self.process.pid)
            self.notice = '正在解析比赛 ' + mid
        except (OSError, ValueError) as exc:
            if self.log:
                self.log.close()
                self.log = None
            job.update(state='failed', reason=type(exc).__name__)
        self._save()


def export_event_timeline(archive, output, *, guard=lambda: None):
    """Normalize only recorded combat events; no invented state transitions."""
    from .replay_archive import sha256_file
    archive, output = Path(archive), Path(output)
    manifest_path = archive / 'manifest.json'
    if manifest_path.stat().st_size > 1024 ** 2:
        raise ValueError('archive_manifest_size')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    mid = str(manifest.get('match_id'))
    from .replay_holdout import refuse_locked_match
    refuse_locked_match(mid)
    if manifest.get('kind') != 'offline_replay_archive':
        raise ValueError('archive_kind_required')
    source = archive / 'events.jsonl'
    expected = manifest['files']['events.jsonl']
    if source.stat().st_size > 256 * 1024 ** 2 or sha256_file(source) != expected['sha256']:
        raise ValueError('archive_event_source_changed')
    if re.fullmatch(r'[0-9]{1,20}', mid) is None:
        raise ValueError('archive_match_id_required')
    count, previous_tick, previous_clock, epoch = 0, None, None, 0
    # Exclusive output, including failed partial derivatives; never overwrite.
    with output.open('x', encoding='utf-8') as target, source.open('rb') as stream:
        while line := stream.readline(65537):
            if len(line) > 65536 or not line.endswith(b'\n') or count >= 200000:
                raise ValueError('event_timeline_row_limit')
            if count % 128 == 0:
                guard()
            event = json.loads(line)
            if (event.get('match_id') != mid or event.get('kind') != 'combat_event'
                    or type(event.get('tick')) is not int):
                raise ValueError('event_identity_invalid')
            clock = event.get('game_time_s')
            if (previous_tick is not None and event['tick'] < previous_tick
                    or type(clock) in (int, float) and previous_clock is not None and clock < previous_clock):
                epoch += 1
            previous_tick = event['tick']
            previous_clock = clock if type(clock) in (int, float) else None
            count += 1
            # Older archives coerced absent protobuf flags to False. Preserve
            # that raw value separately, but interpret it only when presence
            # was recorded explicitly. Never turn legacy absence into a fact.
            flag_names = ('target_is_hero', 'target_is_illusion')
            flags = {key: event.get(key) if event.get(key + '_present') is True
                     and type(event.get(key)) is bool else None for key in flag_names}
            data = {'schema_version': 2, 'match_id': mid, 'sequence': count,
                'tick_epoch': epoch, 'tick': event['tick'], 'game_time_s': event.get('game_time_s'),
                'kind': event.get('event_type'), 'time_basis': event.get('game_time_basis'),
                'source_ref': f'events.jsonl:{count}',
                'details': {key: event.get(key) for key in ('attacker', 'target', 'inflictor',
                    'value', 'timestamp_raw')}}
            data['details'].update(flags)
            data['source_flags'] = {key + suffix: event.get(key + suffix)
                if type(event.get(key + suffix)) is bool else None
                for key in (*flag_names, 'will_reincarnate') for suffix in ('', '_present')}
            data['source_flags']['will_reincarnate_decoder_value'] = (
                event.get('will_reincarnate_decoder_value')
                if type(event.get('will_reincarnate_decoder_value')) is bool else None)
            data['details']['will_reincarnate'] = (
                event.get('will_reincarnate') if event.get('will_reincarnate_present') is True
                and type(event.get('will_reincarnate')) is bool else None)
            data['details']['will_reincarnate_basis'] = event.get('will_reincarnate_basis')
            data['source_clocks'] = {key: event.get(key) for key in
                ('event_tick', 'packet_tick', 'event_game_time_s')}
            data['replay_visibility_raw'] = {team: event.get('visible_' + team + '_raw')
                if type(event.get('visible_' + team + '_raw')) is bool else None for team in ('radiant', 'dire')}
            target.write(json.dumps(data, ensure_ascii=False, allow_nan=False) + '\n')
    guard()
    if sha256_file(source) != expected['sha256']:
        raise ValueError('archive_event_source_changed')
    return {'schema_version': 2, 'rows': count, 'source_sha256': expected['sha256'], 'sha256': sha256_file(output),
        'clock_order': 'source_order_not_sorted_or_interpolated',
        'target_flags': 'only_explicit_presence_is_interpreted; legacy_defaults_remain_raw',
        'reincarnation': 'raw_optional_bool_and_decoder_value_separate; no_scoreboard_normalization',
        'visibility': 'raw_replay_bits_unverified_not_player_minimap_truth',
        'state_samples': 'hero_samples.jsonl remains separate at recorded sampling rate',
        'coverage': 'existing adapter event whitelist only; absence is not a zero count'}
