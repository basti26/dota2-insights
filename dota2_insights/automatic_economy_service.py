"""Durable, independent local scheduler. No downloader or live game imports."""
from contextlib import contextmanager
import json
from html import escape
import os
from pathlib import Path
import sys
import time

from . import automatic_economy as review


def home(root):
    return Path(root) / 'diagnostics/automatic-economy'


@contextmanager
def lease(root):
    folder = home(root); folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'service.lock').open('a+b') as stream:
        acquired = False
        try:
            if sys.platform == 'win32':
                import msvcrt
                if stream.seek(0, 2) == 0:
                    stream.write(b'0'); stream.flush()
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        try:
            yield acquired
        finally:
            if acquired:
                if sys.platform == 'win32':
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Service:
    def __init__(self, root=review.ROOT, *, runner=review.run_task, check=review.guard, now=time.time):
        self.root = Path(root).resolve(); self.runner = runner; self.check = check; self.now = now
        self.folder = home(self.root); self.folder.mkdir(parents=True, exist_ok=True)
        path = self.root / 'config/automatic_economy.local.json'
        self.config = review.read_json(path, 8192) if path.exists() else {}
        if not isinstance(self.config, dict):
            raise ValueError('automatic_review_config_invalid')
        for key in ('replay_directories', 'archive_directories'):
            paths = self.config.get(key, [])
            if (not isinstance(paths, list) or len(paths) > 32
                    or any(not isinstance(p, str) or not Path(p).is_absolute() for p in paths)):
                raise ValueError('automatic_review_config_paths_invalid')
        self.state_path = self.folder / 'queue.json'
        self.state = review.read_json(self.state_path) if self.state_path.exists() else {
            'schema_version': 1, 'known_matches': [], 'tasks': {}, 'initialized': False}
        if (self.state.get('schema_version') != 1 or not isinstance(self.state.get('tasks'), dict)
                or len(self.state['tasks']) > 1000 or not isinstance(self.state.get('known_matches'), list)
                or len(self.state['known_matches']) > 10000):
            raise ValueError('automatic_review_state_invalid')
        self.last_heartbeat = 0

    def save(self):
        review.atomic(self.state_path, self.state)
        lines = ['<!doctype html><meta charset="utf-8"><title>自动经济复查</title>',
            '<style>body{background:#152027;color:#d7e2e6;font:16px system-ui;margin:32px}a{color:#9acdbb}td,th{padding:10px;text-align:left}</style>',
            '<h1>赛后自动经济复查</h1><p>缺录像仍保留记录报告；图像真值待确认，不自动改变线上识别。</p>',
            '<table><tr><th>比赛</th><th>状态</th><th>报告</th></tr>']
        for task in sorted(self.state['tasks'].values(), key=lambda t: t['ended_at_utc'], reverse=True):
            value = task.get('report'); link = '等待生成'
            if value:
                path = Path(value).resolve()
                if path.is_relative_to(self.folder / 'matches'):
                    link = '<a href="' + escape(path.relative_to(self.folder).as_posix(), quote=True) + '">查看</a>'
            label = {'queued': '待处理', 'waiting_replay': '记录已复查，缺录像',
                'waiting_sources_or_comparable_records': '等待闭合来源或可比记录',
                'review_complete_evidence_only': '复查完成，图像真值待确认',
                'review_failed': '复查异常，等待重试', 'replay_review_failed': '记录已复查，录像复查异常',
                'paused_game_running': '等待关闭游戏'}.get(task.get('state'), '等待')
            lines.append('<tr><td>' + escape(task['match_id']) + '</td><td>' + label + '</td><td>' + link + '</td></tr>')
        (self.folder / 'index.html').write_text(''.join(lines) + '</table>', encoding='utf-8')

    def heartbeat(self, stage='idle', match=None, error=None):
        if stage not in {'idle', 'paused_game_running', 'stopped', 'service_error'} and self.now() - self.last_heartbeat < 2:
            return
        self.last_heartbeat = self.now()
        tasks = list(self.state['tasks'].values())
        latest = max((t for t in tasks if t.get('report')), key=lambda t: t.get('reviewed_at_utc', ''), default={})
        review.atomic(self.folder / 'status.json', {'schema_version': 1, 'service_pid': os.getpid(),
            'heartbeat_epoch': self.now(), 'stage': stage, 'active_match_id': match,
            'error': error, 'tasks': len(tasks), 'complete': sum(t.get('state') == 'review_complete_evidence_only' for t in tasks),
            'waiting_replay': sum(t.get('state') == 'waiting_replay' for t in tasks),
            'waiting_sources': sum(t.get('state') == 'waiting_sources_or_comparable_records' for t in tasks),
            'failed': sum(bool(t.get('error')) for t in tasks),
            'latest_report': latest.get('report'), 'latest_match_id': latest.get('match_id'),
            'latest_reviewed_at_utc': latest.get('reviewed_at_utc'), 'independent': True})

    def discover(self):
        roots = review.history_roots(self.root / 'config/history.local.json')
        found = review.endings(roots)
        known = set(self.state['known_matches'])
        new = set(found) - known
        if not self.state['initialized']:
            # Bounded recent bootstrap, never replay the entire historic library.
            new = set(sorted(found, key=lambda mid: found[mid]['ended_at_utc'], reverse=True)[:3])
        for mid in new:
            if len(self.state['tasks']) >= 1000:
                raise ValueError('automatic_review_task_capacity')
            self.state['tasks'][mid] = {**found[mid], 'state': 'queued', 'next_check_epoch': 0, 'attempts': 0}
        for mid, task in self.state['tasks'].items():
            if mid in found:
                task.update(found[mid])
        known.update(found)
        if len(known) > 10000:
            raise ValueError('automatic_review_known_capacity')
        self.state.update(known_matches=sorted(known), initialized=True)
        self.save()

    def cycle(self):
        if (self.folder / 'stop.request').exists():
            return False
        try:
            self.check()
        except InterruptedError:
            self.heartbeat('paused_game_running')
            return True
        self.discover()
        due = [t for t in self.state['tasks'].values() if t.get('next_check_epoch', 0) <= self.now()]
        if not due:
            self.heartbeat(); return True
        task = min(due, key=lambda t: (t.get('next_check_epoch', 0), t['ended_at_utc']))
        mid = task['match_id']

        def check():
            if (self.folder / 'stop.request').exists():
                raise InterruptedError('stop_requested')
            self.check()

        def heartbeat(stage):
            self.heartbeat(stage, mid)

        try:
            self.heartbeat('reviewing_records', mid)
            result = self.runner(dict(task), root=self.root, config=self.config, heartbeat=heartbeat, check=check)
            result['next_check_epoch'] = self.now() + 600
            result['attempts'] = task.get('attempts', 0) + 1
            if result.get('error'):
                result['failures'] = task.get('failures', 0) + 1
                result['next_check_epoch'] = self.now() + min(86400, 600 * 2**min(result['failures'] - 1, 7))
            self.state['tasks'][mid] = result
        except InterruptedError:
            task.update(next_check_epoch=self.now() + 30, state='paused_game_running')
        except Exception as exc:
            failures = task.get('failures', 0) + 1
            task.update(error=type(exc).__name__, failures=failures, state='review_failed',
                next_check_epoch=self.now() + min(86400, 600 * 2**min(failures - 1, 7)))
        self.save(); self.heartbeat()
        return True


def main(root=review.ROOT, *, once=False):
    with lease(root) as acquired:
        if not acquired:
            return 0
        service = Service(root)
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)
        error = None
        try:
            while True:
                try:
                    keep_running = service.cycle()
                except Exception as exc:
                    error = type(exc).__name__
                    service.heartbeat('service_error', error=type(exc).__name__)
                    keep_running = True
                if once or not keep_running:
                    break
                time.sleep(10)
        finally:
            service.heartbeat('stopped', error=error)
    return 1 if once and error else 0
