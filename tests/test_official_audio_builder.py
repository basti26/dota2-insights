"""Owned VPK/PCM fixtures; native decoders, game and network are never used."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave
import zlib

import numpy as np
from dota2_insights.ability_sound_matcher import RATE, WINDOW, VERSION, descriptor
from dota2_insights.valve_resources import directory_bytes, read_vpk_directory, _read_resource
from tools import build_official_ability_sound_bank as builder


def vpk_bytes(resources):
    tree, body, groups = bytearray(), bytearray(), {}
    for path, data in resources.items():
        folder, name = path.rsplit('/',1); filename, extension = name.rsplit('.',1)
        groups.setdefault(extension,{}).setdefault(folder,[]).append((filename,data))
    for extension, folders in groups.items():
        tree.extend(extension.encode()+b'\0')
        for folder, entries in folders.items():
            tree.extend(folder.encode()+b'\0')
            for name, data in entries:
                tree.extend(name.encode()+b'\0')
                tree.extend(struct.pack('<IHHIIH',zlib.crc32(data)&0xffffffff,0,0x7fff,len(body),len(data),0xffff))
                body.extend(data)
            tree.extend(b'\0')
        tree.extend(b'\0')
    tree.extend(b'\0')
    return struct.pack('<III',0x55aa1234,1,len(tree))+tree+body


def pcm_file(path):
    x = (.5*np.sin(np.arange(WINDOW)*2*np.pi*440/RATE)).astype(np.float32)
    with wave.open(str(path),'wb') as stream:
        stream.setparams((1,2,RATE,0,'NONE','not compressed'))
        stream.writeframes((x*32767).astype('<i2').tobytes())
    return x


class OfficialAudioBuilderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def base(self):
        folder = self.root/'base';folder.mkdir()
        x = pcm_file(folder/'template_001.wav')
        np.save(folder/'features.npy',descriptor(x)[None,:].astype(np.float32))
        row = {'template_id':'template_001','skill':'synthetic_old','hero_id':'npc_dota_hero_axe',
            'excerpt':'template_001.wav','excerpt_sha256':hashlib.sha256((folder/'template_001.wav').read_bytes()).hexdigest()}
        meta = {'source':'installed_Valve_original_sound_assets','directory_vpk_sha256':'a'*64,
            'feature_version':VERSION,'templates':[row],
            'features_sha256':hashlib.sha256((folder/'features.npy').read_bytes()).hexdigest()}
        (folder/'bank.json').write_text(json.dumps(meta));return folder

    def test_bounded_vpk_embedded_resources_crc_and_directory_shape(self):
        path = self.root/'pak01_dir.vpk';path.write_bytes(vpk_bytes({'sounds/test.vsnd_c':b'original-sound'}))
        entries, offset = read_vpk_directory(directory_bytes(path))
        self.assertEqual(_read_resource(path,entries['sounds/test.vsnd_c'],offset,100)[0],b'original-sound')
        with self.assertRaises(ValueError):_read_resource(path,entries['sounds/test.vsnd_c'],offset,2)
        raw = path.read_bytes();path.write_bytes(raw[:-1]+b'X')
        with self.assertRaises(ValueError):_read_resource(path,entries['sounds/test.vsnd_c'],offset,100)
        with self.assertRaises(ValueError):read_vpk_directory(b'invalid')
        with patch('dota2_insights.valve_resources.MAX_DIRECTORY_BYTES',4):
            with self.assertRaises(ValueError):directory_bytes(path)

    def test_event_export_duplicate_names_and_unsupported_format_refused(self):
        text = '\tHero.Test.Cast =\n\t{\n\t\tvolume = 1.0\n\t}\n'
        self.assertIn('Hero.Test.Cast',builder.event_blocks(text))
        with self.assertRaises(ValueError):builder.event_blocks(text+text.replace('Hero.Test','hero.test'))
        with self.assertRaises(ValueError):builder.event_blocks('unsupported export')

    def test_base_copy_preserves_vectors_excerpt_and_never_changes_pointer(self):
        folder=self.base();output=self.root/'new';output.mkdir()
        pointer=self.root/'active.json';pointer.write_bytes(b'keep')
        templates,features,hashes,revalidation=builder.load_base_bank(folder,output,'a'*64,['synthetic_new'])
        self.assertIsNone(revalidation)
        self.assertEqual(len(templates),1)
        np.testing.assert_array_equal(np.stack(features),np.load(folder/'features.npy',allow_pickle=False))
        self.assertEqual((output/'template_001.wav').read_bytes(),(folder/'template_001.wav').read_bytes())
        self.assertEqual(hashes['bank.json'],hashlib.sha256((folder/'bank.json').read_bytes()).hexdigest())
        self.assertEqual(pointer.read_bytes(),b'keep')

    def test_base_overlapping_skills_wrong_directory_and_bad_excerpt_refused(self):
        folder=self.base();output=self.root/'new';output.mkdir()
        with self.assertRaises(ValueError):builder.load_base_bank(folder,output,'b'*64,['new'])
        with self.assertRaises(ValueError):builder.load_base_bank(folder,output,'a'*64,['synthetic_old'])
        with self.assertRaises(ValueError):builder.load_base_bank(folder,folder/'inside','a'*64,['new'])
        (folder/'template_001.wav').write_bytes(b'changed')
        with self.assertRaises(ValueError):builder.load_base_bank(folder,output,'a'*64,['new'])

    def test_new_directory_epoch_requires_actual_sound_and_event_bytes_to_match(self):
        path=self.root/'pak01_dir.vpk'
        resources={'sounds/test.vsnd_c':b'sound','soundevents/test.vsndevts_c':b'event'}
        path.write_bytes(vpk_bytes(resources))
        current=hashlib.sha256(path.read_bytes()).hexdigest()
        row={'resource':'sounds/test.vsnd_c','resource_sha256':hashlib.sha256(b'sound').hexdigest(),
             'event_resource':'soundevents/test.vsndevts_c','event_resource_sha256':hashlib.sha256(b'event').hexdigest()}
        meta={'directory_vpk_sha256':'old_epoch','templates':[row]}
        result=builder.verify_base_resources(meta,path,current)
        self.assertEqual(result['resources_checked'],2)
        self.assertEqual(result['prior_directory_sha256'],'old_epoch')
        row['event_resource_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'resource_changed'):builder.verify_base_resources(meta,path,current)

    def test_no_activation_flag_even_when_all_other_paths_are_supplied(self):
        argv=['builder','--vpk','x','--decoder','x','--ffmpeg','x','--events','x','--output','x','--mapping','x','--activate']
        with patch('sys.argv',argv),redirect_stdout(io.StringIO()),patch('sys.stderr',io.StringIO()):
            with self.assertRaises(SystemExit) as cm:builder.main()
        self.assertEqual(cm.exception.code,2)

    def test_generated_vpk_to_pcm_features_and_manifest_last_with_fake_decoder(self):
        game=self.root/'game';game.mkdir()
        path=game/'pak01_dir.vpk'
        event='soundevents/game_sounds_heroes/game_sounds_test.vsndevts_c'
        sound='sounds/test/cast.vsnd_c'
        path.write_bytes(vpk_bytes({event:b'compiled-event',sound:b'compiled-sound'}))
        events=self.root/'events';events.mkdir()
        (events/'game_sounds_test.vsndevts').write_text('\tHero.Test.Cast =\n\t{\n\t\tpitch = 1.0\n\t\tvsnd_files = [ "sounds/test/cast.vsnd" ]\n\t}\n')
        mapping=self.root/'mapping.json';mapping.write_text(json.dumps({'schema_version':1,'skills':[
            {'hero':'test','skill':'test_cast','events':['Hero.Test.Cast']}]}))
        decoder=self.root/'decoder';decoder.write_bytes(b'owned fake decoder')
        ffmpeg=self.root/'ffmpeg';ffmpeg.write_bytes(b'owned fake converter')
        output=self.root/'diagnostics/bank'
        argv=['builder','--vpk',str(path),'--decoder',str(decoder),'--ffmpeg',str(ffmpeg),'--events',str(events),
              '--output',str(output),'--mapping',str(mapping),'--templates-per-skill','4']
        def execute(command,log):
            command=[str(p) for p in command]
            if command[0]==str(decoder):
                pcm_file(output/'decoded/resource_0001.wav')
            else:
                pcm_file(Path(command[-1]))
        with patch.object(builder,'ROOT',self.root),patch('sys.argv',argv),patch.object(builder,'run',side_effect=execute),redirect_stdout(io.StringIO()):
            builder.main()
        meta=json.loads((output/'bank.json').read_text())
        self.assertEqual(meta['source'],'installed_Valve_original_sound_assets')
        self.assertEqual(meta['templates'][0]['skill'],'test_cast')
        self.assertIsNone(meta['base_source_sha256'])
        self.assertFalse((output/'bank.json.tmp').exists())
        self.assertFalse((self.root/'active.json').exists())
