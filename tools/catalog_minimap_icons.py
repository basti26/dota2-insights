"""Export a read-only local original minimap atlas, without a game or VPK parser."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dota2_insights.history import checked_input, checked_output
    from dota2_insights.minimap_icon_catalog import export
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--icons', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = checked_input(ROOT, args.icons)
    output = checked_output(ROOT, args.output)
    if output.is_relative_to(source):
        raise ValueError('minimap_icon_output_overlaps_source')
    result = export(source, output)
    print(json.dumps({'output': str(output), 'icons': len(result['entries']),
                      'unsupported': len(result['unsupported_definitions'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
