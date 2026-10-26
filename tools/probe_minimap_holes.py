"""Verified native-size crops of enclosed-color holes, including glyph ambiguity."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def select_features(features, max_crops):
    if type(max_crops) is not int or not 1 <= max_crops <= 96:
        raise ValueError('hole_probe_crop_limit_invalid')
    groups = defaultdict(list)
    for feature in features:
        shape = feature['feature']
        group = (shape['display_color'], bool(shape['text_band_overlaps']),
                 'smaller' if max(shape['normalized_width'], shape['normalized_height']) <= 12 else 'larger')
        groups[group].append(feature)
    selected, queues = [], []
    for key in sorted(groups):
        rows = groups[key]
        count = min(len(rows), max_crops)
        indices = [round(i * (len(rows) - 1) / max(1, count - 1)) for i in range(count)]
        queues.append(iter([rows[i] for i in indices]))
    while queues and len(selected) < max_crops:
        active = []
        for queue in queues:
            try:
                selected.append(next(queue))
                active.append(queue)
            except StopIteration:
                continue
            if len(selected) >= max_crops:
                break
        queues = active
    return selected


def run(root=ROOT, *, roots=None, latest=3, max_runs=2, max_crops=48):
    import numpy as np
    from PIL import Image, ImageDraw
    from dota2_insights import minimap_hole_probe as algorithm
    from dota2_insights.history import checked_output
    from dota2_insights.match_journals import _read
    from tools.prepare_minimap_review import prepare
    if type(max_crops) is not int or not 1 <= max_crops <= 96:
        raise ValueError('hole_probe_crop_limit_invalid')
    if type(max_runs) is not int or not 1 <= max_runs <= 3:
        raise ValueError('hole_probe_run_limit_invalid')
    root = Path(root).resolve()
    prepared = prepare(root, roots=roots, latest=latest, max_runs=max_runs,
                       max_images=24, selection='continuous_windows')
    source = Path(prepared['output'])
    batch_raw = _read(source / 'batch.json', 16 * 1024**2)
    batch = json.loads(batch_raw)
    frames, features = [], []
    for group in batch['results']:
        for entry in group['result']['entries']:
            if entry['status'] != 'prepared_for_visual_review':
                continue
            path = (source / group['directory'] / entry['image']).resolve()
            if not path.is_relative_to(source):
                raise ValueError('hole_probe_image_outside_review')
            raw = _read(path, 8 * 1024**2)
            if hashlib.sha256(raw).hexdigest() != entry['declared_image_sha256']:
                raise ValueError('hole_probe_prepared_image_changed')
            with Image.open(BytesIO(raw)) as encoded:
                if encoded.mode != 'RGB' or list(encoded.size) != entry['dimensions']:
                    raise ValueError('hole_probe_prepared_geometry_changed')
                result = algorithm.describe(np.asarray(encoded)[:, :, ::-1].copy())
            frame = {'prepared_image': str(path), 'original_image_sha256': entry['declared_image_sha256'],
                'sample_ordinal': entry['sample_ordinal'], 'captured_at_utc': entry['captured_at_utc'],
                'source_evidence': entry['source_evidence'], 'probe': result}
            index = len(frames)
            frames.append(frame)
            for feature_index, feature in enumerate(result['features']):
                features.append({'frame_index': index, 'feature_index': feature_index, 'feature': feature})
    selected = select_features(features, max_crops)
    output = checked_output(root, root / 'diagnostics/minimap-hole-probe' /
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'crops').mkdir()
    crops, tiles = [], []
    for i, selected_feature in enumerate(selected):
        frame = frames[selected_feature['frame_index']]
        feature = selected_feature['feature']
        raw = _read(Path(frame['prepared_image']), 8 * 1024**2)
        if hashlib.sha256(raw).hexdigest() != frame['original_image_sha256']:
            raise ValueError('hole_probe_prepared_image_changed')
        with Image.open(BytesIO(raw)) as encoded:
            x, y, w, h = feature['box']
            pad = max(2, round(encoded.width / 372 * 4))
            box = max(0, x - pad), max(0, y - pad), min(encoded.width, x + w + pad), min(encoded.height, y + h + pad)
            crop = encoded.crop(box).convert('RGB')
        filename = f'crops/crop-{i:03d}.png'
        crop.save(output / filename)
        crops.append({**selected_feature, 'source_evidence': frame['source_evidence'],
            'captured_at_utc': frame['captured_at_utc'], 'original_image_sha256': frame['original_image_sha256'],
            'crop': filename, 'crop_rect': [box[0], box[1], box[2] - box[0], box[3] - box[1]],
            'crop_sha256': hashlib.sha256((output / filename).read_bytes()).hexdigest(),
            'crop_rgb_pixel_sha256': hashlib.sha256(crop.tobytes()).hexdigest(), 'visual_label': 'unlabeled'})
        tile = Image.new('RGB', (300, 240), '#20252b')
        draw = ImageDraw.Draw(tile)
        draw.text((6, 6), f"{i} frame {selected_feature['frame_index']} {feature['display_color']}", fill='white')
        draw.text((6, 24), f"box {feature['box']} hole {feature['hole_pixel_fraction']}", fill='#ffc85a')
        draw.text((6, 42), f"text-overlap {bool(feature['text_band_overlaps'])} holes {feature['hole_count']}", fill='white')
        crop = crop.resize((crop.width * 8, crop.height * 8), Image.Resampling.NEAREST)
        crop.thumbnail((288, 176), Image.Resampling.NEAREST)
        tile.paste(crop, (6, 60))
        tiles.append(tile)
    for start in range(0, len(tiles), 16):
        part = tiles[start:start + 16]
        sheet = Image.new('RGB', (1200, 240 * ((len(part) + 3) // 4)), '#20252b')
        for i, tile in enumerate(part):
            sheet.paste(tile, ((i % 4) * 300, (i // 4) * 240))
        sheet.save(output / f'sheet-{start // 16:02d}.png')
    if _read(source / 'batch.json', 16 * 1024**2) != batch_raw:
        raise ValueError('hole_probe_input_batch_changed')
    summary = {'frames': len(frames), 'enclosed_hole_features': len(features),
        'features_with_text_overlap': sum(bool(f['feature']['text_band_overlaps']) for f in features),
        'features_with_square_overlap': sum(bool(f['feature']['square_shape_overlaps']) for f in features),
        'frames_reaching_feature_cap': sum(frame['probe']['limit_reached'] for frame in frames),
        'crops_prepared': len(crops)}
    report = {'schema_version': 1, 'kind': 'minimap_enclosed_hole_review', 'prepared_review': prepared,
        'algorithm_source_sha256': hashlib.sha256(Path(algorithm.__file__).read_bytes()).hexdigest(),
        'batch_sha256': hashlib.sha256(batch_raw).hexdigest(), 'summary': summary,
        'frames': frames, 'crops': crops, 'hero_count': None, 'accuracy_verified': False,
        'visual_verdicts_entered': False, 'online_changes_applied': False}
    lines = ['# 小地图封闭颜色形状复核', '', '有空洞不等于英雄；文字笔画也会形成封闭形状。所有裁片未标注身份。', '', str(summary), '']
    for i in range((len(tiles) + 15) // 16):
        lines += [f'![原像素放大裁片 {i}](sheet-{i:02d}.png)', '']
    (output / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'output': str(output), **summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', type=int, default=3)
    parser.add_argument('--max-runs', type=int, default=2)
    parser.add_argument('--max-crops', type=int, default=48)
    args = parser.parse_args()
    print(json.dumps(run(latest=args.latest, max_runs=args.max_runs, max_crops=args.max_crops), ensure_ascii=False))


if __name__ == '__main__':
    main()
