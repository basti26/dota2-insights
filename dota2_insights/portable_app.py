"""Portable app checks and publication metadata; never starts a real replay."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def public_locks(root):
    path = Path(root) / 'data/validation-locks.json'
    with path.open('rb') as stream:
        raw = stream.read(16385)
    value = json.loads(raw)
    if (len(raw) > 16384 or not isinstance(value, dict)
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or not isinstance(value.get('heldout_matches'), list)
            or any(not isinstance(v, str) or not v.isascii() or not v.isdigit() or not 1 <= len(v) <= 20
                   for v in value['heldout_matches'])):
        raise ValueError('portable_public_locks_invalid')
    return value


def verify_application(root):
    root = Path(root).resolve(strict=True)
    manifest = root / 'build-manifest.json'
    with manifest.open('rb') as stream:
        raw = stream.read(4 * 1024 ** 2 + 1)
    if len(raw) > 4 * 1024 ** 2:
        raise ValueError('portable_app_manifest_size')
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get('kind') != 'portable_dota2_insights'
            or value.get('schema_version') != 1 or type(value.get('schema_version')) is not int
            or not isinstance(value.get('files'), list) or not 1 <= len(value['files']) <= 6000):
        raise ValueError('portable_app_manifest_invalid')
    names = set()
    for row in value['files']:
        if not isinstance(row, dict):
            raise ValueError('portable_app_manifest_row')
        name = row.get('path')
        if (not isinstance(name, str) or not name or ':' in name or '\\' in name
                or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                or name in names or name == 'build-manifest.json'):
            raise ValueError('portable_app_manifest_path')
        path = root / name
        if not path.resolve().is_relative_to(root) or not path.is_file() or digest(path) != row.get('sha256'):
            raise ValueError('portable_app_file_changed')
        names.add(name)
    required = {'Dota2Insights.exe', 'data/validation-locks.json',
                'runtime/replay/Dota2ReplayWorker.exe', 'runtime/replay/runtime-manifest.json'}
    if not required.issubset(names):
        raise ValueError('portable_app_required_files_missing')
    public_locks(root)
    return value


def check_hidden_ui(root):
    import tkinter as tk
    from unittest.mock import patch
    from tools import win_replay_analysis
    with tempfile.TemporaryDirectory(prefix='insights-ui-check-') as folder, \
            patch.object(win_replay_analysis, 'ROOT', Path(folder)), \
            patch.object(subprocess, 'Popen', side_effect=RuntimeError('portable_unexpected_job_start')):
        host = tk.Tk()
        host.withdraw()
        window = tk.Toplevel(host)
        window.withdraw()
        controller = None
        try:
            controller = win_replay_analysis.ReplayAnalysisWindow(window)
            window.update_idletasks()
            if window.state() != 'withdrawn' or controller.queue.process is not None or controller.queue.enabled:
                raise ValueError('portable_idle_ui_not_idle')
            controller.close()
            controller = None
            if window.winfo_exists():
                raise ValueError('portable_idle_ui_not_closed')
        finally:
            if controller is not None:
                controller.close()
            host.destroy()
    return {'hidden_form_opened_closed': True, 'parser_started': False}


def write_check(output, root, *, check_ui=False):
    output, root = Path(output), Path(root)
    if output.exists():
        raise ValueError('portable_check_output_exists')
    receipt = {'kind': 'insights_portable_offline_check', 'passed': False,
               'dem_parsed': False, 'game_started': False, 'capture_started': False,
               'network_used': False}
    try:
        if not getattr(sys, 'frozen', False):
            raise ValueError('portable_check_requires_frozen_app')
        value = verify_application(root)
        from .replay_runtime import worker_directory, read_runtime_manifest, WORKER_NAME
        runtime = worker_directory(root)
        read_runtime_manifest(runtime, verify_files=True)
        with tempfile.TemporaryDirectory(prefix='insights-worker-check-') as folder:
            result_path = Path(folder) / 'worker.json'
            process = subprocess.run([str(runtime / WORKER_NAME), '--offline-self-check', str(result_path)],
                cwd=root, capture_output=True, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            if process.returncode or not result_path.is_file() or result_path.stat().st_size > 16384:
                raise ValueError('portable_worker_self_check_failed')
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('passed') is not True or result.get('dem_parsed') is not False:
                raise ValueError('portable_worker_self_check_incomplete')
            receipt['worker'] = result
        receipt['application_files_verified'] = len(value['files'])
        if check_ui:
            receipt['ui'] = check_hidden_ui(root)
        receipt['passed'] = True
    except Exception as exc:
        receipt['error_type'] = type(exc).__name__
        if str(exc).startswith('portable_') and len(str(exc)) < 120 and ' ' not in str(exc):
            receipt['error_code'] = str(exc)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    return 0 if receipt['passed'] else 1
