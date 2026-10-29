"""Evaluate exported self-position labels on a separate held-out match, offline."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(train_samples, test_samples, *, train_labels=None, test_labels=None, root=ROOT):
    from dota2_insights.minimap_position_evaluation import load_labels, evaluate
    from dota2_insights.history import checked_output
    train = load_labels(train_samples, train_labels)
    test = load_labels(test_samples, test_labels)
    result = evaluate(train, test)
    output = checked_output(root, Path(root)/'diagnostics/minimap-position-evaluation'/
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    report = {'training': train, 'heldout': test, 'evaluation': result}
    lines = ['# 本人小地图坐标评估', '', f"状态：{result['status']}。",
        f"探索局：{train['match_id']}，可用标注 {len(train['accepted'])}，未标注 {train['unlabelled']}。",
        f"留出局：{test['match_id']}，可用标注 {len(test['accepted'])}，未标注 {test['unlabelled']}。", '',
        '只核验导出素材与标注绑定，未重新核验原采集/GSI包。人工身份、地图版本、布局兼容及帧与GSI同时性仍未认证。', '']
    if result['heldout_error_px'] is None:
        lines.append('尚不能计算误差：至少需要探索局6点、不同比赛留出局3点的有效本人位置标注。没有生成拟合模型。')
    else:
        lines += [f"训练像素误差：{result['training_error_px']}。", f"留出像素误差：{result['heldout_error_px']}。",
            f"训练空间覆盖有限：{result['training_coverage_limited']}。",
            '结果仅供探索；训练残差不替代留出误差，不自动激活任何坐标模型或实时预警。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),encoding='utf-8')
    return {'output': str(output), 'status': result['status'], 'calibration_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train-samples', type=Path, required=True)
    parser.add_argument('--heldout-samples', type=Path, required=True)
    parser.add_argument('--train-labels', type=Path)
    parser.add_argument('--heldout-labels', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.train_samples, args.heldout_samples,
        train_labels=args.train_labels, test_labels=args.heldout_labels), ensure_ascii=False))


if __name__ == '__main__':
    main()
