"""Read an explicitly supplied PresentMon CSV; never start ETW collection.

Presentation spacing is not displayed FPS. Streams stay separate, and this
reader makes no UTC, match-ID, GPU-utilization or HUD-frame correlation claim.
"""

from __future__ import annotations

import csv
from array import array
import hashlib
import io
import math
import statistics
from bisect import bisect_right
from pathlib import Path


MAX_CSV_BYTES = 1040 * 1024 * 1024  # 1 GiB recorder soft limit + shutdown/drain margin.
MAX_CSV_ROWS = 8_000_000
MAX_STREAMS = 128
METRICS = ("msbetweenpresents", "msinpresentapi", "mscpubusy", "mscpuwait",
           "msgputime", "msgpubusy", "displaylatency", "displayedtime", "msbetweendisplaychange")


def frame_summary(values):
    """FPS over measured positive intervals; 1% low has an explicit definition."""
    if not values:
        return None
    ordered = sorted(values)
    slow_count = max(1, math.ceil(len(ordered) * .01))
    return {"interval_count": len(values), "measured_interval_s": round(math.fsum(values) / 1000, 3),
            "average_fps": round(1000 / statistics.fmean(values), 2),
            "one_percent_low_fps": round(1000 / statistics.fmean(ordered[-slow_count:]), 2),
            "one_percent_low_method": "1000_divided_by_mean_of_slowest_ceil_1_percent_positive_intervals",
            "p95_frame_ms": ordered[max(0, math.ceil(.95 * len(ordered)) - 1)],
            "p99_frame_ms": ordered[max(0, math.ceil(.99 * len(ordered)) - 1)],
            "maximum_frame_ms": ordered[-1], "intervals_over_33_3_ms": sum(v > 1000 / 30 for v in values),
            "intervals_over_50_ms": sum(v > 50 for v in values),
            "intervals_over_100_ms": sum(v > 100 for v in values)}


def distribution(values):
    """Nearest-rank P95, with an explicit denominator and no invented zero."""
    if not values:
        return None
    ordered = sorted(values)
    return {"count": len(ordered), "min": round(ordered[0], 3),
            "mean": round(statistics.fmean(ordered), 3), "median": round(statistics.median(ordered), 3),
            "p95": round(ordered[max(0, math.ceil(.95 * len(ordered)) - 1)], 3), "max": round(ordered[-1], 3),
            "p95_method": "nearest_rank"}


def read_presentmon_csv(path: Path, *, application="dota2.exe", qpc_windows=None, qpc_frequency_hz=None):
    path = Path(path).resolve()
    if not isinstance(application, str) or application.casefold() != "dota2.exe":
        raise ValueError("presentmon_reader_requires_dota2_application")
    if path.stat().st_size > MAX_CSV_BYTES:
        raise ValueError("presentmon_csv_too_large")
    # Read bounded bytes once into a spooled file; large CSVs stay on disk.
    import tempfile
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
        checksum = hashlib.sha256()
        source_bytes = 0
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                source_bytes += len(block)
                if source_bytes > MAX_CSV_BYTES:
                    raise ValueError("presentmon_csv_too_large")
                checksum.update(block)
                spool.write(block)
        spool.seek(0)
        with io.TextIOWrapper(spool, encoding="utf-8-sig", newline="") as stream_reader:
            return _read_csv_rows(stream_reader, path, application, checksum.hexdigest(), source_bytes,
                                  qpc_windows=qpc_windows, qpc_frequency_hz=qpc_frequency_hz)


def _read_csv_rows(stream_reader, path, application, source_sha256, source_bytes,
                   *, qpc_windows=None, qpc_frequency_hz=None):
    reader = csv.reader(stream_reader)
    headings = next(reader, None)
    if not headings or len(headings) > 256:
        raise ValueError("invalid_presentmon_header")
    names = [name.strip().casefold() for name in headings]
    if (len(set(names)) != len(names) or not {"application", "processid", "swapchainaddress"} <= set(names)
            or not ({"msbetweenpresents", "displayedtime", "msbetweendisplaychange"} & set(names))):
        raise ValueError("unsupported_or_duplicate_presentmon_columns")
    indices = {name: index for index, name in enumerate(names)}
    scoped = qpc_windows is not None
    if scoped:
        if (type(qpc_frequency_hz) is not int or not 0 < qpc_frequency_hz <= 10**12
                or not isinstance(qpc_windows, (list, tuple)) or not 1 <= len(qpc_windows) <= 16384
                or 'qpctime' not in indices):
            raise ValueError('invalid_presentmon_qpc_scope')
        previous = 0
        for window in qpc_windows:
            if (not isinstance(window, (list, tuple)) or len(window) != 2
                    or any(type(value) is not int for value in window)
                    or not 0 < window[0] < window[1] <= 2**63 - 1 or window[0] < previous):
                raise ValueError('invalid_presentmon_qpc_window')
            previous = window[1]
        starts, ends = zip(*qpc_windows)
        def enclosed(first, last):
            index = bisect_right(starts, first) - 1
            return index >= 0 and first <= last <= ends[index]
    excluded_scope = 0
    streams, row_count, other_rows, invalid_rows = {}, 0, 0, 0
    for values in reader:
        row_count += 1
        if row_count > MAX_CSV_ROWS:
            raise ValueError("too_many_presentmon_rows")
        if len(values) != len(headings):
            invalid_rows += 1
            continue
        app = values[indices["application"]].replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if app != application.casefold():
            other_rows += 1
            continue
        qpc = None
        if scoped:
            raw = values[indices['qpctime']].strip()
            if not raw.isascii() or not raw.isdecimal() or len(raw) > 19 or not 0 < int(raw) <= 2**63 - 1:
                invalid_rows += 1
                continue
            qpc = int(raw)
            if not enclosed(qpc, qpc):
                excluded_scope += 1
                continue
        process = values[indices["processid"]]
        chain = values[indices["swapchainaddress"]]
        if not process.isascii() or not process.isdecimal() or len(process) > 10 or not 0 < int(process) <= 2**32 - 1 or not chain.strip() or len(chain) > 80:
            invalid_rows += 1
            continue
        # Never merge two swapchains, runtimes, layers or frame-generation kinds.
        runtime = (values[indices["presentruntime"]] if "presentruntime" in indices else
                   values[indices["runtime"]] if "runtime" in indices else None)
        layer = values[indices["layerindex"]] if "layerindex" in indices else None
        frame_type = values[indices["frametype"]] if "frametype" in indices else None
        key = (int(process), chain, runtime, layer, frame_type)
        if key not in streams:
            if len(streams) >= MAX_STREAMS:
                raise ValueError("too_many_presentmon_streams")
            streams[key] = {"rows": 0, "values": {name: array('d') for name in METRICS},
                            "missing": {name: 0 for name in METRICS}, "invalid": {name: 0 for name in METRICS},
                            "zero_present_intervals": 0, "display_intervals": array('d'), "cpu_intervals": array('d'),
                            "dropped_rows": 0, "unknown_display_rows": 0, "boundary_excluded_metrics": {}}
        stream = streams[key]
        stream["rows"] += 1
        dropped = values[indices["dropped"]].strip() if "dropped" in indices else None
        stream["dropped_rows"] += dropped == "1"
        stream["unknown_display_rows"] += dropped not in ("0", "1") and "displayedtime" not in indices
        parsed = {}
        for name in METRICS:
            raw = values[indices[name]].strip() if name in indices else ""
            if not raw or raw.casefold() in {"na", "n/a", "nan", "null"}:
                stream["missing"][name] += 1
                continue
            try:
                number = float(raw)
            except ValueError:
                stream["invalid"][name] += 1
                continue
            if not math.isfinite(number) or number < 0 or number > 24 * 3600 * 1000:
                stream["invalid"][name] += 1
            elif name == "msbetweenpresents" and number == 0:
                stream["zero_present_intervals"] += 1
            else:
                permitted = True
                if scoped and name == 'msbetweenpresents':
                    permitted = enclosed(qpc - number * qpc_frequency_hz / 1000, qpc)
                elif scoped and name == 'msbetweendisplaychange':
                    try:
                        delay = float(values[indices['msuntildisplayed']])
                    except (KeyError, ValueError):
                        delay = float('nan')
                    permitted = (math.isfinite(delay) and 0 <= delay <= 24 * 3600 * 1000
                        and enclosed(qpc + (delay - number) * qpc_frequency_hz / 1000,
                                     qpc + delay * qpc_frequency_hz / 1000))
                elif scoped and name == 'displayedtime':
                    permitted = False  # This pinned v1 capture has no validated v2 display-clock binding.
                if not permitted:
                    stream['boundary_excluded_metrics'][name] = stream['boundary_excluded_metrics'].get(name, 0) + 1
                    continue
                stream["values"][name].append(number)
                parsed[name] = number
        # Legacy display intervals are valid only for rows explicitly displayed.
        display = parsed.get("displayedtime")
        if display is None and dropped == "0":
            display = parsed.get("msbetweendisplaychange")
        if display is not None and display > 0:
            stream["display_intervals"].append(display)
        if all(name in parsed for name in ("mscpubusy", "mscpuwait")):
            cpu_interval = parsed["mscpubusy"] + parsed["mscpuwait"]
            if cpu_interval > 0:
                stream["cpu_intervals"].append(cpu_interval)
    if not streams:
        raise ValueError("no_attributable_dota2_presentmon_rows")
    reports = []
    for key, stream in streams.items():
        reports.append({"process_id": key[0], "swap_chain": key[1], "present_runtime": key[2],
                        "layer_index": key[3], "frame_type": key[4], "row_count": stream["rows"],
                        "metrics_ms": {name: distribution(values) for name, values in stream["values"].items()},
                        "missing_metric_rows": stream["missing"], "invalid_metric_rows": stream["invalid"],
                        "zero_present_intervals": stream["zero_present_intervals"],
                        "submitted_frames": frame_summary(stream["values"]["msbetweenpresents"]),
                        "displayed_frames": frame_summary(stream["display_intervals"]),
                        "cpu_frames": frame_summary(stream["cpu_intervals"]),
                        "dropped_rows": stream["dropped_rows"], "unknown_display_rows": stream["unknown_display_rows"],
                        "boundary_excluded_metrics": stream['boundary_excluded_metrics']})
    result = {"schema_version": 1, "kind": "presentmon_csv_review", "source_file": str(path),
            "source_sha256": source_sha256, "source_bytes": source_bytes,
            "application_filter": application, "csv_row_count": row_count, "other_application_rows": other_rows,
            "invalid_shape_or_identity_rows": invalid_rows, "streams": reports,
            "available_metric_columns": [name for name in METRICS if name in indices],
            "clock_alignment": "not_correlated_with_hud_utc",
            "match_binding": "user_selected_csv_not_verified_match_lineage",
            "display_fps": None, "gpu_utilization_percent": None,
            "limitations": ["Presentation spacing is not displayed FPS; dropped/generated frames are not inferred.",
                            "Per-stream submitted/displayed/CPU FPS are separately defined; no whole-game FPS is guessed.",
                            "1% low is the reciprocal mean of the slowest 1% measured intervals, not a percentile FPS.",
                            "GPU busy duration in milliseconds is not GPU utilization percent.",
                            "No dominant stream is guessed; each process/swapchain/runtime/layer/frame type stays separate.",
                            "Relative/QPC/date columns are not converted to or joined with HUD UTC.",
                            "Input selection does not prove the same match, scenario or recording interval."]}
    if scoped:
        result.update(clock_alignment='native_QPC_inside_declared_windows',
                      match_binding='caller_supplied_game_context_windows', qpc_frequency_hz=qpc_frequency_hz,
                      qpc_windows=qpc_windows, scope_excluded_rows=excluded_scope)
        result['limitations'] = [note for note in result['limitations'] if not note.startswith('Relative/QPC')]
        result['limitations'].append('Game context is sampled; transitions shorter than the sampling period may be missed.')
    return result
