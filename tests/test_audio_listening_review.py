"""Generated WAV/JSON only: no device, playback, real labels or network."""
import copy
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import wave

from dota2_insights import audio_listening_review as review


class ListeningReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(bytes(1600))
        self.wav = buffer.getvalue()
        (self.root / 'sample.wav').write_bytes(self.wav)
        self.source = self.root / 'review.json'
        self.value = {'schema_version': 1, 'purpose': review.PURPOSES[0],
                      'prediction_sha256': 'a' * 64, 'match_id': 'synthetic',
                      'casts': [{'clip': 'sample.wav', 'clip_sha256': review.sha(self.wav),
                                 'skill': 'zuus_thundergods_wrath', 'game_time_s': 12}]}
        self.page = self.root / 'page'
        self.labels = self.root / 'labels.json'

    def build(self):
        review.write_json(self.source, self.value)
        return review.build_listening_page(self.source, self.page)

    def notes(self):
        manifest, body = review.read_json(self.page / 'manifest.json')
        return {'kind': 'audio_listening_notes', 'schema_version': 1,
                'manifest_sha256': review.sha(body), 'context_policy': manifest['context_policy'],
                'notes': [{'clip_id': c['clip_id'], 'clip_sha256': c['sha256'],
                           'verdict': 'audible', 'phase': 'cast', 'heard': 'synthetic',
                           'comment': '', 'played': True} for c in manifest['clips']]}

    def submit(self, notes):
        review.write_json(self.labels, notes)
        return review.import_listening_notes(self.page, self.labels)

    def test_package_keeps_exact_wav_source_and_explicit_limitations(self):
        path, count = self.build()
        self.assertEqual((path, count), (self.page, 1))
        self.assertEqual((path / 'clip-001.wav').read_bytes(), self.wav)
        manifest, _ = review.read_json(path / 'manifest.json')
        self.assertEqual(manifest['status'], 'ready')
        self.assertFalse(manifest['training_eligible'])
        self.assertFalse(manifest['accuracy_verified'])
        self.assertEqual(manifest['prediction_sha256'], 'a' * 64)
        self.assertEqual(manifest['review_source_sha256'], review.sha(self.source.read_bytes()))
        html = (path / 'index.html').read_text(encoding='utf-8')
        self.assertIn('audio.preload=\'none\'', html)
        self.assertNotIn('autoplay', html)

    def test_both_supported_source_formats(self):
        self.value['purpose'] = review.PURPOSES[1]
        self.value['clips'] = self.value.pop('casts')
        self.assertEqual(self.build()[1], 1)

    def test_import_is_versioned_and_does_not_modify_predictions_or_package(self):
        self.build()
        source, manifest = self.source.read_bytes(), (self.page / 'manifest.json').read_bytes()
        notes = self.notes()
        first, counts = self.submit(notes)
        second, _ = self.submit(notes)
        self.assertNotEqual(first, second)
        self.assertEqual(counts, {'audible': 1})
        receipt, _ = review.read_json(first / 'notes.json')
        self.assertFalse(receipt['training_eligible'])
        self.assertFalse(receipt['accuracy_verified'])
        self.assertEqual(receipt['notes'], notes['notes'])
        self.assertEqual(self.source.read_bytes(), source)
        self.assertEqual((self.page / 'manifest.json').read_bytes(), manifest)

    def test_wrong_manifest_clip_and_unplayed_verdicts_are_rejected(self):
        self.build()
        notes = self.notes()
        variants = []
        wrong = copy.deepcopy(notes)
        wrong['manifest_sha256'] = 'b' * 64
        variants.append(wrong)
        for key, value in [('clip_id', 'clip-999'), ('clip_sha256', 'b' * 64),
                           ('played', False), ('verdict', 'bad'), ('phase', 'bad'),
                           ('heard', 'x' * 201), ('comment', 'x' * 1001)]:
            wrong = copy.deepcopy(notes)
            wrong['notes'][0][key] = value
            variants.append(wrong)
        for wrong in variants:
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                self.submit(wrong)
        self.assertFalse((self.page / 'notes').exists())

    def test_not_audible_needs_unknown_phase_and_is_not_an_accuracy_label(self):
        self.build()
        notes = self.notes()
        notes['notes'][0]['verdict'] = 'not_audible'
        with self.assertRaises(ValueError):
            self.submit(notes)
        notes['notes'][0]['phase'] = 'unknown'
        output, counts = self.submit(notes)
        self.assertEqual(counts, {'not_audible': 1})
        self.assertFalse(review.read_json(output / 'notes.json')[0]['accuracy_verified'])

    def test_unreviewed_submission_does_not_create_receipt(self):
        self.build()
        notes = self.notes()
        notes['notes'][0].update(verdict='unreviewed', phase='unknown', played=False)
        with self.assertRaisesRegex(ValueError, 'no_reviewed'):
            self.submit(notes)
        self.assertFalse((self.page / 'notes').exists())

    def test_count_and_duplicate_notes_rejected(self):
        self.build()
        notes = self.notes()
        notes['notes'] *= 2
        with self.assertRaisesRegex(ValueError, 'count_mismatch'):
            self.submit(notes)

    def test_changed_clip_and_unready_package_rejected(self):
        self.build()
        notes = self.notes()
        (self.page / 'clip-001.wav').write_bytes(self.wav + b'changed')
        with self.assertRaisesRegex(ValueError, 'hash_mismatch'):
            self.submit(notes)
        (self.page / 'clip-001.wav').write_bytes(self.wav)
        manifest, _ = review.read_json(self.page / 'manifest.json')
        manifest['status'] = 'preparing'
        review.write_json(self.page / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'invalid_listening_package'):
            self.submit(notes)

    def test_bad_source_hash_path_duplicate_and_clock_leave_no_package(self):
        original = copy.deepcopy(self.value)
        variants = []
        for key, value in [('clip', '../sample.wav'), ('clip_sha256', 'b' * 64),
                           ('skill', '</script>'), ('game_time_s', True)]:
            wrong = copy.deepcopy(original)
            wrong['casts'][0][key] = value
            variants.append(wrong)
        wrong = copy.deepcopy(original)
        wrong['casts'] *= 2
        variants.append(wrong)
        for value in variants:
            self.value = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.build()
            self.assertFalse(self.page.exists())

    def test_invalid_wave_and_existing_output_never_overwrite(self):
        (self.root / 'sample.wav').write_bytes(b'not-wave')
        self.value['casts'][0]['clip_sha256'] = review.sha(b'not-wave')
        with self.assertRaises((wave.Error, EOFError, ValueError)):
            self.build()
        self.assertFalse(self.page.exists())
        (self.root / 'sample.wav').write_bytes(self.wav)
        self.value['casts'][0]['clip_sha256'] = review.sha(self.wav)
        self.build()
        before = (self.page / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual((self.page / 'manifest.json').read_bytes(), before)

    def test_boolean_schema_versions_are_not_version_one(self):
        self.value['schema_version'] = True
        with self.assertRaisesRegex(ValueError, 'frozen_post_prediction_review_required'):
            self.build()
        self.assertFalse(self.page.exists())
        self.value['schema_version'] = 1
        self.build()
        notes = self.notes()
        notes['schema_version'] = True
        with self.assertRaisesRegex(ValueError, 'listening_notes_source_mismatch'):
            self.submit(notes)
        manifest, _ = review.read_json(self.page / 'manifest.json')
        manifest['schema_version'] = True
        review.write_json(self.page / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'invalid_listening_package'):
            self.submit(notes)


@unittest.skipUnless(shutil.which('node'), 'Node required for offline page logic')
class ListeningPageLogicTests(unittest.TestCase):
    """Execute actual generated JS in an isolated DOM model, never play audio."""
    def setUp(self):
        fixture = ListeningReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        (fixture.root / 'second.wav').write_bytes(fixture.wav)
        fixture.value['casts'][0]['game_time_s'] = -30
        fixture.value['casts'].append({**fixture.value['casts'][0], 'clip': 'second.wav',
                                      'game_time_s': None})
        fixture.build()
        page = (fixture.page / 'index.html').read_text(encoding='utf-8')
        self.script = re.search(r'<script>(.*?)</script>', page, re.S).group(1)

    def run_page(self, action):
        harness = r'''
const vm=require('node:vm');
const nodes={},elements=[],events={},exports=[],downloads=[],timeouts=[];
function element(tag='div'){
  const e={tag,children:[],events:{},value:'',disabled:false,textContent:'',
    append(child){this.children.push(child);if(this.tag==='select'&&this.children.length===1)this.value=child.value},
    addEventListener(name,handler){this.events[name]=handler},
    click(){if(this.tag==='a')downloads.push(this.download)},
    fire(name){if(!this.disabled){const f=this.events[name]||this['on'+name];if(f)f({target:this})}}
  };elements.push(e);return e;
}
const context=vm.createContext({document:{getElementById(id){return nodes[id]??=element()},createElement:element},
  window:{addEventListener(name,handler){events[name]=handler}},
  Blob:class{constructor(parts){this.parts=parts}},
  URL:{createObjectURL(blob){exports.push(JSON.parse(blob.parts.join('')));return 'synthetic-local-blob'},revokeObjectURL(){}},
  setTimeout(callback){timeouts.push(callback)}});
for(const id of ['save','restore','progress','notice','clips'])nodes[id]=element();
vm.runInContext(SCRIPT,context,{timeout:1000});
const note=()=>vm.runInContext('JSON.parse(JSON.stringify(notes))',context);
const controls=tag=>elements.filter(e=>e.tag===tag);
const choose=(control,value)=>{control.value=value;control.fire('change')};
async function load(value,size=2000){const event={target:{value:'selected',files:[{size,async text(){return JSON.stringify(value)}}]}};await nodes.restore.onchange(event);return event.target.value}
function beforeUnload(){let prevented=false;const event={preventDefault(){prevented=true}};events.beforeunload(event);return prevented}
const valid=()=>({kind:'audio_listening_notes',schema_version:1,
  manifest_sha256:vm.runInContext('data.manifest_sha256',context),
  context_policy:vm.runInContext('policy',context),notes:note().map(n=>({...n,played:true,verdict:'audible',phase:'cast'}))});
(async()=>{const result=await (async()=>{ACTION})();
console.log(JSON.stringify({result,notes:note(),notice:nodes.notice.textContent,progress:nodes.progress.textContent,
 exports,downloads,beforeUnload:beforeUnload()}));})().catch(error=>{console.error(error);process.exitCode=1});
'''
        command = harness.replace('SCRIPT', json.dumps(self.script)).replace('ACTION', action)
        result = subprocess.run([shutil.which('node')], input=command, text=True,
                                encoding='utf-8', capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_initial_controls_do_not_claim_playback_or_review(self):
        result = self.run_page("return {disabled:controls('select').map(e=>e.disabled),audio:controls('audio').map(e=>({preload:e.preload,autoplay:e.autoplay===true})),dirty:beforeUnload()}")
        self.assertEqual(result['result']['disabled'], [True] * 4)
        self.assertEqual(result['result']['audio'], [{'preload': 'none', 'autoplay': False}] * 2)
        self.assertFalse(result['result']['dirty'])
        self.assertTrue(all(n['verdict'] == 'unreviewed' and not n['played'] for n in result['notes']))

    def test_playing_enables_verdict_and_non_audible_resets_phase(self):
        result = self.run_page("""
controls('audio')[0].fire('playing');
choose(controls('select')[0],'audible');choose(controls('select')[1],'impact');
const audible=note()[0];choose(controls('select')[0],'not_audible');
return {audible,phaseDisabled:controls('select')[1].disabled};
""")
        self.assertEqual(result['result']['audible']['phase'], 'impact')
        self.assertEqual(result['notes'][0]['phase'], 'unknown')
        self.assertTrue(result['result']['phaseDisabled'])
        self.assertEqual(result['notes'][0]['verdict'], 'not_audible')
        self.assertIn('1 / 2', result['progress'])

    def test_export_from_actual_controls_imports_into_backend_receipt(self):
        result = self.run_page("""
controls('audio')[0].fire('playing');choose(controls('select')[0],'audible');
choose(controls('select')[1],'sustain');controls('input')[0].value='synthetic';controls('input')[0].fire('input');
controls('textarea')[0].value='<script>text only</script>';controls('textarea')[0].fire('input');
nodes.save.onclick();return null;
""")
        exported, = result['exports']
        output, counts = self.fixture.submit(exported)
        receipt, _ = review.read_json(output / 'notes.json')
        self.assertEqual(counts, {'audible': 1, 'unreviewed': 1})
        self.assertEqual(receipt['notes'][0]['comment'], '<script>text only</script>')
        self.assertEqual(receipt['notes'][0]['phase'], 'sustain')
        self.assertFalse(receipt['accuracy_verified'])
        self.assertEqual(result['downloads'], ['audio-listening-notes.json'])

    def test_restore_uses_identity_not_order_and_round_trips(self):
        result = self.run_page("const value=valid();value.notes.reverse();const cleared=await load(value);nodes.save.onclick();return {cleared,disabled:controls('select').map(e=>e.disabled)}")
        self.assertEqual(result['result']['cleared'], '')
        self.assertEqual([n['clip_id'] for n in result['notes']], ['clip-001', 'clip-002'])
        self.assertEqual(result['result']['disabled'], [False] * 4)
        self.assertEqual(result['exports'][0]['notes'], result['notes'])
        self.assertIn('2 / 2', result['progress'])

    def test_wrong_page_hash_and_duplicate_or_missing_clip_leave_current_notes(self):
        for mutation in ("value.manifest_sha256='b'.repeat(64)",
                         "value.notes[1]={...value.notes[0]}",
                         "value.notes.pop()", "value.notes[1].clip_sha256='b'.repeat(64)"):
            result = self.run_page("const value=valid();" + mutation + ";await load(value);return null")
            self.assertTrue(all(n['verdict'] == 'unreviewed' for n in result['notes']))
            self.assertTrue(result['notice'].startswith('未恢复：'))

    def test_invalid_second_row_does_not_partially_replace_current_notes(self):
        result = self.run_page("const value=valid();value.notes[1].played=false;await load(value);return null")
        self.assertTrue(all(n['verdict'] == 'unreviewed' and not n['played'] for n in result['notes']))
        self.assertTrue(result['notice'].startswith('未恢复：'))

    def test_oversized_file_is_rejected_before_reading(self):
        result = self.run_page("let read=false;await nodes.restore.onchange({target:{value:'selected',files:[{size:1048577,async text(){read=true;return '{}'}}]}});return {read}")
        self.assertFalse(result['result']['read'])
        self.assertTrue(result['notice'].startswith('未恢复：'))

    def test_wrong_phase_lengths_and_schema_are_rejected(self):
        for mutation in ("value.notes[0].verdict='not_audible'",
                         "value.notes[0].heard='x'.repeat(201)",
                         "value.notes[0].comment='x'.repeat(1001)",
                         "value.schema_version=true"):
            result = self.run_page("const value=valid();" + mutation + ";await load(value);return null")
            self.assertTrue(all(n['verdict'] == 'unreviewed' for n in result['notes']))
            self.assertTrue(result['notice'].startswith('未恢复：'))

    def test_corrupt_json_preserves_existing_edits(self):
        result = self.run_page("""
controls('textarea')[0].value='keep this';controls('textarea')[0].fire('input');
await nodes.restore.onchange({target:{value:'selected',files:[{size:5,async text(){return '{bad'}}]}});return null;
""")
        self.assertEqual(result['notes'][0]['comment'], 'keep this')
        self.assertTrue(result['beforeUnload'])

    def test_negative_clock_is_rendered_with_one_sign(self):
        result = self.run_page("return controls('p').map(e=>e.textContent)")
        self.assertTrue(any('-0:30' in line for line in result['result']))
        self.assertFalse(any('-1:-30' in line for line in result['result']))

    def test_older_pending_restore_cannot_overwrite_newer_selection(self):
        result = self.run_page("""
let release;const first=valid(),second=valid();first.notes[0].comment='old';second.notes[0].comment='latest';
const pending=nodes.restore.onchange({target:{value:'first',files:[{size:2000,text(){return new Promise(done=>{release=()=>done(JSON.stringify(first))})}}]}});
await load(second);release();await pending;return null;
""")
        self.assertEqual(result['notes'][0]['comment'], 'latest')

    def test_older_restore_failure_does_not_replace_new_success_notice(self):
        result = self.run_page("""
let reject;const pending=nodes.restore.onchange({target:{value:'first',files:[{size:2000,text(){return new Promise((done,fail)=>{reject=fail})}}]}});
await load(valid());reject(Error('old failure'));await pending;return null;
""")
        self.assertTrue(all(n['verdict'] == 'audible' for n in result['notes']))
        self.assertTrue(result['notice'].startswith('已恢复；'))


if __name__ == '__main__':
    unittest.main()
