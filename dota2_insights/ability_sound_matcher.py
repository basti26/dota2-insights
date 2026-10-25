"""Small experimental audio-template bank. Scores are similarity, not probability."""

from __future__ import annotations

import hashlib
import json
import re
import wave
from pathlib import Path

import numpy as np

RATE = 8000
WINDOW = 9600  # 1.2 seconds, independent of game cooldowns
VERSION = "log_spectrum_temporal_v1"


def active_bank_folder(root):
    root = Path(root)
    pointer = root / "active.json"
    if not pointer.exists():
        return root / "v1"
    if pointer.stat().st_size > 4096:
        raise ValueError("active_sound_bank_pointer_too_large")
    meta = json.loads(pointer.read_text(encoding="utf-8"))
    name = meta.get("folder")
    if meta.get("schema_version") != 1 or not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None:
        raise ValueError("invalid_active_sound_bank")
    folder = (root / name).resolve()
    if folder.parent != root.resolve():
        raise ValueError("active_sound_bank_outside_root")
    manifest = folder / "bank.json"
    if manifest.stat().st_size > 1024**2 or hashlib.sha256(manifest.read_bytes()).hexdigest() != meta.get("manifest_sha256"):
        raise ValueError("active_sound_bank_hash_mismatch")
    return folder


def resample_mono(samples, rate, target_rate=RATE):
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    if rate == target_rate:
        return samples
    # Low-pass the block before decimation; only standard WASAPI rates are
    # expected. FFT resampling avoids treating high frequencies as low ones.
    size = max(1, round(len(samples) * target_rate / rate))
    spectrum = np.fft.rfft(samples)
    result = np.zeros(size // 2 + 1, dtype=np.complex128)
    n = min(len(result), len(spectrum))
    result[:n] = spectrum[:n]
    if size % 2 == 0 and size < len(samples):
        result[-1] *= 2
    elif len(samples) % 2 == 0 and size > len(samples):
        result[len(spectrum) - 1] *= .5
    return (np.fft.irfft(result, n=size) * size / len(samples)).astype(np.float32)


def mono8k(samples, rate):
    return resample_mono(samples, rate, RATE)


def descriptor(samples):
    if len(samples) != WINDOW:
        raise ValueError("invalid_sound_window")
    frames = np.lib.stride_tricks.sliding_window_view(samples, 256)[::160]
    power = np.abs(np.fft.rfft(frames * np.hanning(256), axis=1)) ** 2
    edges = np.unique(np.geomspace(2, 128, 25).astype(int))
    bands = np.stack([np.log1p(power[:, a:b].mean(axis=1) * 1000) for a,b in zip(edges[:-1], edges[1:])], axis=1)
    # Remove stationary spectral background, retain attack/tail structure.
    bands -= bands.mean(axis=0, keepdims=True)
    value = bands.reshape(-1).astype(np.float32)
    norm = float(np.linalg.norm(value))
    return value / norm if norm > 1e-6 else value * 0


class WaveformVerifier:
    """Experimental source corroboration, independent of hero/GSI labels."""

    def __init__(self, folder, templates):
        self.audio = {}
        folder = Path(folder).resolve()
        for template in templates:
            name = template.get("excerpt", "")
            path = (folder / name).resolve()
            if (path.parent != folder or not name.endswith(".wav")
                    or path.stat().st_size > 4 * 1024**2
                    or hashlib.sha256(path.read_bytes()).hexdigest() != template.get("excerpt_sha256")):
                raise ValueError("invalid_waveform_excerpt")
            with wave.open(str(path), "rb") as stream:
                if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getnframes()) != (RATE, 1, 2, WINDOW):
                    raise ValueError("invalid_waveform_format")
                self.audio[template["template_id"]] = np.frombuffer(stream.readframes(WINDOW), dtype="<i2").astype(np.float64) / 32768

    def check(self, samples, template_id):
        query = np.diff(np.asarray(samples, dtype=np.float64))
        source = self.audio[template_id]
        size = 1 << (2 * len(query) - 1).bit_length()
        spectrum = np.fft.rfft(query, size)
        shifts = np.arange(-1600, 1601)
        a0, b0 = np.maximum(shifts, 0), np.maximum(-shifts, 0)
        a1, b1 = np.minimum(len(query), len(query) + shifts), np.minimum(len(query), len(query) - shifts)
        energy_a = np.concatenate(([0.], np.cumsum(query * query)))
        best = {"score": 0., "lag_ms": 0., "pitch_factor": 1.}
        positions = np.arange(WINDOW)
        # Small bounded pitch search tolerates source randomization. This is
        # offline-only until game impact and held-out sound labels are measured.
        for factor in np.linspace(.97, 1.03, 61):
            reference = np.diff(np.interp(positions * factor, positions, source, left=0., right=0.))
            correlation = np.fft.irfft(spectrum * np.conj(np.fft.rfft(reference, size)), size)
            energy_b = np.concatenate(([0.], np.cumsum(reference * reference)))
            denominator = np.sqrt(np.maximum(0., (energy_a[a1]-energy_a[a0]) * (energy_b[b1]-energy_b[b0])))
            scores = np.divide(np.abs(correlation[shifts % size]), denominator,
                               out=np.zeros(len(shifts)), where=denominator > 1e-12)
            index = int(scores.argmax())
            if scores[index] > best["score"]:
                best = {"score": round(min(1., float(scores[index])), 4),
                        "lag_ms": round(float(shifts[index]) * 1000 / RATE, 3),
                        "pitch_factor": round(float(factor), 4)}
        return best


class AbilitySoundMatcher:
    def __init__(self, folder, *, waveform_check=False):
        folder = Path(folder)
        manifest = folder / "bank.json"
        if manifest.stat().st_size > 1024**2:
            raise ValueError("sound_bank_manifest_too_large")
        raw = manifest.read_bytes()
        meta = json.loads(raw)
        if meta.get("feature_version") != VERSION or not 1 <= len(meta["templates"]) <= 512:
            raise ValueError("invalid_sound_bank")
        data = folder / "features.npy"
        if data.stat().st_size > 4 * 1024**2 or hashlib.sha256(data.read_bytes()).hexdigest() != meta["features_sha256"]:
            raise ValueError("sound_bank_integrity_failed")
        self.bank = np.load(data, allow_pickle=False)
        if (self.bank.shape != (len(meta["templates"]), len(descriptor(np.zeros(WINDOW, dtype=np.float32))))
                or self.bank.dtype != np.float32 or not np.isfinite(self.bank).all()):
            raise ValueError("invalid_sound_bank_features")
        self.templates = meta["templates"]
        self.waveform_verifier = WaveformVerifier(folder, self.templates) if waveform_check else None
        self.source = meta.get("source", "recorded_audio_weak_labels")
        self.skill_counts = {}
        for template in self.templates:
            skill = template["skill"]
            self.skill_counts[skill] = self.skill_counts.get(skill, 0) + 1
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.buffer = np.empty(0, dtype=np.float32)
        self.last_emit = {}
        self.last_scan_s = -1.
        self.last_audio = None
        self.scan_number = 0
        self.last_decision = None

    def reset(self):
        self.buffer = np.empty(0, dtype=np.float32)
        self.last_emit.clear()
        self.last_scan_s = -1.
        self.last_audio = None

    def feed(self, samples, *, offset_s):
        self.buffer = np.concatenate((self.buffer, samples))[-WINDOW-1600:]
        if len(self.buffer) < WINDOW or offset_s - self.last_scan_s < .19:
            return None
        self.last_scan_s = offset_s
        self.scan_number += 1
        self.last_decision = {"scan_number": self.scan_number, "audio_offset_s": round(offset_s, 3),
                              "bank_sha256": self.sha256, "accepted": False, "reason": "low_level"}
        if float(np.sqrt(np.mean(self.buffer ** 2))) < 10 ** (-45 / 20):
            return None
        # Search 20 ms alignments in the last 200 ms, so a template is not
        # required to begin exactly at the audio callback boundary.
        shifts = list(range(0, len(self.buffer)-WINDOW+1, 160))
        queries = np.stack([descriptor(self.buffer[s:s+WINDOW]) for s in shifts])
        # This small workload does not need a multithreaded BLAS pool while
        # the game is running. Keep the bounded comparison in one C loop.
        matrix = np.einsum("ij,kj->ik", self.bank, queries, optimize=False)
        scores = matrix.max(axis=1)
        # Variants of one skill compete as one identity, never as runner-up.
        by_skill = {}
        for template, score in zip(self.templates, scores):
            skill = template["skill"]
            if skill not in by_skill or score > by_skill[skill][0]:
                by_skill[skill] = (float(score), template)
        ranked = sorted(by_skill.values(), key=lambda r: -r[0])
        best, template = ranked[0]
        index = self.templates.index(template)
        alignment = shifts[int(matrix[index].argmax())]
        window_end = offset_s - (len(self.buffer)-alignment-WINDOW)/RATE
        second = ranked[1][0] if len(ranked) > 1 else 0.
        # Fixed provisional gates; not calibrated on a held-out test set.
        reason = ("low_similarity" if best < .78 else "small_margin" if best-second < .08 else
                  "repeat_suppressed" if offset_s-self.last_emit.get(template["skill"], -100) < 2 else "candidate")
        self.last_decision.update(skill=template["skill"], similarity=round(best,4),
            runner_up_similarity=round(second,4), window_start_s=round(window_end-WINDOW/RATE,3),
            window_end_s=round(window_end,3), reason=reason, accepted=reason == "candidate")
        if reason != "candidate":
            return None
        verification = None
        if self.waveform_verifier is not None:
            verification = self.waveform_verifier.check(self.buffer[alignment:alignment+WINDOW], template["template_id"])
            self.last_decision["waveform"] = verification
            # Provisional source corroboration gate, not calibrated accuracy.
            if verification["score"] < .30:
                self.last_decision.update(reason="waveform_unconfirmed", accepted=False)
                return None
        self.last_emit[template["skill"]] = offset_s
        self.last_audio = self.buffer[alignment:alignment+WINDOW].copy()
        return {"skill": template["skill"], "hero_id": template["hero_id"],
                "similarity": round(best, 4), "runner_up_similarity": round(second, 4),
                "template_id": template["template_id"], "bank_sha256": self.sha256,
                "template_label_basis": template.get("label_basis"),
                "event_phase": template.get("event_phase"),
                "waveform": verification,
                "window_start_s": round(window_end - WINDOW / RATE, 3), "window_end_s": round(window_end, 3),
                "label_status": "candidate_from_weakly_labeled_template_not_verified_cast",
                "feature_version": VERSION}
