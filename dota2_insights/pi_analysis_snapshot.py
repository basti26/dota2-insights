"""Read-only, bounded collection of completed Pi exports; never transfer DEMs."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .postgame_store import source_category, mid
from .replay_holdout import refuse_locked_match
from .ward_reference_export import build


def catalog_projection(row):
    _, competition = source_category(row)
    result = {k: row.get(k) for k in ('sha256', 'analysis_sha256', 'sources', 'download_status', 'analysis_status')}
    sample = row.get('sampling') or {}
    result['sampling'] = competition or ({'mmr': sample['mmr']} if type(sample.get('mmr')) is int else {})
    return result


REMOTE = '''import base64,hashlib,json
from pathlib import Path
p=PAYLOAD
root=Path(p['root']); identifier=p['match']
def projection(row):
 return {k:row.get(k) for k in ('sha256','analysis_sha256','sources','download_status','analysis_status')}
catalog=json.loads((root/'catalog.json').read_bytes())
row=catalog['matches'][identifier]
if projection(row)!=p['expected']:raise ValueError('snapshot_catalog_changed')
settings=json.loads((root/'config.json').read_bytes())
folder=Path(settings.get('analysis_directory',root/'analysis'))/identifier
def bounded(name,limit):
 with (folder/name).open('rb') as f:raw=f.read(limit+1)
 if len(raw)>limit:raise ValueError('snapshot_file_budget')
 return raw
mraw=bounded('manifest.json',256*1024);m=json.loads(mraw)
if (m.get('match_id')!=identifier or not m.get('complete') or m.get('source_sha256')!=row['sha256']
 or m.get('parse_error') is not None or m.get('stop_reason')!='replay_eof'
 or m.get('source_unchanged_stat') is not True):
 raise ValueError('snapshot_complete_identity_required')
files={'manifest_base64':mraw};total=len(mraw)
for name,key in [('wards.json','wards_base64'),('postgame_context.json','context_base64')]:
 if name=='postgame_context.json' and m.get('postgame_context') is None:continue
 raw=bounded(name,2*1024**2);spec=m['files'][name]
 if len(raw)!=spec['bytes'] or hashlib.sha256(raw).hexdigest()!=spec['sha256']:
  raise ValueError('snapshot_source_changed')
 files[key]=raw;total+=len(raw)
if total>2*1024**2:raise ValueError('snapshot_response_budget')
if bounded('manifest.json',256*1024)!=mraw:raise ValueError('snapshot_manifest_changed')
if projection(json.loads((root/'catalog.json').read_bytes())['matches'][identifier])!=p['expected']:
 raise ValueError('snapshot_catalog_changed')
print(json.dumps({key:base64.b64encode(raw).decode() for key,raw in files.items()}))
'''


def collect(bridge, output, *, source='tier1', limit=8, before_match=None):
    if source not in ('tier1', 'own', 'd2pt') or type(limit) is not int or not 1 <= limit <= 16:
        raise ValueError('snapshot_selection_invalid')
    if before_match is not None:
        before_match = mid(before_match)
    output = Path(output)
    if output.exists():
        raise ValueError('snapshot_new_output_required')
    catalog = bridge.catalog()
    candidates, excluded = [], []
    for row in catalog['matches'].values():
        if source not in row.get('sources', []):
            continue
        identifier = mid(row['match_id'])
        if before_match is not None and int(identifier) >= int(before_match):
            continue
        refuse_locked_match(identifier)  # before requesting any body from Pi
        if row.get('download_status') != 'verified' or row.get('analysis_status') != 'complete':
            excluded.append({k: row.get(k) for k in ('match_id','download_status','analysis_status')})
            continue
        projected = catalog_projection(row)
        candidates.append((identifier, projected))
    selected = sorted(candidates, key=lambda r: int(r[0]), reverse=True)[:limit]
    if not selected:
        raise ValueError('snapshot_no_complete_matches')
    output.mkdir(parents=True)
    entries, receipts, captures = [], [], []
    for identifier, projected in selected:
        request = {'root': bridge.config['remote_data'], 'match': identifier,
                   'expected': {k: projected[k] for k in ('sha256','analysis_sha256','sources','download_status','analysis_status')}}
        script = output/'fetch-private.py'
        script.write_text(REMOTE.replace('PAYLOAD', repr(request)), encoding='utf-8')
        try:
            result = bridge.execute('python3 -', source=script, timeout=30)
        finally:
            script.unlink(missing_ok=True)
        manifest_raw = base64.b64decode(result['manifest_base64'], validate=True)
        manifest = json.loads(manifest_raw)
        refuse_locked_match(str(manifest.get('match_id')))
        if manifest.get('match_id') != identifier or manifest.get('source_sha256') != projected['sha256']:
            raise ValueError('snapshot_source_identity_changed')
        wards_raw = base64.b64decode(result['wards_base64'], validate=True)
        packet = build(manifest, wards_raw)
        if packet['source_wards_sha256'] != projected['analysis_sha256']:
            raise ValueError('snapshot_catalog_wards_changed')
        if 'context_base64' in result:
            context = base64.b64decode(result['context_base64'], validate=True)
            spec = manifest['files']['postgame_context.json']
            if spec['sha256'] != hashlib.sha256(context).hexdigest() or spec['bytes'] != len(context):
                raise ValueError('snapshot_context_changed')
            capture = {'schema_version': 1, 'kind': 'isolated_postgame_capture_export', **result}
            captures.append((identifier, capture))
        entries.append({'match_id': identifier, 'catalog': projected,
                        'manifest_base64': result['manifest_base64'], 'wards_base64': result['wards_base64']})
        receipts.append({'match_id': identifier, 'replay_sha256': projected['sha256'],
                         'wards_sha256': packet['source_wards_sha256'], 'context_collected': 'context_base64' in result})
    final_catalog = bridge.catalog()
    for identifier, projected in selected:
        if catalog_projection(final_catalog['matches'][identifier]) != projected:
            raise ValueError('snapshot_catalog_changed')
    frozen = {'schema_version': 1, 'kind': 'frozen_verified_ward_exports',
              'raw_replays_transferred': False, 'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
              'matches': entries, 'excluded_catalog_rows': excluded}
    raw = json.dumps(frozen, ensure_ascii=False, indent=2).encode()
    if len(raw) > 32*1024**2:
        raise ValueError('snapshot_total_budget')
    (output/'snapshot.json').write_bytes(raw)
    for identifier, capture in captures:
        (output/f'capture-{identifier}.json').write_text(json.dumps(capture), encoding='utf-8')
    receipt = {'status': 'complete', 'source_filter': source, 'matches': receipts,
               'before_match': before_match,
               'next_before_match': min(selected, key=lambda r: int(r[0]))[0] if len(candidates) > len(selected) else None,
               'excluded_incomplete': excluded, 'complete_rows_outside_batch_limit': len(candidates)-len(selected),
               'snapshot_sha256': hashlib.sha256(raw).hexdigest(), 'raw_replays_transferred': False,
               'remote_mutations': False}
    (output/'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt
