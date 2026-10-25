"""Load the exact pinned public DEM footer schema without the entity parser."""
import ast
import hashlib
from pathlib import Path

DESCRIPTOR_SHA256 = '0646be11f53d840a1c47ee0d79300aff6f8a29b3a5f40cf5fd209f63612613dc'
SOURCE_SHA256 = 'f11e5da530a85b95e607f5edfe2592aafc006058afd43059e4cb9169f590acb5'


def schema_from_source(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 64 * 1024 or hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('portable_footer_schema_source_mismatch')
    values = [ast.literal_eval(node.args[0]) for node in ast.walk(ast.parse(raw.decode('utf-8')))
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == 'AddSerializedFile' and len(node.args) == 1]
    if len(values) != 1 or not isinstance(values[0], bytes) or hashlib.sha256(values[0]).hexdigest() != DESCRIPTOR_SHA256:
        raise ValueError('portable_footer_schema_descriptor_mismatch')
    return values[0]


def demo_file_info(root=None):
    from google.protobuf import descriptor_pool, message_factory
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    with (root / 'data/replay_footer_schema.pb').open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != DESCRIPTOR_SHA256:
        raise ValueError('portable_footer_schema_integrity_failed')
    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(raw)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName('CDemoFileInfo'))
