"""Fixed portable replay worker path and bounded artifact integrity checks."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

WORKER_NAME = 'Dota2ReplayWorker.exe'
RUNTIME_KIND = 'isolated_portable_replay_worker'


def worker_directory(root):
    return Path(root).resolve() / 'runtime/replay'


def file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_runtime_manifest(directory, *, verify_files=False):
    directory = Path(directory).resolve(strict=True)
    with (directory / 'runtime-manifest.json').open('rb') as stream:
        raw = stream.read(4 * 1024 ** 2 + 1)
    if len(raw) > 4 * 1024 ** 2:
        raise ValueError('portable_replay_manifest_size')
    value = json.loads(raw)
    from .replay_archive import GEM_VERSION, GEM_COMMIT
    if (not isinstance(value, dict) or value.get('kind') != RUNTIME_KIND
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('worker') != WORKER_NAME or not isinstance(value.get('files'), list)
            or value.get('gem_version') != GEM_VERSION or value.get('gem_commit') != GEM_COMMIT
            or not 1 <= len(value['files']) <= 5000):
        raise ValueError('portable_replay_manifest_invalid')
    paths = set()
    for row in value['files']:
        if not isinstance(row, dict):
            raise ValueError('portable_replay_manifest_row')
        name, digest = row.get('path'), row.get('sha256')
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                or name in paths or name == 'runtime-manifest.json'
                or not isinstance(digest, str) or re.fullmatch(r'[a-f0-9]{64}', digest) is None):
            raise ValueError('portable_replay_manifest_path')
        paths.add(name)
        path = directory / name
        if verify_files or name == WORKER_NAME:
            if not path.is_file() or not path.resolve().is_relative_to(directory) or file_sha256(path) != digest:
                raise ValueError('portable_replay_file_changed')
    if WORKER_NAME not in paths:
        raise ValueError('portable_replay_worker_missing')
    return value


def replay_command(root, source, job, *, frozen=None):
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    if frozen:
        directory = worker_directory(root)
        read_runtime_manifest(directory)
        command = [str(directory / WORKER_NAME)]
    else:
        command = [sys.executable, '-B', '-u', str(Path(root) / 'tools/run_replay_job.py')]
    return [*command, '--source', str(source), '--job', str(job)]
