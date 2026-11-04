from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from tools.extract_map_collision import decode


class DecoderProcessTests(unittest.TestCase):
    def test_game_start_terminates_decoder_and_leaves_no_success_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'PHYS.txt';child=Mock();child.poll.return_value=None
            check=Mock(side_effect=[None,InterruptedError('game_running')])
            with patch('tools.extract_map_collision.subprocess.Popen',return_value=child),self.assertRaises(InterruptedError):
                decode(Path('decoder.exe'),Path('compiled.vmdl_c'),output,check)
            child.terminate.assert_called_once()
            self.assertFalse((output.parent/'COMPLETE').exists())

    def test_failed_child_never_becomes_successful_decode(self):
        with tempfile.TemporaryDirectory() as temp:
            child=Mock();child.poll.return_value=1;child.returncode=1
            with patch('tools.extract_map_collision.subprocess.Popen',return_value=child),self.assertRaisesRegex(ValueError,'decoder_failed'):
                decode(Path('decoder.exe'),Path('compiled.vmdl_c'),Path(temp)/'PHYS.txt',lambda:None)

    def test_metadata_block_whitelist_and_command_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            for block in ('CTRL','RED2'):
                child=Mock();child.poll.return_value=0;child.returncode=0
                with patch('tools.extract_map_collision.subprocess.Popen',return_value=child) as spawn:
                    decode(Path('decoder.exe'),Path('compiled.vmdl_c'),Path(temp)/(block+'.txt'),lambda:None,block=block)
                    self.assertEqual(spawn.call_args.args[0][-4:],['--block',block,'--threads','1'])
            with patch('tools.extract_map_collision.subprocess.Popen') as spawn,self.assertRaisesRegex(ValueError,'unsupported'):
                decode(Path('decoder.exe'),Path('compiled.vmdl_c'),Path(temp)/'bad.txt',lambda:None,block='--help')
            spawn.assert_not_called()
