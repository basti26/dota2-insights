"""Public replay lock guard, with no GUI, parser, or capture imports."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_locks(path):
    with path.open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('独立核验锁定清单超限')
    locks = json.loads(raw)
    if (not isinstance(locks, dict) or type(locks.get('schema_version')) is not int
            or locks['schema_version'] != 1 or not isinstance(locks.get('heldout_matches'), list)
            or any(not isinstance(mid, str) or not mid.isascii() or not mid.isdigit() or len(mid) > 20
                   for mid in locks['heldout_matches'])):
        raise ValueError('独立核验锁定清单无效')
    return set(locks['heldout_matches'])


def locked_matches(root=None):
    root = Path(root) if root is not None else ROOT
    public = root / 'data/validation-locks.json'
    if not public.is_file():
        raise ValueError('独立核验锁定清单缺失，请恢复完整项目或程序文件')
    locks = _read_locks(public)
    local = root / 'config/validation-locks.local.json'
    if local.exists() or local.is_symlink():
        locks.update(_read_locks(local))
    return locks


def refuse_locked_match(match_id, root=None):
    root = Path(root) if root is not None else ROOT
    locks = locked_matches(root)
    if str(match_id) in locks:
        raise ValueError('这场属于独立封存比赛，尚未放行，不能在此录制')
    freeze = root / 'diagnostics/ward-independent-validation-20261023/source-freeze-v1/freeze.json'
    if freeze.exists():
        raw = freeze.read_bytes()
        if hashlib.sha256(raw).hexdigest() != freeze.with_suffix('.sha256').read_text(encoding='ascii').strip():
            raise ValueError('独立核验来源冻结指纹变化，请先复查')
        value = json.loads(raw)
        if any(str(row.get('match_id')) == str(match_id) and row.get('role') == 'heldout'
               for row in value.get('matches', [])):
            raise ValueError('这场属于独立封存比赛，尚未放行，不能在此录制')
