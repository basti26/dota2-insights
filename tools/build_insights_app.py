"""Build the offline analysis UI and its separate pinned parser, without downloads."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.portable_app import digest, public_locks

NAME, VERSION = 'Dota2Insights', '0.1.0'
UI_MODULES = ('__init__', 'portable_app', 'history', 'scrollable_panel', 'replay_archive',
    'replay_workbench', 'replay_runtime', 'replay_holdout', 'portable_replay',
    'replay_visibility_recording', 'replay_phase_evidence', 'replay_death_semantics',
    'replay_periods', 'replay_fact_review')


def public_inputs(root=ROOT):
    root = Path(root).resolve()
    public_locks(root)
    names = [*('dota2_insights/' + n + '.py' for n in UI_MODULES),
             'dota2_insights/minimap/__init__.py', 'dota2_insights/minimap/offline_io.py',
             'tools/__init__.py', 'tools/win_insights.py', 'tools/win_replay_analysis.py',
             'data/validation-locks.json', 'docs/PORTABLE_APP.md']
    result = {}
    for name in names:
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('portable_public_source_missing_or_outside')
        result[path] = name
    for name, target in [('LICENSE.txt', 'runtime/licenses/CPython/LICENSE.txt'),
                         ('tcl/tk8.6/license.terms', 'runtime/licenses/Tk/license.terms')]:
        path = Path(sys.base_prefix) / name
        if not path.is_file():
            raise ValueError('portable_python_license_missing')
        result[path] = target
    tcl_license = Path(sys.base_prefix) / 'tcl/tcl8.6/license.terms'
    if tcl_license.is_file():
        result[tcl_license] = 'runtime/licenses/Tcl/license.terms'
    return result


def verify_worker_sources(root, runtime):
    from tools.build_replay_worker import dependency_inputs
    from dota2_insights.replay_runtime import read_runtime_manifest
    from dota2_insights.portable_replay import schema_from_source
    root, runtime = Path(root), Path(runtime)
    value = read_runtime_manifest(runtime, verify_files=True)
    expected = {target: digest(path) for path, target in dependency_inputs(root).items()}
    expected['data/validation-locks.json'] = hashlib.sha256(json.dumps(public_locks(root)).encode()).hexdigest()
    expected['data/replay_footer_schema.pb'] = hashlib.sha256(
        schema_from_source(root / '.replay-deps/gem/proto/demo_pb2.py')).hexdigest()
    if (value.get('entry_source_sha256') != digest(root / 'tools/run_replay_job.py')
            or value.get('builder_sha256') != digest(root / 'tools/build_replay_worker.py')
            or {r['target']: r['sha256'] for r in value['inputs']} != expected):
        raise ValueError('portable_worker_sources_changed')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-runtime', type=Path, help='reuse only a fully verified build of these exact worker sources')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('build on Windows')
    from dota2_insights.replay_archive import _windows_dota_running
    if _windows_dota_running():
        raise ValueError('portable_build_wait_until_game_closed')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    build, release = ROOT / 'build' / ('insights-' + stamp), ROOT / 'dist' / ('insights-' + VERSION + '-' + stamp)
    build.mkdir(parents=True, exist_ok=False)
    release.mkdir(parents=True, exist_ok=False)
    selected = public_inputs()
    before = {path: digest(path) for path in [*selected, Path(__file__).resolve()]}
    os.environ.setdefault('PYINSTALLER_CONFIG_DIR', str(ROOT / 'build/pyinstaller-cache'))
    if args.worker_runtime:
        verify_worker_sources(ROOT, args.worker_runtime)
        backend = args.worker_runtime.resolve()
    else:
        backend = build / 'parser-runtime'
        with (build / 'worker-build.log').open('x', encoding='utf-8') as stream:
            subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'tools/build_replay_worker.py'),
                '--output', str(backend)], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        verify_worker_sources(ROOT, backend)
    from PyInstaller.__main__ import run
    arguments = ['--noconfirm', '--windowed', '--onedir', '--contents-directory', '.',
        '--name', NAME, '--distpath', str(release), '--workpath', str(build / 'work'),
        '--specpath', str(build), '--paths', str(ROOT), '--hidden-import', 'tools.win_replay_analysis']
    for package in ('gem', 'pandas', 'numpy', 'cv2', 'torch', 'tensorflow', 'paddle', 'scipy',
                    'rapidocr_onnxruntime', 'onnxruntime', 'mss', 'pyaudiowpatch'):
        arguments.extend(['--exclude-module', package])
    for path, target in selected.items():
        arguments.extend(['--add-data', str(path) + ';' + str(Path(target).parent)])
    arguments.append(str(ROOT / 'tools/win_insights.py'))
    run(arguments)
    if any(digest(path) != value for path, value in before.items()):
        raise ValueError('portable_build_sources_changed')
    app = release / NAME
    shutil.copytree(backend, app / 'runtime/replay')
    runtime = verify_worker_sources(ROOT, app / 'runtime/replay')
    files = [{'path': path.relative_to(app).as_posix(), 'sha256': digest(path), 'bytes': path.stat().st_size}
             for path in sorted(app.rglob('*')) if path.is_file()]
    manifest = {'kind': 'portable_dota2_insights', 'schema_version': 1, 'version': VERSION,
        'built_at_utc': datetime.now(timezone.utc).isoformat(), 'files': files,
        'source_inputs': [{'target': target, 'sha256': before[path]} for path, target in selected.items()],
        'builder_sha256': before[Path(__file__).resolve()], 'parser_manifest_sha256': digest(app / 'runtime/replay/runtime-manifest.json'),
        'gem_commit': runtime['gem_commit'], 'policy': 'explicit_sources_no_history_no_config_no_downloads'}
    (app / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    check = build / 'frozen-check.json'
    result = subprocess.run([str(app / (NAME + '.exe')), '--offline-self-check', str(check), '--check-ui'],
        cwd=app, capture_output=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not check.is_file() or json.loads(check.read_text(encoding='utf-8')).get('passed') is not True:
        raise ValueError('portable_frozen_check_failed')
    archive = release / (NAME + '-' + VERSION + '-win64.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as stream:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                stream.write(path, path.relative_to(release))
    print(json.dumps({'application': str(app / (NAME + '.exe')), 'archive': str(archive),
        'archive_sha256': digest(archive), 'check_receipt': str(check)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
