"""Offline multiscale sound proposals in mixtures; no confirmed-cast output."""
from collections import Counter
import hashlib
from pathlib import Path

import numpy as np

from .ability_sound_matcher import AbilitySoundMatcher, WaveformVerifier, RATE

VERSION = 'mixture_local_spectrum_v3'
# Fixed research scope, never derived from the current roster or GSI.
TARGETS = ('zuus_thundergods_wrath', 'tidehunter_ravage',
           'magnataur_reverse_polarity', 'enigma_black_hole')
SIZES = (3200, 6400, 9600)


def spectral_bands(samples):
    frames = np.lib.stride_tricks.sliding_window_view(samples, 256)[::160]
    power = np.abs(np.fft.rfft(frames * np.hanning(256), axis=1)) ** 2
    edges = np.unique(np.geomspace(2, 128, 33).astype(int))
    return np.stack([power[:, a:b].mean(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1)


def feature_batch(bands):
    """Retain frequency contrast and changing energy, discount stationary beds."""
    # A local floor is an attenuation heuristic, not source separation.
    floor = np.quantile(bands, .2, axis=1, keepdims=True)
    residual = np.maximum(0., bands - .7 * floor)
    scale = np.maximum(np.mean(bands, axis=(1,2), keepdims=True), 1e-12)
    value = np.log1p(residual / scale * 20)
    value -= np.median(value, axis=2, keepdims=True)
    # Keep positive narrow-band evidence rather than signed common attacks.
    value = np.maximum(0., value)
    norms = np.sqrt(np.sum(value * value, axis=2, keepdims=True))
    value = np.divide(value, norms, out=np.zeros_like(value), where=norms > 1e-8)
    value = value.reshape(len(value),-1).astype(np.float32)
    norms = np.sqrt(np.sum(value*value,axis=1,keepdims=True))
    return np.divide(value,norms,out=np.zeros_like(value),where=norms>1e-8)


def mixture_features(samples):
    return feature_batch(spectral_bands(samples)[None,:,:])[0]


class MixtureSoundMatcher:
    """Bounded offline experiment; scores are similarities, not probabilities."""
    def __init__(self, folder):
        loader = AbilitySoundMatcher(folder)
        self.templates = [t for t in loader.templates if t['skill'] in TARGETS]
        self.background_templates = [t for t in loader.templates if t['skill'] not in TARGETS]
        if set(t['skill'] for t in self.templates) != set(TARGETS):
            raise ValueError('key_ultimate_source_coverage_incomplete')
        self.sha256 = loader.sha256
        self.skill_counts = dict(Counter(t['skill'] for t in self.templates))
        verifier = WaveformVerifier(folder, self.templates + self.background_templates)
        self.banks = {}
        self.background_banks = {}
        for size in SIZES:
            rows = []
            for template in self.templates + self.background_templates:
                pcm = verifier.audio[template['template_id']]
                shifts = range(0, len(pcm)-size+1, 160)
                start = max(shifts, key=lambda s: float(np.dot(pcm[s:s+size], pcm[s:s+size])))
                rows.append(mixture_features(pcm[start:start+size]))
            self.banks[size] = np.stack(rows[:len(self.templates)])
            self.background_banks[size] = np.stack(rows[len(self.templates):])
        self.policy = {'feature_version':VERSION,'targets':list(TARGETS),
            'scales_s':[s/RATE for s in SIZES], 'scale_gate':.70,
            'mean_gate':.72,'margin_gate':.06,'min_scales':2,
            'threshold_status':'provisional_not_calibrated',
            'background_skills':sorted(set(t['skill'] for t in self.background_templates)),
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        self.buffer = np.empty(0, dtype=np.float32)
        self.last_scan_s = -1.
        self.scan_number = 0
        self.last_decision = None
        self.last_audio = None
        self.last_emit = {}

    def reset(self):
        self.buffer = np.empty(0, dtype=np.float32)
        self.last_scan_s = -1.
        self.last_emit.clear()
        self.last_audio = None

    def feed(self, samples, *, offset_s):
        self.buffer = np.concatenate((self.buffer, samples))[-11200:]
        if len(self.buffer) < 9600 or offset_s-self.last_scan_s < .19:
            return None
        self.last_scan_s = offset_s
        self.scan_number += 1
        self.last_decision = {'scan_number':self.scan_number,'audio_offset_s':round(offset_s,3),
            'bank_sha256':self.sha256,'feature_version':VERSION,'accepted':False,'reason':'low_level'}
        if float(np.sqrt(np.mean(self.buffer**2))) < 10**(-60/20):
            return None
        per_skill = {}
        bands = spectral_bands(self.buffer)
        query_banks = {}
        for size in SIZES:
            shifts = list(range(0,len(self.buffer)-size+1,160))
            frames = (size-256)//160+1
            windows = np.lib.stride_tricks.sliding_window_view(bands,frames,axis=0).transpose(0,2,1)
            queries = feature_batch(windows[:len(shifts)])
            query_banks[size] = queries
            scores = np.einsum('ij,kj->ik', self.banks[size], queries, optimize=False)
            for skill in TARGETS:
                ids = [i for i,t in enumerate(self.templates) if t['skill']==skill]
                i,j = np.unravel_index(int(scores[ids].argmax()), scores[ids].shape)
                index = ids[i]
                end = offset_s-(len(self.buffer)-shifts[j]-size)/RATE
                per_skill.setdefault(skill,[]).append({'score':float(scores[index,j]),
                    'seconds':size/RATE,'window_end_s':end,
                    'template_id':self.templates[index]['template_id'], 'template_index':index})
        # Agreement requires scale windows to end near the same event, not
        # unrelated maxima spread across this buffer.
        ranked = []
        for skill, scales in per_skill.items():
            best_pair = max(((a,b) for a in scales for b in scales if a is not b),
                key=lambda pair: (sum(x['score'] for x in pair)/2
                    if abs(pair[0]['window_end_s']-pair[1]['window_end_s']) <= .45 else -1))
            aligned = abs(best_pair[0]['window_end_s']-best_pair[1]['window_end_s']) <= .45
            ranked.append((sum(x['score'] for x in best_pair)/2 if aligned else 0.,skill,best_pair,scales))
        ranked.sort(key=lambda r:-r[0])
        score,skill,pair,scales = ranked[0]
        second = ranked[1][0]
        # All remaining source skills act as competitors. Evaluate only the
        # winning target's two windows, avoiding a full-bank sliding search.
        # This includes ordinary spells of the same hero: correct hero alone
        # does not establish that the ultimate was used.
        background_scores = np.zeros(len(self.background_templates),dtype=np.float32)
        for scale in pair:
            size = round(scale['seconds']*RATE)
            start_index = round(len(self.buffer)-(offset_s-scale['window_end_s'])*RATE)-size
            query = query_banks[size][round(start_index/160)]
            background_scores += np.einsum('ij,j->i',self.background_banks[size],query,optimize=False)/2
        bg_index = int(background_scores.argmax())
        background_score = float(background_scores[bg_index])
        second = max(second, background_score)
        reason = ('scale_disagreement' if min(x['score'] for x in pair)<.70 or score<.72
                  else 'background_competitor' if score-background_score<.06
                  else 'small_margin' if score-second<.06 else
                  'repeat_suppressed' if offset_s-self.last_emit.get(skill,-100)<2 else 'candidate')
        evidence = [{k:round(v,4) if isinstance(v,float) else v for k,v in s.items() if k!='template_index'} for s in scales]
        end = max(s['window_end_s'] for s in pair)
        start = min(s['window_end_s']-s['seconds'] for s in pair)
        self.last_decision.update(skill=skill,similarity=round(score,4),runner_up_similarity=round(second,4),
            scales=evidence,window_start_s=round(start,3),window_end_s=round(end,3),
            background_skill=self.background_templates[bg_index]['skill'],background_similarity=round(background_score,4),
            reason=reason,accepted=reason=='candidate')
        if reason!='candidate':
            return None
        self.last_emit[skill] = offset_s
        # Exact clip spans the union of the two agreeing scales.
        first = max(0,round(len(self.buffer)-(offset_s-start)*RATE))
        last = min(len(self.buffer),round(len(self.buffer)-(offset_s-end)*RATE))
        self.last_audio = self.buffer[first:last].copy()
        template = self.templates[max(pair,key=lambda s:s['score'])['template_index']]
        return {k:v for k,v in self.last_decision.items() if k not in ('accepted','reason','scan_number')} | {
            'hero_id':template['hero_id'],'template_id':template['template_id'],
            'event_phase':template.get('event_phase'),
            'label_status':'audio_only_multiscale_candidate_not_verified_cast'}
