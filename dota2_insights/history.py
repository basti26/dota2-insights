"""Explicit, read-only locations for records left in their original repository."""
import json
from pathlib import Path

ROOT_NAMES = ('helper_history', 'helper_releases', 'helper_documents')


def history_roots(config):
    path = Path(config)
    if not path.is_file():
        return {}
    with path.open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('history_config_too_large')
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get('schema_version') != 1
            or type(value.get('schema_version')) is not int or value.get('read_only') is not True
            or not isinstance(value.get('roots'), dict)
            or set(value['roots']) - set(ROOT_NAMES)):
        raise ValueError('history_config_invalid')
    roots = {}
    for name, location in value['roots'].items():
        if not isinstance(location, str) or not location or not Path(location).is_absolute():
            raise ValueError('history_root_must_be_absolute')
        roots[name] = Path(location).resolve()
    return roots


def history_location(config, name):
    if name not in ROOT_NAMES:
        raise ValueError('unknown_history_root')
    path = history_roots(config).get(name)
    if path is None or not path.is_dir():
        raise ValueError('history_directory_unavailable')
    return path


def checked_input(root, value):
    """Accept own analysis artifacts or an explicitly configured history root."""
    root, path = Path(root).resolve(), Path(value).resolve()
    allowed = [root / 'diagnostics', *history_roots(root / 'config/history.local.json').values()]
    if not any(path.is_relative_to(folder) for folder in allowed):
        raise ValueError('analysis_input_outside_configured_roots')
    from .minimap.offline_io import check_input_paths
    check_input_paths(path)
    return path


def checked_output(root, value):
    """All new output belongs to insights; old history never becomes writable."""
    root, path = Path(root).resolve(), Path(value).resolve()
    historical = history_roots(root / 'config/history.local.json').values()
    if not path.is_relative_to(root / 'diagnostics') or any(path.is_relative_to(p) for p in historical):
        raise ValueError('analysis_output_must_be_owned')
    return path
