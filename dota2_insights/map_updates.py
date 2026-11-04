"""Immutable local map revisions, change reports and versioned extraction hooks.

Capturing or extracting a map never certifies calibration or activates it for
production. Source assets stay under the ignored local revision store.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .map_assets import compiled_blocks
from .map_holdout import load, save, sha
from .map_root_assets import grid, trm
from .valve_resources import directory_bytes, read_vpk_directory, _read_resource
from .virtual_map import MapIdentity, digest

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'source2-observed-v1'
REQUIRED = ('maps/dota.vmap_c', 'maps/dota.gnv', 'maps/dota.trm', 'maps/dota.vhcg',
    'maps/dota/world_physics.vmdl_c', 'maps/dota/entities/default_ents.vents_c')


def guard():
    from .replay_workbench import game_running
    if game_running():
        raise InterruptedError('map_update_requires_game_closed')


def _revision_id(identity, steam_sha):
    return digest({'identity': identity, 'steam_inf_sha256': steam_sha})


def _content_id(resources):
    return digest([{k: r[k] for k in ('resource', 'bytes', 'sha256')} for r in resources])


def _probe(bodies):
    """Eligibility for an attempted decode, not a promise the decode succeeds."""
    issues = []
    for name in REQUIRED:
        if name not in bodies:
            issues.append({'resource': name, 'reason': 'required_resource_missing'})
            continue
        try:
            raw = bodies[name]
            if name.endswith('.gnv'):
                grid(raw)
            elif name.endswith('.trm'):
                trm(raw)
            elif name.endswith('_c'):
                header = compiled_blocks(raw)
                if header['resource_version'] != 1:
                    raise ValueError('resource_version_requires_adapter')
            # VHCG stays opaque; it is fingerprinted, not a height decoder.
        except ValueError as exc:
            issues.append({'resource': name, 'reason': str(exc)[:160]})
    return {'eligible_profile': PROFILE if not issues else None, 'issues': issues,
        'meaning': 'observed_headers_only_full_decoder_may_still_refuse',
        'vhcg_height_decoded': False}


def inspect_installation(game):
    """Read-only, bounded, source-stable snapshot of the installed standard map."""
    guard()
    game = Path(game).resolve(strict=True)
    steam = (game/'steam.inf').read_bytes()
    if len(steam) > 65536:
        raise ValueError('map_update_steam_metadata_budget')
    fields = dict(line.split('=', 1) for line in steam.decode('utf-8-sig').splitlines() if '=' in line)
    source = game/'maps/dota.vpk'
    raw = directory_bytes(source)
    entries, start = read_vpk_directory(raw)
    if not 1 <= len(entries) <= 20000:
        raise ValueError('map_update_resource_count_budget')
    resources, bodies, total = [], {}, 0
    for index, (name, entry) in enumerate(sorted(entries.items())):
        if index % 64 == 0:
            guard()
        if entry.archive_index != 0x7FFF:
            raise ValueError('split_map_archive_requires_adapter')
        body, _ = _read_resource(source, entry, start, 16*1024**2)
        total += len(body)
        if total > 128*1024**2:
            raise ValueError('map_update_payload_budget')
        resources.append({'resource': name, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
            'crc32': f'{entry.crc32:08x}'})
        if name in REQUIRED:
            bodies[name] = body
    identity = asdict(MapIdentity('dota', int(fields['ClientVersion']), hashlib.sha256(raw).hexdigest()))
    steam_sha = hashlib.sha256(steam).hexdigest()
    snapshot = {'schema_version': 1, 'kind': 'local_map_revision', 'identity': identity,
        'steam_inf_sha256': steam_sha, 'map_vpk_bytes': len(raw), 'resources': resources,
        'map_content_sha256': _content_id(resources), 'probe': _probe(bodies),
        'revision_id': _revision_id(identity, steam_sha),
        'calibration_status': 'unverified', 'production_ready': False,
        'external_dependencies_verified': False}
    if directory_bytes(source) != raw or (game/'steam.inf').read_bytes() != steam:
        raise ValueError('map_update_source_changed_during_inspection')
    guard()
    return snapshot


def validate(snapshot):
    if (type(snapshot.get('schema_version')) is not int or snapshot.get('schema_version') != 1
            or snapshot.get('kind') != 'local_map_revision'
            or snapshot.get('calibration_status') != 'unverified' or snapshot.get('production_ready') is not False
            or snapshot.get('external_dependencies_verified') is not False):
        raise ValueError('map_revision_schema_or_certification_invalid')
    identity = asdict(MapIdentity(**snapshot['identity']))
    if identity != snapshot['identity'] or not re.fullmatch(r'[a-f0-9]{64}', snapshot['steam_inf_sha256']):
        raise ValueError('map_revision_identity_invalid')
    if snapshot['revision_id'] != _revision_id(identity, snapshot['steam_inf_sha256']):
        raise ValueError('map_revision_id_mismatch')
    resources = snapshot['resources']
    if not isinstance(resources, list) or not 1 <= len(resources) <= 20000:
        raise ValueError('map_revision_resource_budget')
    names = []
    for item in resources:
        name = item['resource']
        if (not isinstance(name, str) or not 1 <= len(name) <= 2048 or '\\' in name
                or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))
                or type(item['bytes']) is not int or not 0 <= item['bytes'] <= 16*1024**2
                or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])):
            raise ValueError('map_revision_resource_invalid')
        names.append(name)
    if names != sorted(set(names)) or snapshot['map_content_sha256'] != _content_id(resources):
        raise ValueError('map_revision_content_manifest_invalid')
    return snapshot


def compare(before, after):
    validate(after)
    if before is None:
        return {'status': 'baseline_missing', 'to_revision': after['revision_id'],
            'next_action': 'capture_baseline', 'production_ready': False}
    validate(before)
    old = {r['resource']: r for r in before['resources']}
    new = {r['resource']: r for r in after['resources']}
    changed = [name for name in sorted(old.keys() & new.keys())
        if (old[name]['sha256'], old[name]['bytes']) != (new[name]['sha256'], new[name]['bytes'])]
    added, removed = sorted(new.keys()-old.keys()), sorted(old.keys()-new.keys())
    same_payload = before['map_content_sha256'] == after['map_content_sha256']
    same_archive = before['identity']['map_asset_sha256'] == after['identity']['map_asset_sha256']
    if before['identity']['map_name'] != after['identity']['map_name']:
        status = 'map_variant_changed'
    elif changed or added or removed:
        status = 'map_content_changed'
    elif not same_archive:
        status = 'archive_repacked_same_resources'
    elif before['identity']['client_build'] != after['identity']['client_build']:
        status = 'client_changed_same_map'
    elif before['steam_inf_sha256'] != after['steam_inf_sha256']:
        status = 'installation_metadata_changed'
    else:
        status = 'unchanged'
    requires_decode = bool(changed or added or removed or status == 'map_variant_changed')
    return {'status': status, 'from_revision': before['revision_id'], 'to_revision': after['revision_id'],
        'from_identity': before['identity'], 'to_identity': after['identity'],
        'added': added, 'removed': removed, 'changed': changed,
        'same_map_archive_bytes': same_archive, 'same_resource_payloads': same_payload,
        'static_payload_reuse_candidate': same_payload and status != 'map_variant_changed',
        'requires_new_extraction': requires_decode,
        'requires_new_validation': status != 'unchanged',
        'next_action': ('capture_and_update_adapter' if after['probe']['eligible_profile'] is None
            else 'capture_and_extract_unverified' if requires_decode
            else 'keep_revision' if status == 'unchanged' else 'capture_and_revalidate_context'),
        'calibration_inherited': False, 'production_ready': False,
        'external_dependency_compatibility': 'unknown'}


def read_revision(path):
    path = Path(path).resolve(strict=True)
    if (path/'COMPLETE').read_text(encoding='ascii').strip() != sha(path/'snapshot.json'):
        raise ValueError('map_revision_receipt_changed')
    snapshot = validate(load(path/'snapshot.json', limit=8*1024**2))
    if path.name != snapshot['revision_id']:
        raise ValueError('map_revision_directory_mismatch')
    if sha(path/'source/maps/dota.vpk') != snapshot['identity']['map_asset_sha256']:
        raise ValueError('map_revision_source_archive_changed')
    if sha(path/'source/steam.inf') != snapshot['steam_inf_sha256']:
        raise ValueError('map_revision_source_metadata_changed')
    return snapshot


def latest_revision(store):
    store = Path(store).resolve()
    pointer = store/'latest-capture.json'
    if not pointer.exists():
        return None
    value = load(pointer, limit=4096)
    identifier = value.get('revision_id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{64}', identifier):
        raise ValueError('map_revision_pointer_invalid')
    path = store/'revisions'/identifier
    snapshot = read_revision(path)
    if sha(path/'snapshot.json') != value.get('snapshot_sha256'):
        raise ValueError('map_revision_pointer_binding')
    return path, snapshot


def _publish_blob(blobs, raw, expected):
    blobs.mkdir(parents=True, exist_ok=True)
    target = blobs/(expected+'.vpk')
    if not target.exists():
        temporary = blobs/('.blob-'+uuid.uuid4().hex+'.part')
        try:
            with temporary.open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            # Only our uniquely named temporary file, not a historical asset.
            temporary.unlink(missing_ok=True)
    if sha(target) != expected:
        raise ValueError('map_revision_blob_changed_or_incomplete')
    return target


def capture(game, store):
    from .history import checked_output
    store = checked_output(ROOT, store)
    snapshot = inspect_installation(game)
    revision = store/'revisions'/snapshot['revision_id']
    if revision.exists():
        read_revision(revision)
    else:
        game = Path(game).resolve(strict=True)
        raw, steam = directory_bytes(game/'maps/dota.vpk'), (game/'steam.inf').read_bytes()
        if (hashlib.sha256(raw).hexdigest() != snapshot['identity']['map_asset_sha256']
                or hashlib.sha256(steam).hexdigest() != snapshot['steam_inf_sha256']):
            raise ValueError('map_update_source_changed_before_capture')
        guard()
        blob = _publish_blob(store/'blobs', raw, snapshot['identity']['map_asset_sha256'])
        stage = store/'staging'/('capture-'+uuid.uuid4().hex)
        (stage/'source/maps').mkdir(parents=True, exist_ok=False)
        try:
            # Only owned immutable game bytes, within this store. Unchanged
            # archives share disk space; no link to another repo or install.
            try:
                os.link(blob, stage/'source/maps/dota.vpk')
            except OSError:
                with (stage/'source/maps/dota.vpk').open('xb') as stream:
                    stream.write(raw)
            (stage/'source/steam.inf').write_bytes(steam)
            snapshot['captured_at_utc'] = datetime.now(timezone.utc).isoformat()
            save(stage/'snapshot.json', snapshot)
            if (directory_bytes(game/'maps/dota.vpk') != raw or (game/'steam.inf').read_bytes() != steam):
                raise ValueError('map_update_source_changed_during_capture')
            guard()
            (stage/'COMPLETE').write_text(sha(stage/'snapshot.json')+'\n', encoding='ascii')
            revision.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(stage, revision)
            except FileExistsError:
                read_revision(revision)
        except Exception as exc:
            if stage.exists():
                save(stage/'FAILED.json', {'state': 'capture_failed', 'error': type(exc).__name__,
                    'reason': str(exc)[:400], 'production_ready': False})
            raise
    snapshot = read_revision(revision)
    guard()
    if (sha(Path(game)/'maps/dota.vpk') != snapshot['identity']['map_asset_sha256']
            or sha(Path(game)/'steam.inf') != snapshot['steam_inf_sha256']):
        raise ValueError('map_update_source_changed_before_pointer')
    pointer = {'schema_version': 1, 'revision_id': snapshot['revision_id'],
        'snapshot_sha256': sha(revision/'snapshot.json'), 'meaning': 'latest_capture_not_production_selection'}
    temporary = store/('latest-capture.'+uuid.uuid4().hex+'.tmp')
    save(temporary, pointer)
    os.replace(temporary, store/'latest-capture.json')
    return revision, snapshot


@dataclass(frozen=True)
class MapUpdateAdapter:
    profile: str
    extract: object
    accepts: object = None

    def __post_init__(self):
        if (not isinstance(self.profile, str) or not re.fullmatch(r'[a-z0-9-]{1,64}', self.profile) or not callable(self.extract)
                or self.accepts is not None and not callable(self.accepts)):
            raise ValueError('map_update_adapter_invalid')

    def supports(self, snapshot):
        if self.accepts is None:
            return snapshot['probe']['eligible_profile'] == self.profile
        accepted = self.accepts(snapshot)
        if type(accepted) is not bool:
            raise ValueError('map_update_adapter_acceptance_must_be_bool')
        return accepted


def _extract_current(source, output, decoder):
    # Existing bounded extractors, unchanged algorithms and their own receipts.
    from tools.extract_map_root_assets import run as roots
    from tools.extract_map_landmarks import run as landmarks
    from tools.extract_map_collision import run as collision
    roots(source, output/'root')
    landmarks(source, decoder, output/'landmarks')
    collision(source, decoder, output/'collision')
    return {'root': 'root-assets.json', 'landmarks': 'landmarks.json', 'collision': 'collision.json'}


ADAPTERS = (MapUpdateAdapter(PROFILE, _extract_current),)


def _output_files(folder):
    folder = Path(folder)
    if folder.is_symlink() or folder.is_junction():
        raise ValueError('map_adapter_output_link_refused')
    folder = folder.resolve(strict=True)
    result, total, directories, pending = {}, 0, 0, [folder]
    while pending:
        # Bound enumeration while walking, rather than collecting an
        # unbounded rglob list before checking the file/directory budgets.
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink() or path.is_junction():
                    raise ValueError('map_adapter_output_link_refused')
                if entry.is_dir(follow_symlinks=False):
                    directories += 1
                    if directories > 64:
                        raise ValueError('map_adapter_directory_budget')
                    pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError('map_adapter_regular_file_required')
                stat = path.stat()
                total += stat.st_size
                if len(result) >= 128 or total > 256*1024**2:
                    raise ValueError('map_adapter_payload_budget')
                fingerprint = sha(path)
                after = path.stat()
                if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise ValueError('map_adapter_payload_changed_during_read')
                result[path.relative_to(folder).as_posix()] = fingerprint
    return dict(sorted(result.items()))


def read_prepared(path, revision=None):
    """Consumer hook: reverify the entire extraction, including mesh/text bytes."""
    path = Path(path).resolve(strict=True)
    if (path/'COMPLETE').read_text(encoding='ascii').strip() != sha(path/'extraction.json'):
        raise ValueError('map_extraction_receipt_changed')
    result = load(path/'extraction.json')
    if (type(result.get('schema_version')) is not int or result.get('schema_version') != 1
            or result.get('kind') != 'map_revision_extraction' or result.get('state') != 'extracted_unverified'
            or result.get('calibration_status') != 'unverified' or result.get('production_ready') is not False
            or set(result['outputs']) != {'root', 'landmarks', 'collision'}):
        raise ValueError('map_extraction_schema_or_certification_invalid')
    MapIdentity(**result['identity'])
    if revision is not None:
        snapshot = read_revision(revision)
        if result['revision_id'] != snapshot['revision_id'] or result['identity'] != snapshot['identity']:
            raise ValueError('map_extraction_revision_mismatch')
    for name, spec in result['outputs'].items():
        if (not isinstance(spec['metadata'], str) or not spec['metadata'].startswith(name+'/')
                or spec['metadata'].split('/') != [name, Path(spec['metadata']).name]):
            raise ValueError('map_extraction_metadata_path_invalid')
        if _output_files(path/name) != spec.get('files_sha256'):
            raise ValueError('map_extraction_payload_changed')
        metadata = path/spec['metadata']
        if (sha(metadata) != spec['sha256']
                or (path/name/'COMPLETE').read_text(encoding='ascii').strip() != spec['sha256']):
            raise ValueError('map_extraction_child_receipt_changed')
    return result


def prepare_revision(revision, *, decoder, adapters=ADAPTERS):
    """New extraction attempt; failure never replaces old output or readiness."""
    from .history import checked_output
    revision = Path(revision).resolve(strict=True)
    store = checked_output(ROOT, revision.parent.parent)
    snapshot = read_revision(revision)
    adapter = next((a for a in adapters if a.supports(snapshot)), None)
    if adapter is None:
        raise ValueError('map_update_requires_new_adapter')
    guard()
    output = store/'derived'/snapshot['revision_id']/('attempt-'+uuid.uuid4().hex)
    output.mkdir(parents=True, exist_ok=False)
    try:
        outputs = adapter.extract(revision/'source', output, decoder)
        if set(outputs) != {'root', 'landmarks', 'collision'}:
            raise ValueError('map_adapter_output_contract_invalid')
        receipts = {}
        for name, filename in outputs.items():
            if Path(filename).name != filename:
                raise ValueError('map_adapter_output_path_invalid')
            folder = output/name
            if (folder/'COMPLETE').read_text(encoding='ascii').strip() != sha(folder/filename):
                raise ValueError('map_adapter_output_receipt_changed')
            data = load(folder/filename)
            if (type(data.get('client_build')) is not int or data['client_build'] != snapshot['identity']['client_build']
                    or data['map_asset_sha256'] != snapshot['identity']['map_asset_sha256']):
                raise ValueError('map_adapter_output_identity_mismatch')
            for flag in ('production_ready', 'map_calibration_verified', 'world_placement_verified',
                    'physics_world_placement_verified', 'walkable_ground_verified', 'ward_legality_verified',
                    'independent_image_correspondence_verified'):
                if flag in data and data[flag] is not False:
                    raise ValueError('map_adapter_cannot_certify_raw_extraction')
            receipts[name] = {'metadata': name+'/'+filename, 'sha256': sha(folder/filename),
                'files_sha256': _output_files(folder)}
        if read_revision(revision) != snapshot:
            raise ValueError('map_revision_changed_during_extraction')
        guard()
        result = {'schema_version': 1, 'kind': 'map_revision_extraction', 'revision_id': snapshot['revision_id'],
            'identity': snapshot['identity'], 'adapter_profile': adapter.profile, 'outputs': receipts,
            'state': 'extracted_unverified', 'calibration_status': 'unverified', 'production_ready': False}
        for name, spec in receipts.items():
            if _output_files(output/name) != spec['files_sha256']:
                raise ValueError('map_adapter_payload_changed_before_publish')
        save(output/'extraction.json', result)
        (output/'COMPLETE').write_text(sha(output/'extraction.json')+'\n', encoding='ascii')
        return output, result
    except Exception as exc:
        save(output/'FAILED.json', {'state': 'failed', 'revision_id': snapshot['revision_id'],
            'adapter_profile': adapter.profile, 'error': type(exc).__name__, 'reason': str(exc)[:400],
            'production_ready': False})
        raise


def check_prototype(package, installed):
    """Adapter for future launchers: historical display may stay open, never live-rebind."""
    from .map_prototype import open_scene
    validate(installed)
    scene = open_scene(package)
    same_map = scene['map_asset_sha256'] == installed['identity']['map_asset_sha256']
    same_build = scene['client_build'] == installed['identity']['client_build']
    ward_context = None
    if scene.get('ward_candidates') is not None:
        from .ward_map import check_installation
        from .virtual_map import VirtualMap
        ward_context = check_installation(scene['ward_candidates'], VirtualMap.from_dict(scene['virtual_map_model']), installed)
    return {'prototype_client_build': scene['client_build'], 'installed_identity': installed['identity'], 'wards': ward_context,
        'status': 'same_build_and_map' if same_map and same_build else 'client_changed_same_map' if same_map else 'map_changed',
        'historical_view_allowed': True, 'reuse_for_current_game_allowed': False,
        'requires_new_validation': not (same_map and same_build),
        'calibration_inherited': False, 'production_ready': False}
