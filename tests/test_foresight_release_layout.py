"""Generated release directories only; no private history or real app launch."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dota2_insights import (helper_handoff, match_journal_index, observation_index,
    draft_phase_index, minimap_index, audio_match_index, performance_index)


class ForesightReleaseLayouts(unittest.TestCase):
    layouts = (("foresight-test", "Foresight"), ("foresight-economy-test", "ForesightEconomy"),
               ("helper-test", "Dota2Helper"), ("economy-test", "Dota2Economy"))

    def channels(self):
        run = "run_" + "a" * 32
        return (
            (match_journal_index, "self-economy/self-economy-12345678-1234-1234-1234-123456789abc/closed-matches/match_12345/game_" + "b" * 32),
            (observation_index, "matches/sessions/" + run + "/observation-handoff.json"),
            (draft_phase_index, "drafts/session_" + "a" * 32 + "/phase-handoff.json"),
            (minimap_index, "minimap-learning/closed-runs/" + run + "/record-handoff.json"),
            (audio_match_index, "match-audio/" + run + "/closed-matches/scope_" + "b" * 64),
            (performance_index, "game-frames/" + run + "/closed-match"))

    def test_all_channels_accept_new_and_legacy_names(self):
        with TemporaryDirectory() as folder:
            roots = {"helper_releases": Path(folder)}
            for release, app in self.layouts:
                for module, tail in self.channels():
                    with self.subTest(release=release, channel=module.__name__):
                        result = module.resolve_reference({"root_key": "helper_releases",
                            "source": release + "/" + app + "/diagnostics/" + tail}, roots)
                        target = result[0] if isinstance(result, tuple) else result
                        self.assertTrue(target.is_relative_to(Path(folder)))
                relative = release + "/" + app + "/diagnostics/field-recording/run_" + "a" * 32 + "/recorders.json"
                self.assertEqual(helper_handoff._registry_base("helper_releases", relative).as_posix(),
                                 release + "/" + app + "/diagnostics")

    def test_new_names_do_not_allow_arbitrary_apps_or_traversal(self):
        with TemporaryDirectory() as folder:
            roots = {"helper_releases": Path(folder)}
            for module, tail in self.channels():
                for prefix in ("foresight-test/OtherApp/diagnostics/", "random/Foresight/diagnostics/",
                               "foresight-test/Foresight/../diagnostics/"):
                    with self.subTest(channel=module.__name__, prefix=prefix), self.assertRaises(ValueError):
                        module.resolve_reference({"root_key": "helper_releases", "source": prefix + tail}, roots)

    def test_registry_discovery_and_shared_bases_find_new_and_old_releases(self):
        with TemporaryDirectory() as folder:
            roots = {"helper_releases": Path(folder)}
            expected = set()
            for release, app in self.layouts:
                base = Path(folder)/release/app/"diagnostics"
                path = base/("field-recording/run_" + "a" * 32)/"recorders.json"
                path.parent.mkdir(parents=True)
                path.write_text("{}", encoding="utf-8")
                expected.add(path)
            self.assertEqual({row[2] for row in helper_handoff.discover(roots)}, expected)
            bases = {row[2] for row in match_journal_index.history_bases(roots)}
            self.assertTrue({p.parent.parent.parent for p in expected}.issubset(bases))
