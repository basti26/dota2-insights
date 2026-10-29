"""Merge explicit completed analysis batches without double counting or overwrites."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path

from .pi_analysis_snapshot import catalog_projection
from .postgame_store import bounded, mid, sha, HEX
from .replay_holdout import refuse_locked_match
from .ward_reference_export import build

KIND = 'completed_analysis_batch_registry'


def decoded(value, limit):
    raw = base64.b64decode(value, validate=True)
    if len(raw) > limit:
        raise ValueError('analysis_registry_body_budget')
    return raw


def version(entry):
    identifier = mid(entry['match_id'])
    refuse_locked_match(identifier)
    mraw = decoded(entry['manifest_base64'], 256*1024)
    manifest = json.loads(mraw)
    refuse_locked_match(str(manifest.get('match_id')))
    if manifest.get('match_id') != identifier:
        raise ValueError('analysis_registry_manifest_identity')
    raw = decoded(entry['wards_base64'], 5*1024**2)
    packet = build(manifest, raw)
    catalog = catalog_projection(entry['catalog'])
    if (catalog['download_status'] != 'verified' or catalog['analysis_status'] != 'complete'
            or not HEX.fullmatch(str(catalog['sha256'])) or catalog['sha256'] != manifest.get('source_sha256')
            or catalog['analysis_sha256'] != packet['source_wards_sha256']
            or manifest.get('source_unchanged_stat') is not True or manifest.get('stop_reason') != 'replay_eof'):
        raise ValueError('analysis_registry_complete_binding')
    # Origin ordering carries no semantics, but classification changes do.
    catalog['sources'] = sorted(catalog['sources'])
    identity = {'match_id': identifier, 'manifest_sha256': sha(mraw),
                'wards_sha256': sha(raw), 'catalog': catalog}
    source_id = sha(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode())
    return source_id, identity, manifest, {
        'match_id': identifier, 'catalog': catalog,
        'manifest_base64': entry['manifest_base64'], 'wards_base64': entry['wards_base64']}


def merge(batches, output, *, previous=None, selection=None):
    output = Path(output)
    if output.exists():
        raise ValueError('analysis_registry_new_output_required')
    paths, inputs, explicit, old_refs = list(map(Path, batches)), [], {}, {}
    if previous is not None:
        previous = Path(previous)
        raw = bounded(previous, 4*1024**2)
        old = json.loads(raw)
        if old.get('kind') != KIND or type(old.get('schema_version')) is not int or old['schema_version'] != 1:
            raise ValueError('analysis_registry_previous_kind')
        explicit.update(old['explicit_selections'])
        for source in old['batches']:
            path = Path(source['snapshot_path'])
            if (sha(bounded(path, 32*1024**2)) != source['snapshot_sha256']
                    or sha(bounded(path.parent/'receipt.json', 1024**2)) != source['receipt_sha256']):
                raise ValueError('analysis_registry_previous_source_changed')
            paths.append(path.parent)
            old_refs[source['snapshot_sha256']] = source
        inputs.append((previous, raw))
    explicit.update(selection or {})
    for identifier, source_id in explicit.items():
        mid(identifier); refuse_locked_match(identifier)
        if not isinstance(source_id, str) or not HEX.fullmatch(source_id):
            raise ValueError('analysis_registry_selection_invalid')
    if not paths or len(paths) > 128:
        raise ValueError('analysis_registry_batch_budget')
    versions, batch_refs, seen_batches = {}, [], set()
    count = 0
    total_bytes = 0
    for folder in paths:
        sp = folder/'snapshot.json'; rp = folder/'receipt.json'
        raw = bounded(sp, 32*1024**2); rraw = bounded(rp, 1024**2)
        inputs.extend(((sp, raw), (rp, rraw)))
        receipt = json.loads(rraw)
        digest = sha(raw)
        if receipt.get('status') != 'complete' or receipt.get('snapshot_sha256') != digest:
            raise ValueError('analysis_registry_batch_receipt')
        if digest in seen_batches:
            continue
        seen_batches.add(digest)
        total_bytes += len(raw)
        if total_bytes > 128*1024**2:
            raise ValueError('analysis_registry_input_budget')
        frozen = json.loads(raw)
        if (frozen.get('kind') != 'frozen_verified_ward_exports'
                or type(frozen.get('schema_version')) is not int or frozen['schema_version'] != 1
                or frozen.get('raw_replays_transferred') is not False
                or not isinstance(frozen.get('matches'), list) or not 1 <= len(frozen['matches']) <= 256):
            raise ValueError('analysis_registry_snapshot_kind')
        seen_ids, capture_hashes = set(), {}
        for entry in frozen['matches']:
            source_id, identity, manifest, clean = version(entry)
            identifier = identity['match_id']
            if identifier in seen_ids:
                raise ValueError('analysis_registry_duplicate_match_in_batch')
            seen_ids.add(identifier)
            count += 1
            refs = versions.setdefault(identifier, {})
            record = refs.setdefault(source_id, {'identity': identity, 'entry': clean,
                'batch_sha256': [], 'capture_bytes': None, 'capture_alias_sha256': []})
            record['batch_sha256'].append(digest)
            cp = folder/f'capture-{identifier}.json'
            declared = [r for r in receipt.get('matches', []) if r.get('match_id') == identifier]
            if len(declared) != 1 or declared[0].get('wards_sha256') != identity['wards_sha256']:
                raise ValueError('analysis_registry_receipt_match_binding')
            if declared[0].get('context_collected') is True and not cp.is_file():
                raise ValueError('analysis_registry_declared_capture_missing')
            if cp.is_file():
                craw = bounded(cp, 20*1024**2); inputs.append((cp, craw)); capture = json.loads(craw)
                if (capture.get('kind') != 'isolated_postgame_capture_export'
                        or type(capture.get('schema_version')) is not int or capture['schema_version'] != 1
                        or decoded(capture['manifest_base64'], 256*1024) != decoded(clean['manifest_base64'],256*1024)
                        or decoded(capture['wards_base64'], 5*1024**2) != decoded(clean['wards_base64'],5*1024**2)):
                    raise ValueError('analysis_registry_capture_binding')
                context = decoded(capture['context_base64'], 8*1024**2)
                spec = manifest.get('files', {}).get('postgame_context.json', {})
                if spec.get('sha256') != sha(context) or spec.get('bytes') != len(context):
                    raise ValueError('analysis_registry_context_digest')
                record['capture_bytes'] = record['capture_bytes'] or craw
                record['capture_alias_sha256'].append(sha(craw))
                capture_hashes[identifier] = sha(craw)
            if len(refs) > 8 or sum(len(v) for v in versions.values()) > 512:
                raise ValueError('analysis_registry_version_budget')
        if digest in old_refs and capture_hashes != old_refs[digest]['capture_sha256']:
            raise ValueError('analysis_registry_previous_captures_changed')
        batch_refs.append({'snapshot_path': str(sp.resolve()), 'snapshot_sha256': digest,
                           'receipt_sha256': sha(rraw), 'capture_sha256': capture_hashes})
        if len(versions) > 256:
            raise ValueError('analysis_registry_match_budget')
    for identifier, source_id in explicit.items():
        if identifier not in versions or source_id not in versions[identifier]:
            raise ValueError('analysis_registry_selection_not_found')
    chosen, conflicts, inventory, captures = [], [], [], []
    for identifier, alternatives in sorted(versions.items(), key=lambda kv: int(kv[0])):
        selected = explicit.get(identifier) or (next(iter(alternatives)) if len(alternatives) == 1 else None)
        if selected is None:
            conflicts.append(identifier)
        for source_id, record in sorted(alternatives.items()):
            inventory.append({'match_id': identifier, 'source_id': source_id, **record['identity'],
                              'batch_sha256': record['batch_sha256'],
                              'capture_alias_sha256': sorted(set(record['capture_alias_sha256'])),
                              'selected': source_id == selected})
        if selected is not None:
            record = alternatives[selected]
            chosen.append(record['entry'])
            if record['capture_bytes'] is not None:
                captures.append((identifier, record['capture_bytes']))
    now = datetime.now(timezone.utc).isoformat()
    frozen = {'schema_version': 1, 'kind': 'frozen_verified_ward_exports', 'raw_replays_transferred': False,
              'frozen_at_utc': now, 'matches': chosen, 'excluded_catalog_rows': []}
    raw = json.dumps(frozen, ensure_ascii=False, indent=2).encode()
    if len(raw) > 32*1024**2:
        raise ValueError('analysis_registry_output_budget')
    if any(path.read_bytes() != raw for path, raw in inputs):
        raise ValueError('analysis_registry_input_changed')
    result = {'schema_version': 1, 'kind': KIND, 'created_at_utc': now,
              'status': 'complete_with_conflicts' if conflicts else 'complete', 'batches': batch_refs,
              'explicit_selections': explicit, 'versions': inventory,
              'unique_matches': len(versions), 'selected_matches': len(chosen), 'conflicting_matches': conflicts,
              'duplicate_entries': count-sum(len(v) for v in versions.values()),
              'snapshot_sha256': sha(raw) if chosen else None,
              'capture_count': len(captures), 'raw_replays_transferred': False}
    output.mkdir(parents=True)
    if chosen:
        (output/'snapshot.json').write_bytes(raw)
    for identifier, raw in captures:
        (output/f'capture-{identifier}.json').write_bytes(raw)
    (output/'registry.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
