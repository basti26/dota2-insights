import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from dota2_insights.ward_height_grid import (
    export_height_grid,
    optics_from_calibration,
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

    def _valid_candidate(self):
        return {
            "version": "native_position_anchor_calibration_v1",
            "status": "conditional_candidate",
            "reason": "fit_and_separate_checks_passed",
            "native_viewport": [2560, 1440],
            "fit_anchor_count": 6,
            "check_anchor_count": 4,
            "fit_rms_px": 1.2,
            "check_max_error_px": 2.5,
            "pitch_candidate_degrees": 58.5,
            "vertical_fov_candidate_degrees": 64.2,
            "yaw_xy_degrees": 90.0,
            "camera_xyz": [-1000.0, -1600.0, 1150.0],
            "look_at_z_candidate": 120.0,
            "live_usable": False,
            "coordinate_accuracy_verified": False,
        }

    def test_rasterize_layers_and_bilinear_sampling_has_no_baked_camera_offset(self):
        verts, tris = self._synthetic_mesh()
        ground_z, smooth_z = rasterize_height_grid(
            verts, tris, grid_size=128, bounds=(-4096.0, 4096.0), smooth_sigma_cells=2.0
        )
        self.assertEqual(ground_z.shape, (128, 128))
        self.assertAlmostEqual(sample_bilinear(ground_z, -1500.0, -1500.0, (-4096.0, 4096.0)), 0.0, delta=1.0)
        self.assertAlmostEqual(sample_bilinear(ground_z, 1750.0, 1750.0, (-4096.0, 4096.0)), 256.0, delta=1.0)
        # Vertical wall should not overwrite ground at (50, 0)
        self.assertAlmostEqual(sample_bilinear(ground_z, 50.0, 0.0, (-4096.0, 4096.0)), 0.0, delta=1.0)
        self.assertAlmostEqual(sample_bilinear(smooth_z, -1500.0, -1500.0, (-4096.0, 4096.0)), 0.0, delta=5.0)
        self.assertAlmostEqual(sample_bilinear(smooth_z, 1750.0, 1750.0, (-4096.0, 4096.0)), 256.0, delta=10.0)

    def test_export_height_grid_omits_optics_without_calibration_and_embeds_when_validated(self):
        verts, tris = self._synthetic_mesh()
        with tempfile.TemporaryDirectory() as tmp:
            col = Path(tmp) / "collision"
            out_plain = Path(tmp) / "out_plain"
            out_cal = Path(tmp) / "out_cal"
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

            manifest_plain = export_height_grid(col, out_plain, grid_size=64, bounds=(-4096.0, 4096.0))
            self.assertEqual(manifest_plain["map_asset_sha256"], map_sha)
            self.assertIsNone(manifest_plain["optics"])
            self.assertIsNone(manifest_plain["calibration_provenance"])
            self.assertEqual(
                hashlib.sha256((out_plain / "ward-height-grid.local.npz").read_bytes()).hexdigest(),
                manifest_plain["grid_sha256"],
            )

            cal_path = Path(tmp) / "candidate.json"
            cal_path.write_text(json.dumps(self._valid_candidate()), encoding="utf-8")
            manifest_cal = export_height_grid(
                col, out_cal, calibration_path=cal_path, grid_size=64, bounds=(-4096.0, 4096.0)
            )
            self.assertAlmostEqual(manifest_cal["optics"]["pitch_degrees"], 58.5)
            self.assertAlmostEqual(manifest_cal["optics"]["vfov_degrees"], 64.2)
            self.assertAlmostEqual(manifest_cal["optics"]["camera_height_offset_world_units"], 1030.0)
            self.assertEqual(manifest_cal["calibration_provenance"]["fit_anchor_count"], 6)
            self.assertEqual(manifest_cal["calibration_provenance"]["check_anchor_count"], 4)

    def test_optics_from_calibration_enforces_2px_4px_gate_and_rejects_failed_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            base = self._valid_candidate()
            for mutation in (
                {"status": "unavailable"},
                {"fit_anchor_count": 5},
                {"check_anchor_count": 3},
                {"fit_rms_px": 2.7},          # > 2.0 * (1440/1080) = 2.667
                {"check_max_error_px": 5.4},  # > 4.0 * (1440/1080) = 5.333
                {"pitch_candidate_degrees": 30.0},
            ):
                with self.subTest(mutation=mutation):
                    path.write_text(json.dumps({**base, **mutation}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        optics_from_calibration(path)


if __name__ == "__main__":
    unittest.main()
