"""Compare full-frame color proposals in verified prepared PNG snapshots."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def visual_indices(rows, limit=64):
    indices = [i for i, row in enumerate(rows) if row['result']['state'] == 'shape_candidate_only'][:limit // 2]
    strata = set()
    for index, row in enumerate(rows):
        ranked = row['result'].get('ranked_families', [])
        group = (row['result']['state'], ranked[0]['family'] if ranked else None,
                 bool(row['candidate']['text_band_overlaps']), bool(row['candidate']['solid_square_overlaps']))
        if group not in strata and len(indices) < limit:
            strata.add(group)
            if index not in indices:
                indices.append(index)
    for i in range(min(limit, len(rows))):
        index = round(i * (len(rows) - 1) / max(1, min(limit, len(rows)) - 1))
        if len(indices) < limit and index not in indices:
            indices.append(index)
    return indices


def run(catalog_folder, batch_path, *, root=ROOT, max_frames=8):
    import numpy as np
    from PIL import Image, ImageDraw
    from dota2_insights.history import checked_input, checked_output
    from dota2_insights.match_journals import _read, _json
    from dota2_insights.minimap_template_match import load_bank
    from dota2_insights.minimap_frame_match import evaluate_frame
    from dota2_insights import minimap_frame_match, minimap_template_match, minimap_component_probe
    if type(max_frames) is not int or not 1 <= max_frames <= 24:
        raise ValueError('frame_match_frame_limit_invalid')
    root = Path(root).resolve()
    catalog_folder, batch_path = checked_input(root, catalog_folder), checked_input(root, batch_path)
    bank = load_bank(catalog_folder)
    raw = _read(batch_path, 16 * 1024**2)
    batch = _json(raw)
    groups = batch.get('results')
    if (type(batch.get('schema_version')) is not int or batch['schema_version'] != 1
            or batch.get('kind') != 'minimap_image_review_batch' or not isinstance(groups, list) or len(groups) > 10):
        raise ValueError('frame_match_prepared_batch_invalid')
    inputs = []
    for group in groups:
        entries = group.get('result', {}).get('entries')
        if not isinstance(entries, list) or len(entries) > 24:
            raise ValueError('frame_match_prepared_entries_invalid')
        inputs += [(group['directory'], entry) for entry in entries if entry['status'] == 'prepared_for_visual_review']
    count = min(max_frames, len(inputs))
    selected = [inputs[round(i * (len(inputs) - 1) / max(1, count - 1))] for i in range(count)]
    output = checked_output(root, root / 'diagnostics/minimap-frame-template-match' /
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'crops').mkdir()
    frames, rows, images = [], [], []
    for frame_index, (directory, entry) in enumerate(selected):
        path = batch_path.parent / directory / entry['image']
        if not path.resolve().is_relative_to(batch_path.parent.resolve()):
            raise ValueError('frame_match_prepared_image_outside_batch')
        for parent in (path, *path.parents):
            if parent.is_relative_to(batch_path.parent) and (parent.is_symlink() or parent.is_junction()):
                raise ValueError('frame_match_prepared_link_refused')
        content = _read(path, 8 * 1024**2)
        if hashlib.sha256(content).hexdigest() != entry.get('declared_image_sha256'):
            raise ValueError('frame_match_prepared_image_sha_changed')
        with Image.open(BytesIO(content)) as encoded:
            if encoded.mode != 'RGB' or list(encoded.size) != entry.get('dimensions'):
                raise ValueError('frame_match_prepared_geometry_changed')
            image = encoded.copy()
        if entry.get('pixel_sha_verified') is not True:
            raise ValueError('frame_match_prior_pixel_check_required')
        bgr = np.asarray(image)[:, :, ::-1].copy()
        observation = evaluate_frame(bgr, bank)
        frames.append({'frame_index': frame_index, 'prepared_image': str(path),
            'original_image_sha256': entry['declared_image_sha256'], 'sample_ordinal': entry['sample_ordinal'],
            'source_evidence': entry['source_evidence'], 'captured_at_utc': entry['captured_at_utc'],
            'candidate_count_before_limit': observation['candidate_count_before_limit'],
            'candidate_limit_reached': observation['candidate_limit_reached'],
            'retained_candidates': len(observation['candidates'])})
        images.append(image)
        for row in observation['candidates']:
            rows.append({**row, 'frame_index': frame_index, 'crop_prepared': False})
    # Preserve every result, but keep the visual sheet bounded and varied.
    indices = visual_indices(rows)
    tiles = []
    for index in indices:
        row = rows[index]
        x, y, w, h = row['candidate']['box']
        crop = images[row['frame_index']].crop((x, y, x + w, y + h))
        filename = f'crops/candidate-{index:04d}.png'
        crop.save(output / filename)
        row.update(crop_prepared=True, crop_image=filename,
                   crop_png_sha256=hashlib.sha256((output / filename).read_bytes()).hexdigest())
        tile = Image.new('RGB', (360, 240), '#20252b')
        draw = ImageDraw.Draw(tile)
        draw.text((6, 5), f"{index} frame {row['frame_index']} {row['result']['state']}", fill='white')
        for line, candidate in enumerate(row['result'].get('ranked_families', [])[:2]):
            draw.text((6, 23 + line * 17), f"{candidate['family']} {candidate['score']:.3f}", fill='#ffc85a')
        draw.text((6, 59), row['result']['reason'], fill='white')
        crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.NEAREST)
        crop.thumbnail((344, 152), Image.Resampling.NEAREST)
        tile.paste(crop, (6, 82))
        tiles.append(tile)
    for start in range(0, len(tiles), 12):
        part = tiles[start:start + 12]
        sheet = Image.new('RGB', (1440, 240 * ((len(part) + 3) // 4)), '#20252b')
        for i, tile in enumerate(part):
            sheet.paste(tile, ((i % 4) * 360, (i // 4) * 240))
        sheet.save(output / f'sheet-{start // 12:02d}.png')
    refreshed = load_bank(catalog_folder)
    if (_read(batch_path, 16 * 1024**2) != raw or refreshed['bindings'] != bank['bindings']
            or refreshed['catalog_sha256'] != bank['catalog_sha256']):
        raise ValueError('frame_match_input_or_reference_changed')
    for frame in frames:
        if hashlib.sha256(_read(Path(frame['prepared_image']), 8 * 1024**2)).hexdigest() != frame['original_image_sha256']:
            raise ValueError('frame_match_prepared_image_changed_before_publication')
    summary = {'frames_compared': len(frames), 'candidates_compared': len(rows),
        'unique_png_sha256': len({frame['original_image_sha256'] for frame in frames}),
        'frames_reaching_candidate_cap': sum(frame['candidate_limit_reached'] for frame in frames),
        'states': dict(Counter(row['result']['state'] for row in rows)),
        'shape_family_candidates': dict(Counter(row['result']['resource_family_candidate'] for row in rows
            if row['result']['resource_family_candidate'] is not None)), 'visual_crops_prepared': len(tiles),
        'shape_candidates_without_visual_crop': sum(row['result']['state'] == 'shape_candidate_only'
            and not row['crop_prepared'] for row in rows)}
    report = {'schema_version': 1, 'kind': 'minimap_full_candidate_resource_match', 'summary': summary,
        'batch_sha256': hashlib.sha256(raw).hexdigest(), 'catalog_sha256': bank['catalog_sha256'],
        'source_hashes': {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                          for module in (minimap_frame_match, minimap_template_match, minimap_component_probe)},
        'parameters': bank['parameters'], 'frames': frames, 'rows': rows,
        'source_provenance': 'prepared_png_snapshot_original_source_logs_not_reverified_here',
        'accuracy_verified': False, 'hero_count': None, 'online_changes_applied': False}
    lines = ['# 完整颜色候选的资源族对照', '', '包含实心/方向型候选，不限于孔洞。形状候选仍不是确认身份，未知结果保留。',
             '原始比赛日志未在本入口重验；输入是带SHA的已准备图像副本。', '', str(summary), '']
    for i in range((len(tiles) + 11) // 12):
        lines += [f'![候选裁片 {i}](sheet-{i:02d}.png)', '']
    (output / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'output': str(output), **summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--max-frames', type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(run(args.catalog, args.batch, max_frames=args.max_frames), ensure_ascii=False))


if __name__ == '__main__':
    main()
