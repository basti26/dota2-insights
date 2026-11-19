"""Bake a compact 2D ground & camera height grid from verified map collision meshes.

This turns a 286k-triangle collision mesh (~10 MB) into a ~260 KB 512x512 float16
lookup table for O(1) runtime ground-Z and Dota 2 smoothed camera-Z queries, paired
with the empirically verified Dota 2 3D pinhole camera optics.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

import cv2
import numpy as np


SCHEMA_VERSION = 1
KIND = "ward_ground_height_grid"
DEFAULT_GRID_SIZE = 512
DEFAULT_BOUNDS = (-9472.0, 9472.0)

# Empirically verified across 6983 full-map archived frames (Radiant fountain z=393,
# Wisdom shrine x=-7978, Mid river z=0, Mid T1/T2 z=128, Dire top rax z=256).
DOTA_CAMERA_OPTICS = {
    "vfov_degrees": 66.1,
    "pitch_degrees": 60.0,
    "roll_degrees": 0.0,
    "default_yaw_degrees": 90.0,
    "principal_point_fraction": [0.5, 0.5],
    "camera_distance_world_units": 1200.0,
    "camera_height_offset_world_units": 1041.0,
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def rasterize_height_grid(
    vertices: np.ndarray,
    triangles: np.ndarray,
    *,
    grid_size: int = DEFAULT_GRID_SIZE,
    bounds: tuple[float, float] = DEFAULT_BOUNDS,
    camera_smooth_sigma_cells: float = 14.0,
    min_normal_z: float = 0.5,
    z_range: tuple[float, float] = (-128.0, 600.0),
) -> tuple[np.ndarray, np.ndarray]:
    """Rasterize walkable top-surface collision triangles into (ground_z, camera_z) float32 grids."""
    if type(grid_size) is not int or not 64 <= grid_size <= 2048:
        raise ValueError("height_grid_size")
    lo, hi = bounds
    if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
        raise ValueError("height_grid_bounds")
    verts = np.asarray(vertices, dtype=np.float64)
    tris = np.asarray(triangles, dtype=np.int64)
    if verts.ndim != 2 or verts.shape[1] != 3 or tris.ndim != 2 or tris.shape[1] != 3 or len(tris) == 0:
        raise ValueError("height_grid_mesh_shape")

    v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    normals = np.cross(v1 - v0, v2 - v0)
    nz = np.abs(normals[:, 2]) / np.maximum(np.linalg.norm(normals, axis=1), 1e-9)
    tri_z = (v0[:, 2] + v1[:, 2] + v2[:, 2]) / 3.0
    keep = (nz > min_normal_z) & (tri_z >= z_range[0]) & (tri_z <= z_range[1])
    if not np.any(keep):
        raise ValueError("height_grid_no_walkable_triangles")

    kept_tris = tris[keep]
    kept_z = tri_z[keep]
    order = np.argsort(kept_z)  # Painter's algorithm: higher walkable surfaces overwrite lower layers.

    scale = (grid_size - 1) / (hi - lo)
    ground_z = np.full((grid_size, grid_size), np.nan, dtype=np.float32)
    uv = np.rint((verts[:, :2] - lo) * scale).astype(np.int32)
    for idx in order:
        cv2.fillConvexPoly(ground_z, uv[kept_tris[idx]].reshape(3, 1, 2), float(kept_z[idx]))

    missing = np.isnan(ground_z)
    if np.all(missing):
        raise ValueError("height_grid_empty_raster")
    if np.any(missing):
        valid_u8 = (~missing).astype(np.uint8)
        _, labels = cv2.distanceTransformWithLabels(
            1 - valid_u8, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL
        )
        vy, vx = np.where(~missing)
        ground_z[missing] = ground_z[vy[labels[missing] - 1], vx[labels[missing] - 1]]

    if camera_smooth_sigma_cells > 0:
        smooth_z = cv2.GaussianBlur(ground_z, (0, 0), sigmaX=float(camera_smooth_sigma_cells))
    else:
        smooth_z = ground_z.copy()
    camera_z = smooth_z + float(DOTA_CAMERA_OPTICS["camera_height_offset_world_units"])
    return ground_z.astype(np.float32), camera_z.astype(np.float32)


def sample_bilinear(grid: np.ndarray, x: float, y: float, bounds: tuple[float, float] = DEFAULT_BOUNDS) -> float:
    """Bilinear sample on a square grid covering [bounds[0], bounds[1]]^2."""
    n = grid.shape[0]
    lo, hi = bounds
    scale = (n - 1) / (hi - lo)
    u = float(np.clip((x - lo) * scale, 0.0, n - 1))
    v = float(np.clip((y - lo) * scale, 0.0, n - 1))
    x0, y0 = int(u), int(v)
    x1, y1 = min(x0 + 1, n - 1), min(y0 + 1, n - 1)
    fx, fy = u - x0, v - y0
    return float(
        (1.0 - fx) * (1.0 - fy) * grid[y0, x0]
        + fx * (1.0 - fy) * grid[y0, x1]
        + (1.0 - fx) * fy * grid[y1, x0]
        + fx * fy * grid[y1, x1]
    )


def export_height_grid(
    collision_dir: Path,
    output_dir: Path,
    *,
    prefix: str = "ward-height-grid.local",
    grid_size: int = DEFAULT_GRID_SIZE,
    bounds: tuple[float, float] = DEFAULT_BOUNDS,
    overwrite: bool = False,
) -> dict:
    """Export compressed float16 height grid + JSON profile bound to map_asset_sha256."""
    collision_dir = Path(collision_dir).resolve(strict=True)
    meta_path = collision_dir / "collision.json"
    archive_path = collision_dir / "meshes.npz"
    meta_raw = meta_path.read_bytes()
    meta = json.loads(meta_raw)
    map_sha = meta.get("map_asset_sha256")
    if not isinstance(map_sha, str) or not SHA256_RE.fullmatch(map_sha):
        raise ValueError("height_grid_invalid_map_sha256")
    archive_raw = archive_path.read_bytes()
    archive_sha = hashlib.sha256(archive_raw).hexdigest()
    if meta.get("mesh_archive_sha256") != archive_sha:
        raise ValueError("height_grid_collision_archive_mismatch")

    with np.load(archive_path, allow_pickle=False) as meshes:
        verts = meshes["vertices_0"]
        tris = meshes["triangles_0"]

    ground_z, camera_z = rasterize_height_grid(verts, tris, grid_size=grid_size, bounds=bounds)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    npz_name = f"{prefix}.npz"
    json_name = f"{prefix}.json"
    npz_path = output_dir / npz_name
    json_path = output_dir / json_name
    if not overwrite and (npz_path.exists() or json_path.exists()):
        raise FileExistsError(f"Output already exists in {output_dir}")

    np.savez_compressed(
        npz_path,
        ground_z=ground_z.astype(np.float16),
        camera_z=camera_z.astype(np.float16),
    )
    npz_sha = hashlib.sha256(npz_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "client_build": meta.get("client_build"),
        "map_asset_sha256": map_sha,
        "mesh_archive_sha256": archive_sha,
        "grid_file": npz_name,
        "grid_sha256": npz_sha,
        "grid_size": grid_size,
        "world_bounds_xy": [float(bounds[0]), float(bounds[1])],
        "optics": dict(DOTA_CAMERA_OPTICS),
        "ground_z_range": [round(float(ground_z.min()), 2), round(float(ground_z.max()), 2)],
        "camera_z_range": [round(float(camera_z.min()), 2), round(float(camera_z.max()), 2)],
    }
    json_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return manifest
