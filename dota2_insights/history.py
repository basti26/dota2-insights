"""Explicit, read-only locations for records left in their original repository."""
import json
from pathlib import Path
import re

ROOT_NAMES = ('helper_history', 'helper_releases', 'helper_documents')
RELEASE_PREFIXES = ('foresight-', 'helper-', 'economy-')
RELEASE_APPS = ('Foresight', 'ForesightEconomy', 'Dota2Helper', 'Dota2Economy')
PREVIEW_CONTAINER = re.compile(r'preview-[0-9]{1,8}-(?:local|atlas)')


def release_record_tail(parts):
    """Return the path under diagnostics for a supported release layout."""
    if parts and PREVIEW_CONTAINER.fullmatch(parts[0]):
        parts = parts[1:]
    if (len(parts) >= 3 and parts[0].startswith(RELEASE_PREFIXES)
            and parts[1] in RELEASE_APPS and parts[2] == 'diagnostics'):
        return parts[3:]
    return None


def release_diagnostics(root, *, limit_error):
    """Discover direct releases and one level of versioned local previews."""
    releases = []
    preview_count = 0
    for folder in root.iterdir():
        if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
            continue
        if folder.name.startswith(RELEASE_PREFIXES):
            releases.append(folder)
        elif PREVIEW_CONTAINER.fullmatch(folder.name):
            preview_count += 1
            if preview_count > 100:
                raise ValueError(limit_error)
            for child in folder.iterdir():
                if (child.name.startswith(RELEASE_PREFIXES) and child.is_dir()
                        and not child.is_symlink() and not child.is_junction()):
                    releases.append(child)
                    if len(releases) > 100:
                        raise ValueError(limit_error)
        if len(releases) > 100:
            raise ValueError(limit_error)
    for release in sorted(releases, reverse=True):
        for app in RELEASE_APPS:
            app_dir = release / app
            base = app_dir / 'diagnostics'
            if not any(path.is_symlink() or path.is_junction() for path in (app_dir, base)):
                yield base


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
