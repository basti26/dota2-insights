"""Export a compact 512x512 ground & camera height grid for Foresight 3D ward projection."""

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
    parser.add_argument("--grid-size", type=int, default=DEFAULT_GRID_SIZE)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    manifest = export_height_grid(
        args.collision,
        args.output,
        grid_size=args.grid_size,
        overwrite=args.overwrite,
    )
    print(json.dumps({
        "map_asset_sha256": manifest["map_asset_sha256"],
        "grid_size": manifest["grid_size"],
        "grid_sha256": manifest["grid_sha256"],
        "ground_z_range": manifest["ground_z_range"],
        "camera_z_range": manifest["camera_z_range"],
    }, indent=2))


if __name__ == "__main__":
    main()
