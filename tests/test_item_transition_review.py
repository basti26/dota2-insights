from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
from tests.test_equipment_continuity import row
from dota2_insights.item_transition_review import Transitions,prepare

CATALOGUE={'blade_mail':{'cost':2300},'blink':{'cost':2250},'branches':{'cost':50}}


class ItemTransitionTrackerTests(unittest.TestCase):
    def feed(self,*rows,limit=24):
        t=Transitions(CATALOGUE,limit=limit)
        for i,r in enumerate(rows,1):t.add(r,{'line':i,'line_sha256':'a'*64})
        return t

    def test_same_changed_and_empty_are_separate_recorded_groups(self):
        rows=[row(0),row(1),row(2,item='blink'),row(3,status='empty')]
        t=self.feed(*rows)
        self.assertEqual([c['kind'] for c in t.cases],['recorded_same_item','recorded_item_changed','recorded_empty'])

    def test_unknown_missing_duplicate_and_time_gap_break_references(self):
        for middle in (row(1,status='unknown'),row(1,status='empty')):
            t=self.feed(row(0),middle,row(2,item='blink'))
            self.assertFalse(any(c['kind']=='recorded_item_changed' for c in t.cases))
        for mode in ('missing','duplicate'):
            middle=row(1);s=middle['attempt']['hero']['slots']
            middle['attempt']['hero']['slots']=[] if mode=='missing' else s*2
            self.assertEqual(self.feed(row(0),middle,row(2,item='blink')).cases,[])
        self.assertEqual(self.feed(row(0),row(181,item='blink')).cases,[])

    def test_distinct_pair_first_selected_without_image_score(self):
        t=self.feed(row(0),row(1),row(2),row(3,item='blink'),row(4,item='blade_mail'),limit=1)
        self.assertEqual(t.counts['recorded_same_item_pairs'],2)
        self.assertEqual(sum(c['kind']=='recorded_same_item' for c in t.cases),1)
        self.assertEqual(t.cases[0]['before']['evidence']['line'],1)
        self.assertEqual(t.counts['recorded_item_changed_omitted_by_limit'],1)

    def test_hero_and_slot_do_not_share_history_and_low_cost_not_reference(self):
        self.assertEqual(self.feed(row(0),row(1,hero='lion',item='blink')).cases,[])
        self.assertEqual(self.feed(row(0),row(1,slot='main_2',item='blink')).cases,[])
        self.assertEqual(self.feed(row(0,item='branches'),row(1,item='blink')).cases,[])


class ItemTransitionSourceTests(unittest.TestCase):
    def setUp(self):
        from tests.test_observation_handoff import ObservationHandoffTests
        self.f=ObservationHandoffTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup);self.output=Path(temp.name)/'review'
        self.roots={'helper_history':self.f.root}
        rows=[]
        for i,item in enumerate(('blade_mail','blink'),1):
            r=deepcopy(self.f.row);r['captured_at_utc']=r['attempt']['captured_at_utc']=f'2026-10-26T00:00:0{i-1}Z'
            image=self.f.source.parent/f'hud-{i:06d}.png';im=Image.new('RGB',(80,40),'red');im.paste('green',(10,10,34,30));im.save(image)
            r.update(hud_image=image.name,hud_image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                     frame_geometry={'width':80,'height':40,'origin_left':100,'origin_top':200,'client_width':300,'client_height':300})
            r['attempt']['hero']['slots']=[{'slot_id':'main_1','status':'known','item_id':item,'rect':[110,210,24,20]}]
            rows.append(r)
        self.f.write_payload(b''.join((json.dumps(r)+'\n').encode() for r in rows))
        self.f.manifest['payload']['rows']=2;self.f.manifest['accepted_observations_at_cutoff']=2;self.f.write_manifest()
        from dota2_insights.observation_handoff import verify
        _,pid=verify(self.f.root,self.f.session)
        self.entry={'root_key':'helper_history','source':f'matches/sessions/{self.f.session}/observation-handoff.json','package_id':pid}

    def test_original_rows_and_exact_geometry_crops_are_bound(self):
        r=prepare(self.entry,self.roots,CATALOGUE,self.output)
        self.assertEqual(len(r['cases']),1)
        self.assertEqual(r['cases'][0]['kind'],'recorded_item_changed')
        self.assertEqual(len(r['entries']),2)
        for e in r['entries']:
            self.assertEqual(e['crop_rect'],[10,10,24,20])
            self.assertEqual(e['crop_sha256'],hashlib.sha256((self.output/e['image']).read_bytes()).hexdigest())
        self.assertFalse(r['accuracy_verified'])

    def test_changed_image_is_unavailable_not_silently_matching(self):
        (self.f.source.parent/'hud-000002.png').write_bytes(b'changed')
        r=prepare(self.entry,self.roots,CATALOGUE,self.output)
        self.assertIsNone(r['cases'][0]['similarity'])
        self.assertEqual(sum(e['status']=='unavailable' for e in r['entries']),1)

    def test_source_change_during_cropping_cannot_publish_success(self):
        from dota2_insights.item_review_evidence import slot_crop
        changed=False
        def mutate(*args):
            nonlocal changed
            result=slot_crop(*args)
            if not changed:self.f.source.write_bytes(self.f.source.read_bytes()+b'\n');changed=True
            return result
        with patch('dota2_insights.item_transition_review.slot_crop',side_effect=mutate):
            with self.assertRaises(ValueError):prepare(self.entry,self.roots,CATALOGUE,self.output)
        self.assertFalse((self.output/'evidence.json').exists())
