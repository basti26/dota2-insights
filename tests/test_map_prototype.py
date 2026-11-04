import copy
from dataclasses import replace
import json
import struct
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from dota2_insights.map_holdout import sha
from dota2_insights.map_prototype import ViewTransform, open_scene, prepare, project_marker, load_ward_candidates, ward_candidate_xy
from dota2_insights.map_prototype_view import PrototypeWindow, export_overview, texture_view, world_image
from dota2_insights.virtual_map import VirtualMap, MapIdentity, WorldBounds


def fixture(root):
    (root/'frames').mkdir()
    Image.new('RGB', (101, 101), '#111111').save(root/'collision.png')
    Image.new('RGB', (360, 360), '#253a4b').save(root/'frames/00.png')
    scene = {'schema_version': 1, 'kind': 'offline_virtual_map_prototype',
        'p2_pass': False, 'collision_world_placement_verified': False, 'client_build': 10, 'map_asset_sha256': 'a'*64,
        'bounds': {'min_x': -10000, 'min_y': -10000, 'max_x': 10000, 'max_y': 10000},
        'collision_texture': {'image': 'collision.png', 'footprint': {
            'min_x': -50.5, 'min_y': -50.5, 'max_x': 50.5, 'max_y': 50.5}},
        'markers': [{'id': 'a', 'name': 'lotus', 'classname': 'npc_dota_lotus_pool', 'world_xyz': [-1000, 1000, 30]},
            {'id': 'b', 'name': 'fountain', 'classname': 'ent_dota_fountain', 'world_xyz': [1000, -1000, 40]}],
        'frames': [{'case': 'day', 'frame_index': 1, 'size': [360, 360], 'crop_origin_client_xy': [8, 100], 'image': 'frames/00.png'}],
        'historical_matrix_client': {'scale_x': .02, 'scale_y': -.02, 'offset_x': 190, 'offset_y': 280},
        'comparisons': []}
    scene['virtual_map_model'] = VirtualMap(MapIdentity('dota', 10, 'a'*64), WorldBounds(**scene['bounds']),
        'source2_map_centered', 'asset_extracted', 'synthetic map fixture').to_dict()
    write_scene(root, scene)
    return scene


def write_scene(root, scene):
    (root/'scene.json').write_text(json.dumps(scene), encoding='utf-8')
    files = ['scene.json', 'collision.png', 'frames/00.png']
    if scene.get('root_assets'):files += ['gnv.png','gnv.bin']
    layer = scene.get('ward_candidates')
    (root/'manifest.json').write_text(json.dumps({'ward_match_id': layer['match_id'] if layer else None,
        'files': {p: sha(root/p) for p in files}}), encoding='utf-8')
    (root/'COMPLETE').write_text(sha(root/'manifest.json'), encoding='ascii')


class PrototypeTests(unittest.TestCase):
    def test_root_grid_tree_layers_and_export_share_the_real_view_controls(self):
        import tkinter as tk
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scene=fixture(root)
            Image.new('RGB',(2,3),'#487682').save(root/'gnv.png')
            (root/'gnv.bin').write_bytes(struct.pack('<IfffIIii',0xFADEBEAD,64,32,32,2,3,-1,-1)+bytes([1,4,16,20,13,25]))
            scene['root_assets']={'world_placement_verified':False,'grid_image':'gnv.png','grid_bytes':'gnv.bin',
                'grid_info':{'xy_bounds_candidate':{'min_x':-64,'min_y':-64,'max_x':64,'max_y':128}},
                'trm':{'record_count':2,'points':[{'xy':[0,0]},{'xy':[64,64]}]},
                'tree_comparison':{'exact_xy_layer_matches':1,'trm_records_not_paired_to_entities':[{'xy':[64,64]}]}}
            write_scene(root,scene)
            self.assertEqual(open_scene(root)['root_assets']['trm']['record_count'],2)
            window=tk.Tk();window.withdraw()
            try:
                app=PrototypeWindow(window,root)
                app.physics.set(True);app.grid_layer.set(True);app.tree_layer.set(True)
                app.choose_grid()
                self.assertFalse(app.physics.get())
                self.assertTrue(app.grid_layer.get())
                app.motion(SimpleNamespace(x=app.view.width/2,y=app.view.height/2))
                self.assertIn('0x14',app.cursor.get())
                app.physics.set(True);app.choose_physics()
                self.assertFalse(app.grid_layer.get())
                app.redraw()
            finally:window.destroy()
            export_overview(root,root/'roots.png')
            with Image.open(root/'roots.png') as im:self.assertEqual(im.size,(1340,1560))
            texture=Image.new('RGB',(2,2),'#ff0000');texture.putpixel((1,0),(0,255,0))
            out=texture_view(texture,{'min_x':-64,'min_y':-64,'max_x':64,'max_y':64},ViewTransform(width=200,height=200,scale=1),categorical=True)
            colors=set(map(tuple,np.asarray(out).reshape(-1,3).tolist()))
            self.assertTrue(colors<={(255,0,0),(0,255,0),(17,30,41)})

    def test_pan_zoom_inverse_and_cursor_anchor_preserve_world_coordinates(self):
        view = ViewTransform(width=701, height=593)
        points = [(-9000, 8000), (0, 0), (9400, -8200)]
        for i in range(40):
            cursor = (int(i*13 % view.width), int(i*31 % view.height))
            anchor = view.world(*cursor)
            view = view.zoom(1.2 if i%3 else 1/1.2, *cursor)
            self.assertAlmostEqual(view.world(*cursor)[0], anchor[0], places=8)
            self.assertAlmostEqual(view.world(*cursor)[1], anchor[1], places=8)
            view = view.pan(i-15, 9-i)
            for x,y in points:
                recovered = view.world(*view.pixel(x, y))
                self.assertAlmostEqual(recovered[0], x, places=8)
                self.assertAlmostEqual(recovered[1], y, places=8)

    def test_bad_view_and_zoom_inputs_rejected(self):
        for kwargs in ({'scale': 0}, {'center_x': float('nan')}, {'width': True}, {'height': 9000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ViewTransform(**kwargs)
        with self.assertRaises(ValueError):
            ViewTransform().zoom(-1, 5, 5)

    def test_real_display_route_uses_candidate_virtual_map_normalization(self):
        model = VirtualMap(MapIdentity('dota', 10, 'a'*64), WorldBounds(-9472,-9472,9472,9472),
            'source2_map_centered', 'asset_extracted', 'synthetic')
        normalized = ViewTransform(map_model=model)
        plain = ViewTransform()
        for p in ((0,0), (-7548,4209), (10500,-11000)):
            a,b = normalized.pixel(*p), plain.pixel(*p)
            self.assertAlmostEqual(a[0],b[0],places=9)
            self.assertAlmostEqual(a[1],b[1],places=9)
            for q,r in zip(normalized.world(*a),p): self.assertAlmostEqual(q,r,places=8)
        zoomed = normalized.zoom(1.8, 100, 500).pan(20,-40)
        self.assertIs(zoomed.map_model, model)
        self.assertEqual(model.validation_status, 'unverified')

    def test_texture_and_marker_share_the_same_transform_under_pan_zoom(self):
        import numpy as np
        texture = Image.new('RGB', (101, 101), 'black')
        texture.putpixel((50, 50), (0, 255, 0))
        footprint = {'min_x': -50.5, 'max_x': 50.5, 'min_y': -50.5, 'max_y': 50.5}
        view = ViewTransform(scale=2, width=200, height=160)
        for view in (view, view.pan(17, -11), view.zoom(1.8, 120, 70), replace(view, center_x=12, center_y=-9)):
            out = np.asarray(texture_view(texture, footprint, view))[:, :, 1]
            y,x = np.unravel_index(out.argmax(), out.shape)
            expected = view.pixel(0, 0)
            self.assertLess(abs(x+.5-expected[0]), 1.5)
            self.assertLess(abs(y+.5-expected[1]), 1.5)

    def test_projections_are_fixed_and_do_not_modify_world_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            scene = fixture(Path(tmp))
            before = copy.deepcopy(scene)
            marker, frame = scene['markers'][0], scene['frames'][0]
            self.assertEqual(project_marker(scene, marker, frame, 'asset_window'), [162, 162])
            self.assertEqual(project_marker(scene, marker, frame, 'historical_tower_model'), [162, 160])
            with self.assertRaises(ValueError):
                project_marker(scene, marker, frame, 'auto_fit')
            world_image(scene, Image.new('RGB', (101, 101)), ViewTransform(), selected='a', labels=True)
            self.assertEqual(scene, before)

    def test_changed_display_file_and_forged_certification_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scene = fixture(root)
            self.assertEqual(len(open_scene(root)['markers']), 2)
            Image.new('RGB', (360, 360), '#ab2345').save(root/'frames/00.png')
            with self.assertRaisesRegex(ValueError, 'display_file_changed'):
                open_scene(root)
            scene['p2_pass'] = True
            write_scene(root, scene)
            with self.assertRaisesRegex(ValueError, 'scene_invalid'):
                open_scene(root)

    def test_manifest_escape_and_duplicate_world_identity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'package'
            root.mkdir()
            scene = fixture(root)
            scene['markers'][1]['id'] = 'a'
            write_scene(root, scene)
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                open_scene(root)
            manifest = {'ward_match_id': None, 'files': {'scene.json': sha(root/'scene.json'), '../outside.png': 'a'*64, 'collision.png': sha(root/'collision.png')}}
            (root/'manifest.json').write_text(json.dumps(manifest))
            (root/'COMPLETE').write_text(sha(root/'manifest.json'))
            with self.assertRaisesRegex(ValueError, 'path_escape'):
                open_scene(root)

    def test_deterministic_export_uses_actual_renderer_without_gui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            first, second = root/'one.png', root/'two.png'
            export_overview(root, first)
            export_overview(root, second)
            self.assertEqual(sha(first), sha(second))
            with Image.open(first) as im:
                self.assertEqual(im.size, (1840, 840))

    def test_tk_controls_select_pan_zoom_and_compare_without_game(self):
        import tkinter as tk
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scene = fixture(root)
            scene['ward_candidates'] = {'match_id': '1234567890', 'map_version_alignment_verified': False,
                'world_projection_verified': False, 'raw_coordinate_frame': 'gem_cell128_plus_vec_raw',
                'translation_candidate_xy': [-16384,-16384], 'points': [{'id': 'ward-test', 'raw_world_xy': [16884,16884],
                    'ward_type': 'observer', 'team': 2, 'placed_game_time_s': 35}]}
            write_scene(root, scene)
            window = tk.Tk()
            window.withdraw()
            try:
                app = PrototypeWindow(window, root)
                with patch.object(app.world_canvas, 'winfo_width', return_value=700), \
                        patch.object(app.world_canvas, 'winfo_height', return_value=650), \
                        patch.object(app.shot_canvas, 'winfo_width', return_value=650), \
                        patch.object(app.shot_canvas, 'winfo_height', return_value=650):
                    app.redraw()
                    app.marker_name.set(app.marker_names()[1])
                    app.choose_marker()
                    self.assertEqual(app.selected, 'b')
                    self.assertIn('40.000', app.detail.get())
                    anchor = app.view.world(300, 200)
                    app.wheel(SimpleNamespace(delta=120, x=300, y=200))
                    self.assertAlmostEqual(app.view.world(300, 200)[0], anchor[0])
                    app.press(SimpleNamespace(x=30, y=40))
                    app.drag(SimpleNamespace(x=50, y=60))
                    app.release(SimpleNamespace(x=50, y=60))
                    x,y = app.view.pixel(-1000, 1000)
                    app.press(SimpleNamespace(x=x, y=y))
                    app.release(SimpleNamespace(x=x, y=y))
                    self.assertEqual(app.selected, 'a')
                    app.mode.set('声明边界铺满截图（假设）')
                    app.physics.set(True)
                    app.redraw()
                    self.assertIn('162.00, 162.00', app.detail.get())
                    self.assertEqual(app.world_photo.width(), 700)
                    app.reset()
                    app.wards.set(True)
                    x,y = app.view.pixel(500, 500)
                    app.press(SimpleNamespace(x=x, y=y))
                    app.release(SimpleNamespace(x=x, y=y))
                    self.assertEqual(app.selected_ward, 'ward-test')
                    self.assertIn('不同场景', app.detail.get())
                    self.assertIsNone(app.selected)
                    app.choose_marker()
                    self.assertIsNone(app.selected_ward)
                    app.ward_phase.set('10–20分钟')
                    app.choose_ward_phase()
                    self.assertIn('0/1',app.ward_count.get())
                    x,y=app.view.pixel(500,500)
                    app.press(SimpleNamespace(x=x,y=y));app.release(SimpleNamespace(x=x,y=y))
                    self.assertIsNone(app.selected_ward)
                    app.ward_phase.set('0–10分钟');app.choose_ward_phase()
                    app.press(SimpleNamespace(x=x,y=y));app.release(SimpleNamespace(x=x,y=y))
                    self.assertEqual(app.selected_ward,'ward-test')
            finally:
                window.destroy()

    def test_builder_rejects_different_maps_before_render_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = [
                {'map_asset_sha256': 'a'*64, 'client_build': 10},
                {'map_asset_sha256': 'b'*64, 'client_build': 10},
                {'current_client_build': 10, 'map_vpk_exact_bytes_compatible': True, 'historical_client_build': 9}]
            with patch('dota2_insights.history.checked_input', side_effect=lambda r,p: Path(p)), \
                    patch('dota2_insights.history.checked_output', side_effect=lambda r,p: Path(p)), \
                    patch('dota2_insights.map_prototype.read_complete', side_effect=calls), \
                    patch('dota2_insights.map_prototype.read_plan', return_value=({'map_build': 9}, {})), \
                    patch('dota2_insights.map_prototype.render_collision') as render:
                with self.assertRaisesRegex(ValueError, 'map_identity_mismatch'):
                    prepare(root/'c', root/'l', root/'r', root/'s', root/'output')
                render.assert_not_called()
                self.assertFalse((root/'output').exists())

    def test_ward_identity_gate_precedes_display_body_access(self):
        with patch('dota2_insights.map_prototype.read_complete', return_value={'ward_match_id': '1234567890', 'files': {}}), \
                patch('dota2_insights.replay_holdout.refuse_locked_match', side_effect=ValueError('locked')), \
                patch('dota2_insights.map_prototype.sha') as body_hash, \
                patch('dota2_insights.map_prototype.load') as body_read:
            with self.assertRaisesRegex(ValueError, 'locked'):
                open_scene(Path('synthetic-package'))
            body_hash.assert_not_called()
            body_read.assert_not_called()

    def test_ward_candidates_keep_raw_coordinates_and_require_finished_source(self):
        import base64
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = json.dumps([{'match_id': '1234567890', 'ward_type': 'observer', 'team': 2,
                'x': 16884, 'y': 16884, 'placed_game_time_s': 30,
                'coordinate_basis': 'gem_cell128_plus_vec_raw_coordinates'}]).encode()
            digest = hashlib.sha256(raw).hexdigest()
            manifest = {'match_id': '1234567890', 'parsed_match_id': '1234567890', 'complete': True,
                'parse_error': None, 'independent_footer_matches_parser': True, 'source_unchanged_stat': True,
                'stop_reason': 'replay_eof', 'ward_count': 1, 'source_sha256': 'a'*64,
                'files': {'wards.json': {'sha256': digest, 'bytes': len(raw)}}}
            snap, receipt = root/'snapshot.json', root/'receipt.json'
            def write():
                snap.write_text(json.dumps({'matches': [{'match_id': '1234567890',
                    'manifest_base64': base64.b64encode(json.dumps(manifest).encode()).decode(),
                    'wards_base64': base64.b64encode(raw).decode()}]}))
                receipt.write_text(json.dumps({'snapshot_sha256': sha(snap), 'matches': [{'match_id': '1234567890',
                    'replay_sha256': 'a'*64, 'wards_sha256': digest}]}))
            write()
            layer = load_ward_candidates(snap, receipt, '1234567890')
            self.assertEqual(layer['points'][0]['raw_world_xy'], [16884,16884])
            self.assertEqual(ward_candidate_xy(layer, layer['points'][0]), (500,500))
            self.assertFalse(layer['map_version_alignment_verified'])
            manifest['stop_reason'] = 'incomplete'
            write()
            with self.assertRaisesRegex(ValueError, 'receipt_binding'):
                load_ward_candidates(snap, receipt, '1234567890')
            with patch('dota2_insights.replay_holdout.refuse_locked_match', side_effect=ValueError('locked')), \
                    patch('dota2_insights.map_prototype.load') as reader:
                with self.assertRaisesRegex(ValueError, 'locked'):
                    load_ward_candidates(snap, receipt, '1234567890')
                reader.assert_not_called()
