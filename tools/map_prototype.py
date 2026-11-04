"""Prepare or view the bounded, local-only virtual-map comparison prototype."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dota2_insights.history import checked_input, checked_output
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    build = commands.add_parser('prepare')
    for name in ('collision', 'landmarks', 'review', 'survey', 'output'):
        build.add_argument('--'+name, type=Path, required=True)
    build.add_argument('--ward-snapshot', type=Path)
    build.add_argument('--ward-receipt', type=Path)
    build.add_argument('--ward-match')
    build.add_argument('--root-assets',type=Path)
    build.add_argument('--map-revision',type=Path)
    view = commands.add_parser('view')
    view.add_argument('package', type=Path)
    view.add_argument('--export', type=Path, help='render a contact sheet without starting the GUI')
    args = cli.parse_args()
    if args.command == 'prepare':
        from dota2_insights.map_prototype import prepare
        scene = prepare(args.collision, args.landmarks, args.review, args.survey, args.output,
            ward_snapshot=args.ward_snapshot, ward_receipt=args.ward_receipt, ward_match=args.ward_match,
            root_assets=args.root_assets,map_revision=args.map_revision)
        print({'output': str(args.output), 'markers': len(scene['markers']), 'frames': len(scene['frames']), 'p2_pass': False})
    else:
        from dota2_insights.map_prototype_view import export_overview, show
        package = checked_input(ROOT, args.package)
        if args.export:
            output = checked_output(ROOT, args.export)
            if output.exists():
                raise ValueError('new_prototype_export_required')
            export_overview(package, output)
            print({'export': str(output)})
        else:
            show(package)


if __name__ == '__main__':
    main()
