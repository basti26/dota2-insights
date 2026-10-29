from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
from tests.test_tier1_postgame_store import entry, snapshot
from tools import review_ward_phase_profile as cli


class WardPhaseSnapshotCliTests(unittest.TestCase):
    def test_frozen_snapshot_drives_report_and_binds_original_manifest(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);(root/'diagnostics').mkdir();s=root/'snapshot.json';snapshot(s,[entry()])
            mp=root/'map.jpg';mp.write_bytes(b'synthetic-map')
            out=root/'diagnostics/report'
            args=['review','--snapshot',str(s),'--map',str(mp),'--output',str(out)]
            with patch.object(cli,'ROOT',root), patch('sys.argv',args), \
                 patch.object(cli,'load_map',return_value=(mp.read_bytes(),Image.new('RGB',(16,16)))), \
                 redirect_stdout(io.StringIO()):
                cli.main()
            d=json.loads((out/'profile.json').read_text(encoding='utf-8'))
            self.assertEqual(len(d['matches']),1)
            self.assertEqual(d['input_snapshot_sha256'],hashlib.sha256(s.read_bytes()).hexdigest())
            self.assertEqual(len(d['input_manifest_sha256']),1)
            self.assertIn('9876543210',(out/'report.html').read_text(encoding='utf-8'))

    def test_changed_body_is_rejected_before_map_or_output(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);(root/'diagnostics').mkdir();s=root/'snapshot.json'
            e=entry();e['wards_base64']='W10=';snapshot(s,[e]);out=root/'diagnostics/report'
            args=['review','--snapshot',str(s),'--map',str(root/'map.jpg'),'--output',str(out)]
            with patch.object(cli,'ROOT',root),patch('sys.argv',args),patch.object(cli,'load_map') as image:
                with self.assertRaises(ValueError):cli.main()
                image.assert_not_called()
            self.assertFalse(out.exists())
