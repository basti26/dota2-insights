"""Evaluate provisional B0/B1 coarse-grid proxies on saved replay evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.minimap.replay_baselines import evaluate


def game_guard():
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ValueError("baseline_process_guard_unavailable")
    entry = Entry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        if not ok:
            raise ValueError("baseline_process_guard_unavailable")
        while ok:
            if entry.szExeFile.lower() in ("dota.exe", "dota2.exe"):
                raise ValueError("baseline_refused_while_dota_running")
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", nargs=2, action="append", required=True,
                        metavar=("ARCHIVE_DIRECTORY", "VISIBILITY_CANDIDATES_V2_JSON"))
    parser.add_argument("--hero-stats", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if output.exists() or output.suffix != ".json" or not output.is_relative_to((ROOT / "diagnostics").resolve()):
            raise ValueError("baseline_new_diagnostics_json_required")
        game_guard()
        last_check = time.monotonic()
        def guard():
            nonlocal last_check
            if time.monotonic() - last_check >= 5:
                game_guard()
                last_check = time.monotonic()
        report = evaluate(args.case, hero_stats=args.hero_stats, guard=guard)
        body = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)
        if len(body.encode("utf-8")) > 16 * 1024 ** 2:
            raise ValueError("baseline_output_limit")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(body + "\n")
        print(json.dumps({"status": "proxy_analysis_written", "training_ready": False,
            "cases": [{"match_id": c["sources"]["match_id"], "events": len(c["events"]),
                       "summary": c["summary"], "paired": c["paired_same_input_events"]}
                      for c in report["cases"]]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)[:160]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
