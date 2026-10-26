"""Resolve priority rows to SHA-bound, geometry-checked original HUD slot crops."""
from io import BytesIO
import hashlib
import re
from pathlib import Path

from .closed_journals import ClosedJournals


def _prepare_entry(roots, ref, row, evidence, index, priority, label, output):
    from PIL import Image, ImageDraw
    from .map_holdout import sha
    hero = (row.get('attempt') or {}).get('hero') or {}
    if row.get('match_id') != priority['match_id'] or hero.get('hero_id') != priority['hero_id']:
        raise ValueError('item_review_priority_identity_changed')
    found = [s for s in hero.get('slots', []) if s.get('slot_id') == priority['slot_id']]
    if len(found) != 1:
        raise ValueError('item_review_unique_slot_required')
    slot = found[0]
    candidate = slot.get('item_id') if slot.get('status') == 'known' else slot.get('candidate')
    if not isinstance(candidate, str) or candidate.removeprefix('item_') != priority['candidate_item']:
        raise ValueError('item_review_priority_candidate_changed')
    entry = {'priority_index': index, 'match_id': priority['match_id'], 'hero_id': priority['hero_id'],
        'slot_id': priority['slot_id'], 'candidate_item': priority['candidate_item'],
        'evidence_role': label, 'source_evidence': evidence, 'captured_at_utc': row.get('captured_at_utc'),
        'clock_time_s': (row.get('clock_context') or {}).get('clock_time_s'),
        'recorded_slot_status': slot.get('status'), 'recorded_item_id': slot.get('item_id'),
        'recorded_candidate': slot.get('candidate')}
    tile = None
    try:
        crop, binding = slot_crop(roots[ref['root_key']], ref, row, priority['slot_id'])
        filename = f'crops/{index:03d}-{label}.png'
        crop.save(output / filename)
        entry.update(status='prepared_for_visual_review', image=filename,
                     crop_sha256=sha(output / filename), **binding)
        tile = Image.new('RGB', (360, 220), '#20252b')
        draw = ImageDraw.Draw(tile)
        draw.text((8, 6), f"{index} {priority['match_id']} L{evidence['line']}", fill='white')
        draw.text((8, 24), priority['hero_id'].removeprefix('npc_dota_hero_') + ' ' + priority['slot_id'], fill='white')
        draw.text((8, 42), priority['candidate_item'] + '  ' + label.removesuffix('_evidence'), fill='#ffc85a')
        scaled = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.NEAREST)
        scaled.thumbnail((336, 144), Image.Resampling.NEAREST)
        tile.paste(scaled, (8, 68))
    except (ValueError, OSError) as exc:
        entry.update(status='unavailable', reason=str(exc) if isinstance(exc, ValueError) else 'image_not_readable')
    return entry, tile


def _finish(output, results, tiles, **metadata):
    from PIL import Image
    from .map_holdout import save
    if tiles:
        sheet = Image.new('RGB', (1080, 220 * ((len(tiles) + 2) // 3)), '#20252b')
        for index, tile in enumerate(tiles):
            sheet.paste(tile, ((index % 3) * 360, (index // 3) * 220))
        sheet.save(output / 'contact.png')
    result = {'schema_version': 1, **metadata, 'entries': results,
              'source_verified_before_and_after': True, 'visual_verdicts_entered': False}
    save(output / 'evidence.json', result)
    return result


def slot_crop(root, source, row, slot_id):
    from PIL import Image
    root = Path(root).resolve()
    name, expected = row.get('hud_image'), row.get('hud_image_sha256')
    if (not isinstance(name,str) or not re.fullmatch(r'hud-[0-9]{6}\.png',name)
            or not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected)):
        raise ValueError('item_review_missing_bound_hud_image')
    path = (root / source['path']).parent / name
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError('item_review_image_outside_history')
    from .minimap.offline_io import check_input_paths
    check_input_paths(path)
    before = path.stat()
    if before.st_size > 8*1024**2:
        raise ValueError('item_review_image_budget')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('item_review_image_sha_mismatch')
    after = path.stat()
    if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
        raise ValueError('item_review_image_changed')
    geometry = row.get('frame_geometry') or {}
    fields = ('width','height','origin_left','origin_top','client_width','client_height')
    if any(type(geometry.get(k)) is not int for k in fields):
        raise ValueError('item_review_explicit_geometry_required')
    w,h,ox,oy,cw,ch = [geometry[k] for k in fields]
    if not (1 <= w <= cw <= 4096 and 1 <= h <= ch <= 2160
            and 0 <= ox <= cw-w and 0 <= oy <= ch-h):
        raise ValueError('item_review_frame_geometry_invalid')
    slots = (row.get('attempt') or {}).get('hero', {}).get('slots', [])
    found = [s for s in slots if s.get('slot_id') == slot_id]
    if len(found) != 1:
        raise ValueError('item_review_unique_slot_required')
    rect = found[0].get('rect')
    if not isinstance(rect,list) or len(rect)!=4 or any(type(v) is not int for v in rect):
        raise ValueError('item_review_slot_geometry_required')
    x,y,sw,sh = rect
    x,y = x-ox,y-oy
    if not (1 <= sw <= 256 and 1 <= sh <= 256 and 0 <= x <= w-sw and 0 <= y <= h-sh):
        raise ValueError('item_review_slot_outside_saved_hud')
    with Image.open(BytesIO(raw)) as image:
        if image.format != 'PNG' or image.size != (w,h):
            raise ValueError('item_review_image_geometry_mismatch')
        crop = image.convert('RGB').crop((x,y,x+sw,y+sh))
    return crop, {'source_image': {'root_key':source['root_key'],
        'path':path.relative_to(root).as_posix(),'sha256':expected},
        'client_rect':rect,'crop_rect':[x,y,sw,sh], 'origin_client_xy':[ox,oy],
        'image_semantics_verified':False}


def prepare(package, roots, priorities, output):
    output = Path(output)
    if not isinstance(priorities,list) or len(priorities)>60:
        raise ValueError('item_review_priority_budget')
    wanted = {}
    for index,p in enumerate(priorities):
        for label in ('first_evidence','last_evidence'):
            evidence = p[label]
            ref = evidence['source']
            if (ref not in package['sources'] or not ref['path'].endswith('/observations.jsonl')
                    or type(evidence['line']) is not int or not 1 <= evidence['line'] <= 100000):
                raise ValueError('item_review_priority_source_invalid')
            key = ref['root_key'],ref['path'],evidence['line']
            if any(v[0] == index for v in wanted.get(key,[])): continue
            wanted.setdefault(key,[]).append((index,p,label,evidence))
    results, tiles = [], []
    output.mkdir(parents=True,exist_ok=False)
    (output/'crops').mkdir()
    with ClosedJournals(package,roots) as reader:
        for component,ref in reader.journals('economy_context'):
            if not any(k[:2] == (ref['root_key'],ref['path']) for k in wanted): continue
            for number,row,evidence in reader.rows(ref):
                for index,p,label,declared in wanted.pop((ref['root_key'],ref['path'],number),[]):
                    if evidence['line_sha256'] != declared['line_sha256']:
                        raise ValueError('item_review_priority_line_changed')
                    entry, tile = _prepare_entry(roots, ref, row, evidence, index, p, label, output)
                    if tile is not None:
                        tiles.append(tile)
                    results.append(entry)
    if wanted:
        raise ValueError('item_review_priority_rows_missing')
    return _finish(output, results, tiles, kind='item_review_evidence', launcher_run_id=package['launcher_run_id'])


def prepare_observation(entry, roots, priorities, output):
    from .observation_handoff import open_rows
    from .observation_index import resolve_reference, journal_reference
    if not isinstance(priorities, list) or len(priorities) > 60:
        raise ValueError('item_review_priority_budget')
    output = Path(output).resolve()
    if any(output.is_relative_to(Path(root).resolve()) for root in roots.values()):
        raise ValueError('item_review_output_overlaps_history')
    root, session = resolve_reference(entry, roots)
    results, tiles, wanted = [], [], {}
    with open_rows(root, session, expected_package_id=entry['package_id'], with_evidence=True) as (manifest, rows):
        ref = journal_reference(entry, manifest)
        for index, priority in enumerate(priorities):
            if (priority.get('match_id') != manifest['match_id']
                    or not isinstance(priority.get('hero_id'), str)
                    or not re.fullmatch(r'npc_dota_hero_[a-z0-9_]{1,60}', priority['hero_id'])):
                raise ValueError('item_review_priority_identity_changed')
            for label in ('first_evidence', 'last_evidence'):
                declared = priority[label]
                number = declared.get('line')
                if (ref is None or declared.get('source') != ref or type(number) is not int
                        or not 1 <= number <= manifest['payload']['rows']
                        or not re.fullmatch(r'[a-f0-9]{64}', str(declared.get('line_sha256', '')))):
                    raise ValueError('item_review_priority_source_invalid')
                if any(value[0] == index for value in wanted.get(number, [])):
                    continue
                wanted.setdefault(number, []).append((index, priority, label, declared))
        output.mkdir(parents=True, exist_ok=False)
        (output / 'crops').mkdir()
        for number, row, evidence in rows:
            for index, priority, label, declared in wanted.pop(number, []):
                if evidence['line_sha256'] != declared['line_sha256']:
                    raise ValueError('item_review_priority_line_changed')
                bound = {**evidence, 'source': ref}
                result, tile = _prepare_entry(roots, ref, row, bound, index, priority, label, output)
                results.append(result)
                if tile is not None:
                    tiles.append(tile)
    if wanted:
        raise ValueError('item_review_priority_rows_missing')
    return _finish(output, results, tiles, kind='native_item_review_evidence',
                   equipment_package_id=entry['package_id'], record_session_id=session,
                   match_id=manifest['match_id'])
