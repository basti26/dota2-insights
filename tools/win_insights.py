"""Independent analysis app. No capture, game automation or background downloads."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def self_check():
    from dota2_insights.replay_holdout import refuse_locked_match
    from dota2_insights.replay_archive import GEM_VERSION, GEM_COMMIT
    from dota2_insights.replay_workbench import dependency_status
    from dota2_insights.history import history_roots
    locks = json.loads((ROOT / 'data/validation-locks.json').read_text(encoding='utf-8'))
    refused = 0
    for match in locks['heldout_matches']:
        try:
            refuse_locked_match(match)
        except ValueError:
            refused += 1
        else:
            raise ValueError('sealed_match_not_refused')
    from dota2_insights import replay_fact_review
    roots = history_roots(ROOT / 'config/history.local.json')
    return {'passed': True, 'sealed_matches_refused': refused,
            'parser_dependency_status': dependency_status(ROOT),
            'gem_version': GEM_VERSION, 'gem_commit': GEM_COMMIT,
            'history_roots_configured': len(roots), 'historical_payloads_read': False,
            'capture_started': False, 'network_started': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--offline-self-check', type=Path)
    parser.add_argument('--check-ui', action='store_true')
    args = parser.parse_args()
    if args.offline_self_check:
        from dota2_insights.portable_app import write_check
        return write_check(args.offline_self_check, ROOT, check_ui=args.check_ui)
    if args.check_ui:
        parser.error('--check-ui requires --offline-self-check')
    if args.self_check:
        print(json.dumps(self_check(), ensure_ascii=False))
        return 0
    import tkinter as tk
    from tkinter import messagebox
    from tools.win_replay_analysis import ReplayAnalysisWindow
    from dota2_insights.history import history_location
    root = tk.Tk()
    app = ReplayAnalysisWindow(root)
    root.title('Dota 2 Insights · 录像与数据分析')
    menu = tk.Menu(root)
    history = tk.Menu(menu, tearoff=False)
    def open_history(name):
        try:
            location = history_location(ROOT / 'config/history.local.json', name)
            os.startfile(str(location))
        except (OSError, ValueError) as exc:
            messagebox.showerror('历史资料未配置', str(exc), parent=root)
    for name, label in (('helper_history', '原始记录与报告'), ('helper_releases', '历史交付程序'),
                        ('helper_documents', '历史项目文档')):
        history.add_command(label=label, command=lambda key=name: open_history(key))
    menu.add_cascade(label='历史资料（保留原位）', menu=history)
    root.config(menu=menu)
    root.mainloop()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        folder = ROOT / 'diagnostics/entry-errors'
        folder.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        (folder / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.txt')).write_text(
            traceback.format_exc(), encoding='utf-8')
        raise
