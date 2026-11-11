"""Independent protocol fixtures; no helper imports or user records."""
import copy
import unittest
from dota2_insights.laning_ui_review import LaneWidgetSummary, summary_text
from dota2_insights.draft_diagnostics import DraftDiagnostics


def row():
    return {'record_version':1, 'session_id':'a'*32, 'revision':2, 'phase':'strategy',
        'advice_stage':'waiting', 'visible':True, 'expanded':True, 'reason':'shown', 'display_mode':'laning',
        'observed_at_utc':'2026-11-11T00:00:00Z', 'written_at_utc':'2026-11-11T00:00:01Z',
        'laning': {'selected_hero_ids':['npc_dota_hero_lina'], 'tip_hero_ids':['npc_dota_hero_lina'],
            'advice_status':'ready', 'package_sha256':'1'*64, 'rendered_text_sha256':'2'*64,
            'viewport_y':[0.,.5], 'meaning':'widget_state_not_OS_pixels_or_user_reading'}}


class LaneReviewTests(unittest.TestCase):
    def test_allied_metadata_is_separate_and_legacy_absence_stays_unknown(self):
        legacy=LaneWidgetSummary();legacy.add(row(),{'line':1})
        self.assertIsNone(legacy.result()['declared_selected_ally_hero_ids'])
        data=row();data['laning'].update(selected_hero_ids=[],tip_hero_ids=[],
            selected_ally_hero_ids=['npc_dota_hero_lion'],tip_ally_hero_ids=['npc_dota_hero_lion'])
        summary=LaneWidgetSummary();summary.add(data,{'line':1})
        self.assertEqual(summary.result()['recorded_state_counts'],{'ready':1})
        self.assertEqual(summary.result()['declared_selected_ally_hero_ids'],['npc_dota_hero_lion'])
        bad=copy.deepcopy(data);bad['laning']['selected_hero_ids']=['npc_dota_hero_lion']
        summary.add(bad,{'line':2})
        self.assertEqual(summary.result()['invalid_rows'],1)

    def test_scroll_changes_are_not_new_tip_texts_or_read_counts(self):
        diag=DraftDiagnostics()
        one=row(); two=copy.deepcopy(one);two['laning']['viewport_y']=[.5,1.]
        for line,r in enumerate([one,two],1):
            diag.add('panel-visibility.jsonl',line,r,{'line_sha256':'3'*64})
        result=diag.result()['laning_widget_metadata']
        self.assertEqual(result['recorded_state_counts'],{'ready':2})
        self.assertEqual(result['unique_ready_text_states'],1)
        self.assertEqual(result['declared_tip_hero_ids'],['npc_dota_hero_lina'])
        self.assertFalse(result['user_reading_verified'])
        self.assertIn('不是阅读次数',summary_text(result))

    def test_old_or_missing_extension_remains_unknown(self):
        summary=LaneWidgetSummary();legacy=row();legacy.pop('laning')
        summary.add(legacy,{'line':1})
        result=summary.result()
        self.assertEqual(result['status'],'not_recorded_unknown')
        self.assertIsNone(result['unique_ready_text_states'])
        self.assertIn('不能解释为零次使用',summary_text(result))

    def test_bad_private_unready_and_invisible_metadata_never_becomes_ready(self):
        bad=[]
        for change in [{'chat':'private'}, {'advice_status':'loading'}, {'viewport_y':[float('nan'),1.]},
                       {'tip_hero_ids':['npc_dota_hero_axe']}]:
            item=row();item['laning'].update(change);bad.append(item)
        for change in [{'expanded':False},{'display_mode':'bp'},{'session_id':'other'}]:
            item=row();item.update(change);bad.append(item)
        summary=LaneWidgetSummary()
        for item in bad:summary.add(item,{'line':1})
        result=summary.result()
        self.assertEqual(result['invalid_rows'],len(bad))
        self.assertEqual(result['unique_ready_text_states'],0)
        self.assertEqual(result['declared_package_sha256'],[])
        self.assertNotIn('private',str(result))

    def test_loading_failure_and_no_selection_are_distinct(self):
        summary=LaneWidgetSummary()
        for status in ['loading','unavailable','not_selected']:
            item=row();item['laning'].update(advice_status=status,package_sha256=None,tip_hero_ids=[])
            if status=='not_selected':item['laning']['selected_hero_ids']=[]
            summary.add(item,{'line':1})
        self.assertEqual(summary.result()['recorded_state_counts'],{'loading':1,'unavailable':1,'not_selected':1})
