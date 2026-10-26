"""Prepare verified frames and annotate exploratory color/text/square overlaps."""
import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(root=ROOT, *, roots=None, latest=3, max_runs=2, max_images=12):
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    from dota2_insights.history import checked_output
    from dota2_insights.match_journals import _read
    from dota2_insights import minimap_component_probe as algorithm
    from tools.prepare_minimap_review import prepare
    root = Path(root).resolve()
    prepared = prepare(root, roots=roots, latest=latest, max_runs=max_runs, max_images=max_images)
    source = Path(prepared['output'])
    batch_raw = _read(source / 'batch.json', 16 * 1024**2)
    batch = json.loads(batch_raw)
    output = checked_output(root, root / 'diagnostics/minimap-component-probe' /
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for group in batch['results']:
        for entry in group['result']['entries']:
            if entry['status'] != 'prepared_for_visual_review':
                continue
            path = (source / group['directory'] / entry['image']).resolve()
            if not path.is_relative_to(source):
                raise ValueError('component_probe_image_outside_review')
            raw = _read(path, 8 * 1024**2)
            if hashlib.sha256(raw).hexdigest() != entry['declared_image_sha256']:
                raise ValueError('component_probe_prepared_image_changed')
            with Image.open(BytesIO(raw)) as encoded:
                if list(encoded.size) != entry['dimensions'] or encoded.mode != 'RGB':
                    raise ValueError('component_probe_prepared_geometry_changed')
                image = encoded.copy()
            result = algorithm.probe(np.asarray(image)[:, :, ::-1].copy())
            draw = ImageDraw.Draw(image)
            for square in result['square_shapes']:
                x, y, w, h = square['rect']
                draw.rectangle((x, y, x + w - 1, y + h - 1), outline='#d97aff', width=1)
            for band in result['text_bands']:
                x, y, w, h = band['box']
                draw.rectangle((x, y, x + w - 1, y + h - 1), outline='#ffaa20', width=1)
            for i, candidate in enumerate(result['candidates']):
                x, y, w, h = candidate['box']
                draw.rectangle((x, y, x + w - 1, y + h - 1), outline='#60e8ff', width=1)
                draw.text((x, y), str(i), fill='#ffffff')
            filename = f'annotated-{len(results):03d}.png'
            image.save(output / filename)
            results.append({'prepared_image': str(path), 'original_image_sha256': entry['declared_image_sha256'],
                'sample_ordinal': entry['sample_ordinal'], 'captured_at_utc': entry['captured_at_utc'],
                'source_evidence': entry['source_evidence'], 'annotated_image': filename, 'probe': result})
    if hashlib.sha256(_read(source / 'batch.json', 16 * 1024**2)).digest() != hashlib.sha256(batch_raw).digest():
        raise ValueError('component_probe_input_batch_changed')
    summary = {'frames': len(results),
        'raw_color_candidates_before_cap': sum(r['probe']['candidate_count_before_limit'] for r in results),
        'retained_color_candidates': sum(len(r['probe']['candidates']) for r in results),
        'frames_reaching_candidate_cap': sum(r['probe']['candidate_limit_reached'] for r in results),
        'text_bands': sum(len(r['probe']['text_bands']) for r in results),
        'candidates_with_text_overlap': sum(r['probe']['candidates_with_text_overlap'] for r in results),
        'candidates_with_square_overlap': sum(r['probe']['candidates_with_square_overlap'] for r in results)}
    report = {'schema_version': 1, 'kind': 'minimap_component_probe', 'prepared_review': prepared,
        'batch_sha256': hashlib.sha256(batch_raw).hexdigest(),
        'algorithm_source_sha256': hashlib.sha256(Path(algorithm.__file__).read_bytes()).hexdigest(),
        'algorithm_version': algorithm.VERSION, 'opencv_version': cv2.__version__, 'numpy_version': np.__version__,
        'summary': summary, 'results': results, 'hero_count': None, 'visual_verdicts_entered': False,
        'online_changes_applied': False, 'accuracy_verified': False,
        'meaning': 'exploratory_ambiguity_flags_no_suppression_no_team_identity'}
    lines = ['# 小地图颜色候选干扰复核', '',
        '浅蓝框：原颜色候选；橙框：疑似横向文字带；紫框：实色方块形状。颜色均不是敌我标签。',
        '重合只表示歧义，英雄可能与文字或建筑重合，没有删除候选、统计英雄或启用预警。', '', str(summary), '']
    for i, entry in enumerate(results):
        lines += [f"## 图 {i + 1} / 样本 {entry['sample_ordinal']}", '',
            f"原采集UTC：{entry['captured_at_utc']}；原行：{entry['source_evidence']['line']}",
            f"候选：{len(entry['probe']['candidates'])}；文字带重合：{entry['probe']['candidates_with_text_overlap']}；方块重合：{entry['probe']['candidates_with_square_overlap']}",
            f"![候选图]({entry['annotated_image']})", '']
    (output / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return {'output': str(output), **summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', type=int, default=3)
    parser.add_argument('--max-runs', type=int, default=2)
    parser.add_argument('--max-images', type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(run(latest=args.latest, max_runs=args.max_runs, max_images=args.max_images), ensure_ascii=False))


if __name__ == '__main__':
    main()
