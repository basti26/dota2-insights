"""Guard entry points before any real parser, full hash, game probe or body read."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

from dota2_insights import replay_archive as archive
from dota2_insights.replay_holdout import refuse_locked_match


class EntryLockTests(unittest.TestCase):
    def test_known_sealed_footer_path_refused_before_proto_or_file_read(self):
        with self.assertRaisesRegex(ValueError,'封存'):
            archive._footer_match_id(Path('9056629627.dem'))

    def test_known_sealed_parse_path_refused_before_game_probe(self):
        with patch.object(archive,'_windows_dota_running') as probe:
            with self.assertRaisesRegex(ValueError,'封存'):
                archive.parse_archive(Path('9056629627.dem'),Path('unwritten'),archive.ArchiveOptions())
            probe.assert_not_called()

    def test_disguised_sealed_footer_refused_before_full_hash_or_parser_import(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'generated.dem';source.write_bytes(b'generated, no actual demo')
            with patch.object(sys,'platform','linux'),patch.object(archive.importlib.metadata,'version',return_value=archive.GEM_VERSION), \
                    patch.object(archive,'_footer_match_id',return_value='9056629627'),patch.object(archive,'sha256_file') as full_hash:
                with self.assertRaisesRegex(ValueError,'封存'):
                    archive.parse_archive(source,root/'out',archive.ArchiveOptions())
                full_hash.assert_not_called()
                self.assertFalse((root/'out').exists())

    def test_ward_cli_refuses_sealed_filename_before_parser_dependency(self):
        from tools import extract_replay_wards as tool
        with tempfile.TemporaryDirectory() as t,patch.object(sys,'argv',['extract_replay_wards','9056629627.dem','--output',str(Path(t)/'out')]), \
                patch.object(sys,'path',sys.path.copy()),patch.object(tool.importlib.metadata,'version') as dependency:
            with self.assertRaisesRegex(ValueError,'封存'):tool.main()
            dependency.assert_not_called()

    def test_unfrozen_source_tree_requires_public_lock_file(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'缺失'):
                refuse_locked_match('123',Path(t))
