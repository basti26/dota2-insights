"""Explicitly authorized offline regression runner with capture/network guards.

Existing tests still own their fixtures and mocks. Guards forbid real devices
and external connections, and never import the live helper or start background replay services.
This does not certify gameplay behavior or child-process sandbox isolation.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import argparse
import gc
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import threading
import traceback
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CollectingTestResult(unittest.TextTestResult):
    def startTest(self, test):
        # The suite has released the previous case by the next start. Collect
        # again before the new test can create an HTTP/worker thread.
        gc.collect()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        # Tests construct Tk objects only on this thread. Collect their closed
        # cycles here rather than in a later HTTP/server allocation thread.
        gc.collect()


def unraisable_recorder(record):
    def hook(event):
        error_type = event.exc_type if event.exc_type is not None else RuntimeError
        error = event.exc_value if event.exc_value is not None else RuntimeError('unraisable exception without value')
        # Never repr(event.object): it can be a Tk object or private payload.
        record('unraisable', error_type, error, event.exc_traceback)
    return hook


def loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def guarded_network(stack):
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_bind, original_lookup = socket.socket.bind, socket.getaddrinfo

    def check(address):
        if not isinstance(address, tuple) or len(address) < 2 or not loopback(address[0]):
            raise OSError('offline_regression_external_network_forbidden')

    def connect(handle, address):
        check(address)
        return original_connect(handle, address)

    def connect_ex(handle, address):
        check(address)
        return original_connect_ex(handle, address)

    def bind(handle, address):
        check(address)
        return original_bind(handle, address)

    def lookup(host, *args, **kwargs):
        if not loopback(host):
            raise OSError('offline_regression_external_dns_forbidden')
        return original_lookup(host, *args, **kwargs)

    stack.enter_context(patch.object(socket.socket, 'connect', connect))
    stack.enter_context(patch.object(socket.socket, 'connect_ex', connect_ex))
    stack.enter_context(patch.object(socket.socket, 'bind', bind))
    stack.enter_context(patch.object(socket, 'getaddrinfo', lookup))
    stack.enter_context(patch.object(socket, 'getfqdn', lambda *_args: 'localhost'))


def forbid_device(*_args, **_kwargs):
    raise RuntimeError('offline_regression_real_capture_or_model_forbidden')


def source_hashes():
    paths = [*ROOT.joinpath('dota2_insights').rglob('*.py'),
             *ROOT.joinpath('tools').glob('*.py'),
             *ROOT.joinpath('tests').glob('test_*.py'), *ROOT.joinpath('data').glob('*.json'),
             *ROOT.joinpath('tools').glob('*.ps1'), *ROOT.glob('Start-*.cmd')]
    return {Path(os.path.relpath(path, ROOT)).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if not path.name.endswith('.local.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pattern', action='append', required=True,
                        help='reviewed test module pattern; repeat to select batches')
    args = parser.parse_args()
    if any('/' in p or '\\' in p or not p.startswith('test_') or not p.endswith('.py') for p in args.pattern):
        parser.error('patterns must select test_*.py under project/tests')
    os.chdir(ROOT)
    output = ROOT / 'diagnostics/offline-tests' / ('run-' + uuid.uuid4().hex[:12])
    output.mkdir(parents=True)
    before = source_hashes()
    result = None
    error = None
    background_errors = []
    def record_background(kind, exception_type, exception, trace):
        background_errors.append({'kind': kind, 'type': exception_type.__name__})
        with (output / 'background-errors.txt').open('a', encoding='utf-8') as log:
            log.write(kind + '\n' + ''.join(traceback.format_exception(exception_type, exception, trace)) + '\n')
    try:
        with ExitStack() as stack:
            original_thread_hook = threading.excepthook
            def thread_hook(args):
                record_background('thread:' + args.thread.name, args.exc_type, args.exc_value, args.exc_traceback)
                original_thread_hook(args)
            stack.enter_context(patch.object(threading, 'excepthook', thread_hook))
            stack.enter_context(patch.object(sys, 'unraisablehook', unraisable_recorder(record_background)))
            import tkinter as tk
            original_tk_init = tk.Tk.__init__
            def tk_init(window, *args, **kwargs):
                original_tk_init(window, *args, **kwargs)
                # Tcl may call a deleted Python after-command without ever
                # reaching Tk.report_callback_exception. Capture that too.
                window.createcommand('bgerror', lambda message: record_background(
                    'tcl_background', RuntimeError, RuntimeError(str(message)), None))
            stack.enter_context(patch.object(tk.Tk, '__init__', tk_init))
            original_tk_hook = tk.Tk.report_callback_exception
            def tk_hook(window, exception_type, exception, trace):
                record_background('tk_callback', exception_type, exception, trace)
                original_tk_hook(window, exception_type, exception, trace)
            stack.enter_context(patch.object(tk.Tk, 'report_callback_exception', tk_hook))
            guarded_network(stack)
            import cv2
            cv2.setNumThreads(1)
            stack.enter_context(patch.object(cv2, 'VideoCapture', forbid_device))
            try:
                import mss
            except ImportError:
                pass
            else:
                stack.enter_context(patch.object(mss, 'mss', forbid_device))
            suite = unittest.TestSuite()
            for pattern in args.pattern:
                suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=pattern))
            with (output / 'unittest.txt').open('w', encoding='utf-8') as log:
                result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=CollectingTestResult).run(suite)
            gc.collect()  # Include final-case destructors while hooks are active.
    except Exception as exc:
        error = {'type': type(exc).__name__, 'message': str(exc)[:400]}
    after = source_hashes()
    receipt = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'patterns': args.pattern,
               'tests_run': result.testsRun if result else 0,
               'failures': len(result.failures) if result else 0,
               'errors': len(result.errors) if result else 0,
               'skipped': len(result.skipped) if result else 0,
               'successful': bool(result and result.wasSuccessful() and not error and not background_errors and before == after),
               'background_errors': background_errors,
               'runner_error': error, 'python': sys.version, 'source_sha256': before,
               'source_unchanged': before == after,
               'changed_source_files': sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k)),
               'guards': ['parent Python loopback only', 'real mss/video capture forbidden',


                          'thread/Tk/Tcl/unraisable errors captured; closed test cycles collected on main thread'],
               'limitations': ['guards are not an OS sandbox and do not automatically propagate into child interpreters',
                               'child process tests require prior source inspection and use owned synthetic workers',
                               'not gameplay, real device, internet, or semantic accuracy validation']}
    (output / 'result.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'evidence': str(output), **{k: receipt[k] for k in
                     ('tests_run', 'failures', 'errors', 'skipped', 'successful', 'runner_error', 'source_unchanged')}}, ensure_ascii=False))
    return 0 if receipt['successful'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
