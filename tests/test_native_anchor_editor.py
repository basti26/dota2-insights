"""Generated image/points and withdrawn Tk only; no real annotation truth."""
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from dota2_insights.native_anchor_editor import AnchorDraft, AnchorEditor
import test_native_orientation_calibration as fixtures


class AnchorEditorTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.folder=Path(folder.name);self.input=self.folder/'pixel-plan.json'
        orientation,anchors,_=fixtures.OrientationCalibrationTests().fixture(size=(1280,720))
        self.expected={row['id']:row['screen_xy'] for row in anchors}
        self.image=self.folder/'frame.png';Image.new('RGB',(1280,720),(40,50,60)).save(self.image)
        self.payload={'schema_version':1,'kind':'native_anchor_pixel_plan','native_orientation':orientation,
            'native_viewport':[1280,720],
            'image_reference':{'file':'frame.png','sha256':hashlib.sha256(self.image.read_bytes()).hexdigest()},
            'anchors':[{k:copy.deepcopy(v) for k,v in row.items() if k!='screen_xy'} for row in anchors]}
        self.input.write_text(json.dumps(self.payload),encoding='utf-8')

    def complete(self,draft):
        for identity,point in self.expected.items():draft.mark(identity,point)

    def test_new_plan_never_prefills_pixels_and_incomplete_or_unknown_points_cannot_export(self):
        draft=AnchorDraft(self.input)
        self.assertTrue(all(p=={'state':'pending','screen_xy':None} for p in draft.points.values()))
        self.assertRaisesRegex(ValueError,'incomplete',draft.calibration_input)
        self.complete(draft);draft.mark('0',state='unidentifiable')
        self.assertRaisesRegex(ValueError,'incomplete',draft.calibration_input)
        for point in ([-1,20],[1280,20],[20,float('nan')],[True,20]):
            self.assertRaises(ValueError,draft.mark,'0',point)

    def test_partial_draft_restores_with_fixed_plan_roles_and_digest(self):
        draft=AnchorDraft(self.input);draft.mark('0',[100,200]);draft.mark('1',state='unidentifiable')
        output=self.folder/'partial.json';draft.save(output)
        saved=json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(saved['kind'],'native_anchor_pixel_draft')
        self.assertFalse(saved['pixel_annotation_truth_verified'])
        other=AnchorDraft(self.input);other.restore(saved)
        self.assertEqual(other.points,draft.points)
        saved['points']['2']={'state':'marked','screen_xy':[99999,2]}
        before=copy.deepcopy(other.points)
        self.assertRaises(ValueError,other.restore,saved)
        self.assertEqual(other.points,before)
        saved['plan_sha256']='b'*64
        self.assertRaises(ValueError,other.restore,saved)

    def test_complete_export_round_trips_actual_calibration_cli_without_overwrite_or_source_edits(self):
        from tools.calibrate_native_camera import main
        before=self.input.read_bytes();image_sha=hashlib.sha256(self.image.read_bytes()).hexdigest()
        draft=AnchorDraft(self.input);self.complete(draft)
        output=self.folder/'anchors.json';candidate=self.folder/'candidate.json';draft.save(output,complete=True)
        exported=json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual([r['role'] for r in exported['anchors']],[r['role'] for r in self.payload['anchors']])
        self.assertFalse(exported['pixel_annotation_review']['pixel_annotation_truth_verified'])
        with patch('sys.argv',['calibrate_native_camera','--input',str(output),'--output',str(candidate)]), \
                redirect_stdout(io.StringIO()):main()
        result=json.loads(candidate.read_text(encoding='utf-8'))
        self.assertEqual(result['status'],'conditional_candidate')
        self.assertTrue(result['source_image_binding_verified'])
        self.assertFalse(result['pixel_annotation_truth_verified']);self.assertFalse(result['live_usable'])
        self.assertRaises(FileExistsError,draft.save,output,complete=True)
        self.assertEqual(self.input.read_bytes(),before)
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).hexdigest(),image_sha)

    def test_changed_plan_image_or_in_memory_roles_prevent_publication(self):
        draft=AnchorDraft(self.input);self.complete(draft);output=self.folder/'anchors.json'
        draft.plan['anchors'][0]['role']='check'
        self.assertRaisesRegex(ValueError,'modified_in_memory',draft.save,output,complete=True)
        draft.plan['anchors'][0]['role']='fit'
        self.image.write_bytes(self.image.read_bytes()+b'changed')
        self.assertRaises(ValueError,draft.save,output,complete=True)
        self.assertFalse(output.exists())
        self.input.write_bytes(self.input.read_bytes()+b' ')
        self.assertRaisesRegex(ValueError,'plan_changed',draft.save,output)
        self.assertFalse(output.exists())

    def test_position_only_plan_binds_image_and_fits_without_inventing_direction(self):
        from test_native_camera_calibration import NativeCameraCalibrationTests
        from tools.calibrate_native_camera import main
        pose, anchors, _ = NativeCameraCalibrationTests().fixture(size=(1280,720))
        self.payload.pop('native_orientation')
        self.payload['native_position'] = pose
        self.payload['anchors'] = [{k:v for k,v in anchor.items() if k!='screen_xy'} for anchor in anchors]
        self.input.write_text(json.dumps(self.payload),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'point_semantics_required'):
            AnchorDraft(self.input)
        for row in self.payload['anchors']:
            row['point_semantics']='Generated point at the center of the base'
        self.input.write_text(json.dumps(self.payload),encoding='utf-8')
        draft=AnchorDraft(self.input)
        for anchor in anchors:
            draft.mark(anchor['id'],anchor['screen_xy'])
        output=self.folder/'position-anchors.json';candidate=self.folder/'position-candidate.json'
        draft.save(output,complete=True)
        exported=json.loads(output.read_bytes())
        self.assertEqual(exported['kind'],'explicit_native_camera_anchor_input')
        self.assertNotIn('native_orientation',exported)
        self.assertEqual(exported['anchors'][0]['point_semantics'],self.payload['anchors'][0]['point_semantics'])
        with patch('sys.argv',['calibrate_native_camera','--input',str(output),'--output',str(candidate)]), \
                redirect_stdout(io.StringIO()):main()
        result=json.loads(candidate.read_bytes())
        self.assertEqual(result['status'],'conditional_candidate')
        self.assertAlmostEqual(result['pitch_candidate_degrees'],63.217,places=2)
        self.assertTrue(result['source_image_binding_verified'])
        self.assertFalse(result['pixel_annotation_truth_verified'])
        self.assertFalse(result['capture_frame_alignment_verified'])
        self.assertFalse(result['live_usable'])

    def test_supplied_pixel_predictions_missing_height_and_changed_roles_are_not_plans(self):
        for edit in (lambda p:p['anchors'][0].update(screen_xy=[10,10]),
                     lambda p:p['anchors'][0].update(world_xyz=[0,0,None]),
                     lambda p:[r.update(role='fit') for r in p['anchors']]):
            data=copy.deepcopy(self.payload);edit(data)
            self.input.write_text(json.dumps(data),encoding='utf-8')
            self.assertRaises(ValueError,AnchorDraft,self.input)

    def test_hidden_editor_zoom_scroll_click_uses_native_pixels_and_does_not_change_roles(self):
        import tkinter as tk
        root=tk.Tk();root.withdraw()
        try:
            draft=AnchorDraft(self.input);editor=AnchorEditor(root,draft)
            editor.zoom(1.);root.update_idletasks()
            editor.canvas.xview_moveto(.2);editor.canvas.yview_moveto(.1)
            event=SimpleNamespace(x=70,y=80)
            expected=[editor.canvas.canvasx(event.x),editor.canvas.canvasy(event.y)]
            editor.click(event)
            self.assertTrue(all(abs(a-b)<.001 for a,b in zip(draft.points['0']['screen_xy'],expected)))
            before=copy.deepcopy(draft.points)
            editor.zoom(2.);editor.zoom(None)
            self.assertEqual(draft.points,before)
            editor.zoom(2.);root.update_idletasks()
            editor.list.selection_clear(0,'end');editor.list.selection_set(1)
            event=SimpleNamespace(x=100,y=120)
            expected=[(editor.canvas.canvasx(event.x)+.5)/2-.5,
                      (editor.canvas.canvasy(event.y)+.5)/2-.5]
            editor.click(event)
            self.assertTrue(all(abs(a-b)<.001 for a,b in zip(draft.points['1']['screen_xy'],expected)))
            self.assertIn('世界 XYZ',editor.detail.cget('text'))
            self.assertEqual(root.state(),'withdrawn')
            editor.clear('unidentifiable')
            self.assertEqual(draft.points['1'],{'state':'unidentifiable','screen_xy':None})
            self.assertRaisesRegex(ValueError,'incomplete',draft.calibration_input)
        finally:root.destroy()
