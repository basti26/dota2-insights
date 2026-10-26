"""Build a separate, pinned replay executable from existing local dependencies."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.replay_archive import GEM_COMMIT, GEM_VERSION
from dota2_insights.replay_runtime import RUNTIME_KIND, WORKER_NAME, file_sha256

DEPENDENCIES = {'gem-dota': GEM_VERSION, 'numpy': '2.5.4', 'pandas': '3.0.6', 'protobuf': '7.36.2',
    'python-snappy': '0.7.3', 'rich': '15.0.0', 'zstandard': '0.25.0', 'python-dateutil': '2.9.0.post0',
    'tzdata': '2026.5', 'cramjam': '2.14.0', 'markdown-it-py': '4.2.0', 'Pygments': '2.21.0',
    'mdurl': '0.1.2', 'six': '1.17.0'}


def dependency_inputs(root=ROOT):
    """No installation/download, arbitrary target traversal, or personal data."""
    root = Path(root).resolve()
    deps = root / '.replay-deps'
    checkout = root / '.parser-source'
    commit = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], check=True,
                            capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', '-C', str(checkout), 'status', '--porcelain'], check=True,
                           capture_output=True, text=True).stdout.strip()
    if commit != GEM_COMMIT or dirty:
        raise ValueError('pinned_gem_checkout_mismatch')
    distributions = {d.metadata['Name'].casefold(): d for d in importlib.metadata.distributions(path=[str(deps)])}
    selected = {}
    for name, version in DEPENDENCIES.items():
        distribution = distributions.get(name.casefold())
        if distribution is None or distribution.version != version:
            raise ValueError('pinned_replay_dependency_mismatch:' + name)
        for entry in distribution.files or ():
            path = Path(distribution.locate_file(entry)).resolve()
            if (path.is_file() and path.is_relative_to(deps) and '.dist-info' in entry.parts[0]
                    and any(part.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')) for part in entry.parts)):
                selected[path] = 'runtime/licenses/' + str(entry).replace('\\', '/')
    for path in sorted((deps / 'gem').rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        relative = path.relative_to(deps / 'gem')
        original = checkout / 'src/gem' / relative
        if (not path.resolve().is_relative_to(deps) or not original.is_file()
                or file_sha256(original) != file_sha256(path)):
            raise ValueError('installed_gem_differs_from_pinned_checkout')
        selected[path] = 'gem/' + relative.as_posix()
    for name in ('replay_archive.py', 'replay_workbench.py', 'replay_runtime.py', 'replay_holdout.py',
                 'portable_replay.py', 'replay_visibility_recording.py', 'replay_phase_evidence.py',
                 'replay_death_semantics.py', 'replay_periods.py', 'replay_fact_review.py'):
        selected[root / 'dota2_insights' / name] = 'dota2_insights/' + name
    selected[checkout / 'LICENSE'] = 'runtime/licenses/gem-dota/LICENSE'
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise ValueError('cpython_license_missing')
    selected[python_license] = 'runtime/licenses/CPython/LICENSE.txt'
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new runtime/replay folder under the app')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('build on Windows')
    output = args.output.resolve()
    if output.exists():
        raise ValueError('replay_worker_output_must_be_new')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    build = ROOT / 'build' / ('replay-worker-' + stamp)
    build.mkdir(parents=True, exist_ok=False)
    selected = dependency_inputs()
    from dota2_insights.portable_app import public_locks
    from dota2_insights.portable_replay import schema_from_source
    lockfile, schema = build / 'validation-locks.json', build / 'replay_footer_schema.pb'
    lockfile.write_text(json.dumps(public_locks(ROOT)), encoding='utf-8')
    schema.write_bytes(schema_from_source(ROOT / '.replay-deps/gem/proto/demo_pb2.py'))
    selected[lockfile] = 'data/validation-locks.json'
    selected[schema] = 'data/replay_footer_schema.pb'
    entry = ROOT / 'tools/run_replay_job.py'
    before = {path: file_sha256(path) for path in [*selected, entry, Path(__file__).resolve()]}
    # This builder runs in its own process: pandas/protobuf/numpy from the
    # optional parser never replace the live application's dependency versions.
    sys.path.insert(0, str(ROOT / '.replay-deps'))
    from PyInstaller.__main__ import run
    arguments = ['--noconfirm', '--console', '--onedir', '--contents-directory', '.',
        '--name', WORKER_NAME.removesuffix('.exe'), '--distpath', str(build / 'dist'),
        '--workpath', str(build / 'work'), '--specpath', str(build),
        '--paths', str(ROOT), '--paths', str(ROOT / '.replay-deps')]
    for package in ('gem', 'numpy', 'pandas', 'google.protobuf', 'snappy', 'cramjam', 'zstandard', 'tzdata'):
        arguments.extend(['--collect-all', package])
    for name in DEPENDENCIES:
        arguments.extend(['--copy-metadata', name])
    arguments.extend(['--hidden-import', 'google._upb._message'])
    for package in ('torch', 'tensorflow', 'paddle', 'matplotlib', 'IPython', 'pytest',
                    'scipy', 'numba', 'pyarrow', 'fastparquet', 'cv2', 'tkinter', 'mss',
                    'rapidocr_onnxruntime', 'onnxruntime', 'pyaudiowpatch'):
        arguments.extend(['--exclude-module', package])
    for path, target in selected.items():
        arguments.extend(['--add-data', str(path) + ';' + str(Path(target).parent)])
    arguments.append(str(entry))
    run(arguments)
    if any(file_sha256(path) != digest for path, digest in before.items()):
        raise ValueError('replay_worker_build_inputs_changed')
    built = build / 'dist' / WORKER_NAME.removesuffix('.exe')
    # pip records the developer's local checkout in direct_url.json. Runtime
    # version lookup needs METADATA only; never distribute that local path.
    omitted = []
    for path in built.glob('*.dist-info/direct_url.json'):
        if not path.resolve().is_relative_to(built.resolve()):
            raise ValueError('replay_metadata_outside_build')
        omitted.append(path.relative_to(built).as_posix())
        path.unlink()
    import shutil
    shutil.copytree(built, output)
    files = [{'path': path.relative_to(output).as_posix(), 'sha256': file_sha256(path),
              'bytes': path.stat().st_size} for path in sorted(output.rglob('*')) if path.is_file()]
    manifest = {'kind': RUNTIME_KIND, 'schema_version': 1, 'worker': WORKER_NAME,
        'gem_version': GEM_VERSION, 'gem_commit': GEM_COMMIT, 'python': sys.version.split()[0],
        'built_at_utc': datetime.now(timezone.utc).isoformat(), 'dependencies': DEPENDENCIES,
        'files': files, 'inputs': [{'target': target, 'sha256': before[path]} for path, target in selected.items()],
        'entry_source_sha256': before[entry], 'builder_sha256': before[Path(__file__).resolve()],
        'omitted_local_install_metadata': omitted,
        'policy': 'isolated_fixed_worker_public_pinned_dependencies_no_user_data_no_download'}
    (output / 'runtime-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'runtime': str(output), 'files': len(files), 'bytes': sum(row['bytes'] for row in files)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
