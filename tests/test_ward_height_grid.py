import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from dota2_insights.ward_height_grid import (
    DOTA_CAMERA_OPTICS,
    export_height_grid,
    rasterize_height_grid,
    sample_bilinear,
)


class WardHeightGridTests(unittest.TestCase):
    def _synthetic_mesh(self):
        # Two horizontal layers: river plane z=0 across [-4000,4000]^2 and highground z=256 in [500,3000]^2
        verts = np.array([
            [-4000.0, -4000.0, 0.0],
            [4000.0, -4000.0, 0.0],
            [4000.0, 4000.0, 0.0],
            [-4000.0, 4000.0, 0.0],
            [500.0, 500.0, 256.0],
            [3000.0, 500.0, 256.0],
            [3000.0, 3000.0, 256.0],
            [500.0, 3000.0, 256.0],
            # Steep vertical wall at z=500 that should be ignored by nz > 0.5
            [0.0, 0.0, 0.0],
            [100.0, 0.0, 0.0],
            [100.0, 0.0, 500.0],
        ], dtype=np.float64)
        tris = np.array([
            [0, 1, 2],
            [0, 2, 3],
            [4, 5, 6],
            [4, 6, 7],
            [8, 9, 10],
        ], dtype=np.int64)
        return verts, tris

    def test_rasterize_layers_and_bilinear_sampling(self):
        verts, tris = self._synthetic_mesh()
        ground_z, camera_z = rasterize_height_grid(
            verts, tris, grid_size=128, bounds=(-4096.0, 4096.0), camera_smooth_sigma_cells=2.0
        )
        self.assertEqual(ground_z.shape, (128, 128))
        self.assertAlmostEqual(sample_bilinear(ground_z, -1500.0, -1500.0, (-4096.0, 4096.0)), 0.0, delta=1.0)
        self.assertAlmostEqual(sample_bilinear(ground_z, 1750.0, 1750.0, (-4096.0, 4096.0)), 256.0, delta=1.0)
        # Vertical wall should not overwrite ground at (50, 0)
        self.assertAlmostEqual(sample_bilinear(ground_z, 50.0, 0.0, (-4096.0, 4096.0)), 0.0, delta=1.0)
        offset = DOTA_CAMERA_OPTICS["camera_height_offset_world_units"]
        self.assertAlmostEqual(sample_bilinear(camera_z, -1500.0, -1500.0, (-4096.0, 4096.0)), offset, delta=5.0)
        self.assertAlmostEqual(sample_bilinear(camera_z, 1750.0, 1750.0, (-4096.0, 4096.0)), 256.0 + offset, delta=10.0)

    def test_export_height_grid_binds_digests(self):
        verts, tris = self._synthetic_mesh()
        with tempfile.TemporaryDirectory() as tmp:
            col = Path(tmp) / "collision"
            out = Path(tmp) / "out"
            col.mkdir()
            npz = col / "meshes.npz"
            np.savez_compressed(npz, vertices_0=verts, triangles_0=tris)
            archive_sha = hashlib.sha256(npz.read_bytes()).hexdigest()
            map_sha = "a" * 64
            (col / "collision.json").write_text(json.dumps({
                "client_build": 6983,
                "map_asset_sha256": map_sha,
                "mesh_archive_sha256": archive_sha,
            }), encoding="utf-8")

            manifest = export_height_grid(col, out, grid_size=64, bounds=(-4096.0, 4096.0))
            self.assertEqual(manifest["map_asset_sha256"], map_sha)
            self.assertEqual(manifest["grid_size"], 64)
            self.assertTrue((out / "ward-height-grid.local.json").is_file())
            self.assertTrue((out / "ward-height-grid.local.npz").is_file())
            self.assertEqual(
                hashlib.sha256((out / "ward-height-grid.local.npz").read_bytes()).hexdigest(),
                manifest["grid_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
