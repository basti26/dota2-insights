"""Explicit offline annotation window for a frozen native-camera pixel plan."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--draft',type=Path)
    args=parser.parse_args()
    from dota2_insights.native_anchor_editor import AnchorDraft,AnchorEditor,read_json
    draft=AnchorDraft(args.input)
    if args.draft:draft.restore(read_json(args.draft)[1])
    import tkinter as tk
    root=tk.Tk();root.title('镜头地标 · 离线像素标注')
    AnchorEditor(root,draft)
    root.mainloop()


if __name__=='__main__':main()
