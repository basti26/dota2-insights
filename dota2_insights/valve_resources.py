"""Bounded Valve VPK bytes for offline source assets; no live-helper import."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import struct
import zlib

MAX_DIRECTORY_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def directory_bytes(path):
    path = Path(path)
    if path.is_symlink() or path.is_junction():
        raise ValueError('vpk_directory_link_refused')
    before = path.stat()
    with path.open('rb') as stream:
        raw = stream.read(MAX_DIRECTORY_BYTES + 1)
    after = path.stat()
    if len(raw) > MAX_DIRECTORY_BYTES or (before.st_size,before.st_mtime_ns,before.st_ino) != (after.st_size,after.st_mtime_ns,after.st_ino):
        raise ValueError('vpk_directory_budget_or_change')
    return raw


@dataclass(frozen=True)
class VPKEntry:
    resource: str
    crc32: int
    preload: bytes
    archive_index: int
    offset: int
    length: int


def read_vpk_directory(content: bytes) -> tuple[dict[str, VPKEntry], int]:
    """Read only the bounded VPK directory tree, including embedded preload."""
    if not 12 <= len(content) <= MAX_DIRECTORY_BYTES:
        raise ValueError("invalid_official_vpk_directory_size")
    signature, version, tree_size = struct.unpack_from("<III", content)
    header_size = 12 if version == 1 else 28
    if (signature != 0x55AA1234 or version not in (1, 2)
            or not 0 < tree_size <= MAX_DIRECTORY_BYTES
            or header_size + tree_size > len(content)):
        raise ValueError("invalid_official_vpk_directory_header")
    tree, position, entries = content[header_size:header_size + tree_size], 0, {}

    def string() -> str:
        nonlocal position
        try:
            end = tree.index(0, position)
            value = tree[position:end].decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid_official_vpk_directory_string") from exc
        position = end + 1
        return value

    while extension := string():
        while folder := string():
            while filename := string():
                if position + 18 > len(tree):
                    raise ValueError("truncated_official_vpk_entry")
                crc, preload, archive, offset, length, terminator = struct.unpack_from(
                    "<IHHIIH", tree, position)
                position += 18
                if terminator != 0xFFFF or position + preload > len(tree):
                    raise ValueError("invalid_official_vpk_entry")
                prefix = tree[position:position + preload]
                position += preload
                resource = f"{folder}/{filename}.{extension}"
                parts = resource.split("/")
                if (resource in entries or "\\" in resource
                        or any(part in {"", ".", ".."} for part in parts)):
                    raise ValueError("invalid_or_duplicate_official_vpk_resource")
                entries[resource] = VPKEntry(resource, crc, prefix, archive, offset, length)
    if position != len(tree):
        raise ValueError("trailing_official_vpk_directory_data")
    return entries, header_size + tree_size


def _read_resource(directory: Path, entry: VPKEntry, embedded_start: int,
                   limit: int) -> tuple[bytes, Path]:
    if entry.length + len(entry.preload) > limit:
        raise ValueError("official_portrait_resource_too_large")
    archive = (directory if entry.archive_index == 0x7FFF else
               directory.with_name(f"pak01_{entry.archive_index:03d}.vpk"))
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("official_asset_archive_too_large")
    with archive.open("rb") as handle:
        handle.seek(entry.offset + (embedded_start if archive == directory else 0))
        content = entry.preload + handle.read(entry.length)
    if (len(content) != entry.length + len(entry.preload)
            or zlib.crc32(content) & 0xFFFFFFFF != entry.crc32):
        raise ValueError("official_asset_resource_integrity_failed")
    return content, archive
