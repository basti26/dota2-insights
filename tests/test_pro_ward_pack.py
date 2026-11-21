import hashlib
import json
import unittest

from dota2_insights.pro_ward_pack import build_aggregated_pack


def _make_match(match_id: str, wards: list[dict]):
    raw = json.dumps(wards).encode("utf-8")
    manifest = {
        "match_id": match_id,
        "parsed_match_id": match_id,
        "complete": True,
        "parse_error": None,
        "independent_footer_matches_parser": True,
        "source_sha256": "a" * 64,
        "ward_count": len(wards),
        "files": {
            "wards.json": {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        },
    }
    return manifest, raw


class ProWardPackTests(unittest.TestCase):
    def test_clusters_nearby_placements_and_computes_frequency_and_phase_counts(self):
        m1 = _make_match("9097783398", [
            {"match_id": "9097783398", "x": 15000.0, "y": 15000.0, "ward_type": "observer", "team": 2, "placed_game_time_s": 120.0},
            {"match_id": "9097783398", "x": 15040.0, "y": 15020.0, "ward_type": "observer", "team": 3, "placed_game_time_s": 900.0},
            {"match_id": "9097783398", "x": 15010.0, "y": 15010.0, "ward_type": "sentry", "team": 2, "placed_game_time_s": 300.0},
            {"match_id": "9097783398", "x": 18000.0, "y": 18000.0, "ward_type": "observer", "team": 2, "placed_game_time_s": 1200.0, "after_match_end": True},
        ])
        m2 = _make_match("9097789980", [
            {"match_id": "9097789980", "x": 15020.0, "y": 15010.0, "ward_type": "observer", "team": 2, "placed_game_time_s": 400.0},
            {"match_id": "9097789980", "x": 19000.0, "y": 14000.0, "ward_type": "observer", "team": 3, "placed_game_time_s": 1500.0},
        ])
        pack = build_aggregated_pack([m1, m2], league_id=20208, league_name="BLAST SLAM IX", game_build=6986)
        self.assertEqual(pack["schema_version"], 1)
        self.assertEqual(pack["kind"], "historical_ward_reference")
        self.assertEqual(pack["aggregated_match_count"], 2)
        self.assertEqual(pack["source_replay_game_build"], 6986)
        self.assertEqual(pack["unavailable_count"], 1)
        # Expect Radiant & Dire clustered separately:
        # 1 radiant obs (count=2), 2 dire obs (hotspot + remote), 1 radiant sentry
        self.assertEqual(len(pack["points"]), 4)
        top = pack["points"][0]
        self.assertEqual(top["ward_type"], "observer")
        self.assertEqual(top["team"], "radiant")
        self.assertEqual(top["count"], 2)
        self.assertEqual(top["match_count"], 2)
        self.assertAlmostEqual(top["frequency"], 1.0)
        self.assertEqual(top["phase_counts"], {"opening": 1, "laning": 1, "mid": 0, "late": 0})
        self.assertEqual(top["phases"], ["opening", "laning"])
        self.assertEqual(top["early_count"], 2)
        self.assertEqual(top["mid_late_count"], 0)
        self.assertEqual(top["team_counts"], {"radiant": 2, "dire": 0})
        dire_obs = [p for p in pack["points"] if p["team"] == "dire" and p["ward_type"] == "observer"]
        self.assertEqual(len(dire_obs), 2)
        self.assertEqual(sum(p["phase_counts"]["mid"] for p in dire_obs), 1)
        self.assertEqual(sum(p["phase_counts"]["late"] for p in dire_obs), 1)

    def test_rejects_incomplete_or_tampered_match(self):
        m_ok = _make_match("9097783398", [
            {"match_id": "9097783398", "x": 15000.0, "y": 15000.0, "ward_type": "observer", "team": 2},
        ])
        bad_manifest = {**m_ok[0], "complete": False}
        with self.assertRaises(ValueError):
            build_aggregated_pack([(bad_manifest, m_ok[1])])
        with self.assertRaises(ValueError):
            build_aggregated_pack([(m_ok[0], m_ok[1] + b" ")])


if __name__ == "__main__":
    unittest.main()
