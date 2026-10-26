"""Compare prepared crop snapshots with competing original resource families."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(catalog_folder, review_path, *, root=ROOT):
    import numpy as np
    from PIL import Image, ImageDraw
    from dota2_insights.history import checked_input, checked_output
    from dota2_insights.match_journals import _read, _json
    from dota2_insights import minimap_template_match as algorithm
    root = Path(root).resolve()
    catalog_folder = checked_input(root, catalog_folder)
    review_path = checked_input(root, review_path)
    bank = algorithm.load_bank(catalog_folder)
    raw = _read(review_path, 16 * 1024**2)
    review = _json(raw)
    crops = review.get('crops')
    if (review.get('kind') != 'minimap_enclosed_hole_review' or not isinstance(crops, list) or len(crops) > 96):
        raise ValueError('template_match_crop_review_invalid')
    output = checked_output(root, root / 'diagnostics/minimap-template-crops' /
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    rows, tiles = [], []
    for index, entry in enumerate(crops):
        filename = entry.get('crop')
        if not isinstance(filename, str) or not re.fullmatch(r'crops/crop-[0-9]{3}\.png', filename):
            raise ValueError('template_match_crop_path_invalid')
        path = review_path.parent / filename
        if path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction():
            raise ValueError('template_match_crop_link_refused')
        content = _read(path, 2 * 1024**2)
        if hashlib.sha256(content).hexdigest() != entry.get('crop_sha256'):
            raise ValueError('template_match_crop_sha_changed')
        with Image.open(BytesIO(content)) as image:
            if image.mode != 'RGB' or not 4 <= min(image.size) <= max(image.size) <= 128:
                raise ValueError('template_match_crop_geometry_invalid')
            image = image.copy()
        if hashlib.sha256(image.tobytes()).hexdigest() != entry.get('crop_rgb_pixel_sha256'):
            raise ValueError('template_match_crop_pixels_changed')
        result = algorithm.match_patch(np.asarray(image)[:, :, ::-1].copy(), bank)
        rows.append({'crop_index': index, 'crop': filename, 'crop_sha256': entry['crop_sha256'],
            'source_evidence': entry['source_evidence'], 'captured_at_utc': entry['captured_at_utc'],
            'result': result})
        tile = Image.new('RGB', (360, 240), '#20252b')
        draw = ImageDraw.Draw(tile)
        draw.text((6, 5), f"crop {index}: {result['state']}", fill='white')
        best = result['ranked_families'][:2]
        for line, candidate in enumerate(best):
            draw.text((6, 23 + line * 17), f"{candidate['family']} {candidate['score']:.3f} / {candidate['angle_degrees']}deg", fill='#ffc85a')
        draw.text((6, 59), result['reason'], fill='white')
        image = image.resize((image.width * 6, image.height * 6), Image.Resampling.NEAREST)
        image.thumbnail((344, 152), Image.Resampling.NEAREST)
        tile.paste(image, (6, 82))
        tiles.append(tile)
    for start in range(0, len(tiles), 12):
        part = tiles[start:start + 12]
        sheet = Image.new('RGB', (1440, 240 * ((len(part) + 3) // 4)), '#20252b')
        for i, tile in enumerate(part):
            sheet.paste(tile, ((i % 4) * 360, (i // 4) * 240))
        sheet.save(output / f'sheet-{start // 12:02d}.png')
    if _read(review_path, 16 * 1024**2) != raw:
        raise ValueError('template_match_input_review_changed')
    refreshed = algorithm.load_bank(catalog_folder)
    if refreshed['catalog_sha256'] != bank['catalog_sha256'] or refreshed['bindings'] != bank['bindings']:
        raise ValueError('template_match_reference_bank_changed')
    summary = {'crops_compared': len(rows), 'states': dict(Counter(r['result']['state'] for r in rows)),
        'shape_family_candidates': dict(Counter(r['result']['resource_family_candidate'] for r in rows
            if r['result']['resource_family_candidate'] is not None)),
        'template_variants': len(bank['templates']), 'missing_resources': bank['missing_resources']}
    report = {'schema_version': 1, 'kind': 'minimap_resource_family_crop_match', 'summary': summary,
        'catalog_sha256': bank['catalog_sha256'], 'input_review_sha256': hashlib.sha256(raw).hexdigest(),
        'algorithm_source_sha256': hashlib.sha256(Path(algorithm.__file__).read_bytes()).hexdigest(),
        'parameters': algorithm.PARAMETERS, 'reference_bindings': bank['bindings'], 'rows': rows,
        'source_provenance': 'prepared_crop_snapshot_original_source_logs_not_reverified_here',
        'accuracy_verified': False, 'semantic_identity_verified': False, 'online_changes_applied': False}
    lines = ['# 小地图资源族候选对照', '',
        '只匹配已准备裁片副本，未重新核验原比赛日志；分数是形状Dice，不是概率或身份确认。',
        '英雄、眼、兵线、建筑、信使模板共同竞争；弱匹配/差距不足/缺模板保持未知。', '', str(summary), '']
    for i in range((len(tiles) + 11) // 12):
        lines += [f'![候选对照 {i}](sheet-{i:02d}.png)', '']
    (output / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'output': str(output), **summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.catalog, args.review), ensure_ascii=False))


if __name__ == '__main__':
    main()
