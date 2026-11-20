"""Export a compact 512x512 walkable ground height grid for Foresight 3D ward projection.

Camera optics are never hardcoded: pass `--calibration <candidate.json>` from
`tools/calibrate_native_camera.py` to embed validated conditional-candidate optics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.ward_height_grid import DEFAULT_GRID_SIZE, export_height_grid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collision", type=Path, required=True, help="Collision directory containing collision.json and meshes.npz")
    parser.add_argument("--output", type=Path, required=True, help="Target directory for ward-height-grid.local.{json,npz}")
    parser.add_argument(
        "--calibration",
        type=Path,
        default=None,
        help="Optional native_camera_calibration.py conditional_candidate JSON to embed validated optics",
    )
    parser.add_argument("--grid-size", type=int, default=DEFAULT_GRID_SIZE)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    manifest = export_height_grid(
        args.collision,
        args.output,
        calibration_path=args.calibration,
        grid_size=args.grid_size,
        overwrite=args.overwrite,
    )
    print(json.dumps({
        "map_asset_sha256": manifest["map_asset_sha256"],
        "grid_size": manifest["grid_size"],
        "grid_sha256": manifest["grid_sha256"],
        "ground_z_range": manifest["ground_z_range"],
        "smooth_ground_z_range": manifest["smooth_ground_z_range"],
        "optics_embedded": manifest["optics"] is not None,
    }, indent=2))


if __name__ == "__main__":
    main()
