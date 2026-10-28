"""Read installed Valve sound events/assets into a new Insights-only offline bank.

Uses the existing Source 2 Viewer CLI for decompilation, not a new sound parser.
Event namespaces require an explicit curated JSON mapping. Output stays local;
the tool has no activation option and never updates a runtime pointer.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.valve_resources import read_vpk_directory, _read_resource, directory_bytes
from dota2_insights.ability_sound_matcher import RATE, WINDOW, VERSION, descriptor, mono8k


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(command, log):
    result = subprocess.run([str(s) for s in command], capture_output=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log.write_bytes(result.stdout + b"\n" + result.stderr)
    if result.returncode:
        raise RuntimeError("decoder_or_audio_conversion_failed: " + log.name)


def event_blocks(text):
    # Source2Viewer 20's exported KV3 text uses a tab per nesting level.
    # Match top-level event blocks only; never interpret KV3 as Python/code.
    matches = list(re.finditer(r"(?ms)^\t([^\s=]+)\s*=\s*\n\t\{(.*?)^\t\}", text))
    if not matches:
        raise ValueError("unsupported_sound_event_export_format")
    result = {}
    for match in matches:
        if match[1].casefold() in {key.casefold() for key in result}:
            raise ValueError('duplicate_sound_event_name')
        result[match[1]] = match[2]
    return result


def number(body, key, default=0.):
    m = re.search(r"(?m)^\t\t"+re.escape(key)+r"\s*=\s*(-?[0-9.]+)\s*$", body)
    return float(m[1]) if m else default


def phase(name):
    n = name.casefold()
    for value, words in (("end", ("end", "stop", "destroy", "revert", "victory")),
                         ("impact", ("impact", "damage", "explode", "crash", "stun", "target")),
                         ("cast", ("cast", "start", "begin", "spawn", "precast", "anim", "channel")),
                         ("loop", ("loop", "aura", "tick"))):
        if any(w in n.rsplit(".", 1)[-1] for w in words):
            return value
    return "ability_related"


def template_jobs(rows):
    """Spend a small class budget on distinct stages, not alphabetic layers.

    Sources often declare many impact variants. Taking the first four base
    clips previously exhausted the budget before transient/pitch/stage views.
    This schedule uses only source event metadata; no test order or GSI.
    """
    if not rows:
        return []
    jobs = [("base", [rows[0]]), ("transient", [rows[0]])]
    first_phase = rows[0]["phase_hint"]
    # Reserve coverage for another stage before more variants of stage one.
    for phase in ("cast", "ability_related", "impact", "loop", "end"):
        if phase == first_phase:
            continue
        selected = next((row for row in rows if row["phase_hint"] == phase), None)
        if selected is not None:
            jobs.append(("base", [selected]))
    jobs.extend((variant, rows) for variant in ("base", "transient", "pitch_low", "pitch_high"))
    return jobs


def verify_base_resources(meta, directory, directory_sha):
    """A changed VPK directory never excuses changed sound/event bytes."""
    raw = directory_bytes(directory)
    if digest(raw) != directory_sha:
        raise ValueError('current_base_directory_changed')
    index, embedded = read_vpk_directory(raw)
    expected = {}
    for template in meta['templates']:
        for key in ('resource','event_resource'):
            name, fingerprint = template[key], template[key+'_sha256']
            if name in expected and expected[name] != fingerprint:
                raise ValueError('base_resource_hash_conflict')
            expected[name] = fingerprint
    if len(expected) > 1024:
        raise ValueError('base_resource_count_limit')
    total = 0
    for name, fingerprint in expected.items():
        if name not in index:
            raise ValueError('base_resource_missing_in_current_game')
        content, _ = _read_resource(directory,index[name],embedded,8*1024**2)
        total += len(content)
        if total > 256*1024**2:
            raise ValueError('base_resource_byte_limit')
        if digest(content) != fingerprint:
            raise ValueError('base_resource_changed_in_current_game')
    if directory_bytes(directory) != raw:
        raise ValueError('current_base_directory_changed')
    return {'prior_directory_sha256':meta.get('directory_vpk_sha256'),
        'current_directory_sha256':directory_sha, 'checked_resource_sha256':expected,
        'resources_checked':len(expected), 'resource_bytes_read':total,
        'basis':'compiled_sound_and_event_bytes_equal_not_patch_or_audibility_certification'}


def load_base_bank(folder, output, directory_sha, new_skills, *, directory=None):
    """Copy a fixed original-asset bank; no active pointer is read or written."""
    from dota2_insights.ability_sound_matcher import AbilitySoundMatcher, WaveformVerifier
    folder, output = Path(folder).resolve(), Path(output).resolve()
    if output.is_relative_to(folder) or folder.is_relative_to(output):
        raise ValueError('base_bank_and_output_must_be_separate')
    manifest = folder/'bank.json'
    if manifest.stat().st_size > 1024**2:
        raise ValueError('base_bank_manifest_budget')
    raw = manifest.read_bytes()
    meta = json.loads(raw)
    if meta.get('source') != 'installed_Valve_original_sound_assets':
        raise ValueError('base_bank_source_conflict')
    source_revalidation = None
    if directory is not None:
        source_revalidation = verify_base_resources(meta,directory,directory_sha)
    elif meta.get('directory_vpk_sha256') != directory_sha:
        raise ValueError('base_bank_source_or_directory_conflict')
    matcher = AbilitySoundMatcher(folder)
    if set(matcher.skill_counts) & set(new_skills):
        raise ValueError('extension_skill_already_in_base')
    if [t['template_id'] for t in matcher.templates] != [f'template_{n+1:03d}' for n in range(len(matcher.templates))]:
        raise ValueError('noncanonical_base_template_identity')
    WaveformVerifier(folder, matcher.templates)
    hashes = {'bank.json':digest(raw), 'features.npy':digest((folder/'features.npy').read_bytes())}
    if matcher.sha256 != hashes['bank.json'] or hashes['features.npy'] != meta['features_sha256']:
        raise ValueError('base_bank_changed_while_loading')
    for template in matcher.templates:
        name = template['excerpt']
        content = (folder/name).read_bytes()
        if digest(content) != template['excerpt_sha256']:
            raise ValueError('base_excerpt_changed')
        (output/name).write_bytes(content)
        hashes[name] = template['excerpt_sha256']
    if digest(manifest.read_bytes()) != hashes['bank.json']:
        raise ValueError('base_manifest_changed')
    return list(matcher.templates), list(matcher.bank.copy()), hashes, source_revalidation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vpk", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True, help="already exported hero .vsndevts folder")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapping",type=Path,required=True)
    parser.add_argument("--templates-per-skill",type=int,default=12)
    parser.add_argument('--base-bank',type=Path,help='fixed official bank to extend without replacing existing skills')
    parser.add_argument("--reuse-bank",type=Path,help="reuse hash-verified PCM for unchanged original resources")
    args = parser.parse_args()
    args.vpk, args.decoder, args.ffmpeg = [p.resolve() for p in (args.vpk, args.decoder, args.ffmpeg)]
    from dota2_insights.history import checked_output
    args.output = checked_output(ROOT, args.output)
    if not 1<=args.templates_per_skill<=12:raise ValueError("invalid_templates_per_skill")
    mapping_path = args.mapping
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    if (type(mapping.get('schema_version')) is not int or mapping['schema_version'] != 1 or not isinstance(mapping.get('skills'),list)
            or not 1 <= len(mapping['skills']) <= 128):
        raise ValueError('sound_mapping_invalid')
    skills = [row.get('skill') for row in mapping['skills']]
    if len(set(skills)) != len(skills) or any(not isinstance(s,str) or not re.fullmatch('[a-z0-9_]{1,100}',s) for s in skills):
        raise ValueError('sound_mapping_skills_invalid')
    for spec in mapping['skills']:
        if (any(not isinstance(spec.get(k),str) or not re.fullmatch('[a-z0-9_]{1,80}',spec[k])
                for k in ('hero', 'file_hero') if k == 'hero' or k in spec)
                or not isinstance(spec.get('events'),list) or not 1 <= len(spec['events']) <= 32
                or any(not isinstance(e,str) or not re.fullmatch('[A-Za-z0-9_.]{1,160}',e) for e in spec['events'])):
            raise ValueError('sound_mapping_namespace_invalid')
    source_directory = directory_bytes(args.vpk)
    index, embedded = read_vpk_directory(source_directory)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output/"compiled").mkdir(); (output/"decoded").mkdir(); (output/"pcm").mkdir(); (output/"logs").mkdir()
    base_templates, base_features, base_hashes, base_revalidation = [], [], {}, None
    if args.base_bank:
        base_templates, base_features, base_hashes, base_revalidation = load_base_bank(
            args.base_bank, output, digest(source_directory), skills, directory=args.vpk)
    all_events, event_sources = {}, {}
    for filename in sorted(args.events.rglob("*.vsndevts")):
        blocks = event_blocks(filename.read_text(encoding="utf-8-sig"))
        for name, body in blocks.items():
            if name.casefold() in all_events:
                raise ValueError("ambiguous_sound_event_name: "+name)
            all_events[name.casefold()] = body
            event_sources[name.casefold()] = filename
    assets = {}
    reuse={}
    if args.reuse_bank:
        prior=json.loads((args.reuse_bank/'coverage.json').read_text(encoding='utf-8'))
        for r in prior['associations']:
            if r.get('status')=='available_for_bank':
                p=(args.reuse_bank/r['pcm']['file']).resolve()
                if p.is_relative_to(args.reuse_bank.resolve()) and digest(p.read_bytes())==r['pcm']['sha256']:
                    reuse[(r['resource'],r['resource_sha256'])]=p
    associations, unresolved = [], []
    for spec in mapping["skills"]:
        filename = args.events / ("game_sounds_"+spec.get("file_hero",spec["hero"])+".vsndevts")
        blocks = event_blocks(filename.read_text(encoding="utf-8-sig"))
        selected = [name for name in blocks if any(name.casefold().startswith(p.casefold()) for p in spec["events"])]
        if not selected:
            raise ValueError("missing_skill_event_namespace: "+spec["skill"])
        for selected_event in selected:
            pending, visited = [selected_event], set()
            while pending:
                name = pending.pop()
                if name.casefold() in visited:
                    continue
                visited.add(name.casefold())
                body = all_events.get(name.casefold())
                if body is None:
                    unresolved.append({"skill":spec["skill"],"event":name,"reason":"layer_not_in_exported_hero_events"})
                    continue
                source_event = event_sources[name.casefold()]
                event_resource = "soundevents/game_sounds_heroes/" + source_event.name + "_c"
                event_entry = index.get(event_resource)
                if event_entry is None:
                    raise ValueError("sound_event_resource_not_in_vpk")
                event_bytes,_ = _read_resource(args.vpk,event_entry,embedded,4*1024**2)
                pitch = number(body,"pitch",1.)
                for resource in sorted(set(re.findall(r'"(sounds?/[^"\r\n]+\.vsnd)"',body))):
                    compiled = resource + "_c"
                    row = {"skill":spec["skill"],"hero_id":"npc_dota_hero_"+spec["hero"],
                        "selected_event":selected_event,"event":name,"phase_hint":phase(name),
                        "phase_basis":"event_name_hint_not_engine_cast_time",
                        "resource":compiled,"event_resource":event_resource,"event_resource_sha256":digest(event_bytes),
                        "event_export_sha256":digest(source_event.read_bytes()),"pitch":pitch,
                        "pitch_rand_min":number(body,"pitch_rand_min"),"pitch_rand_max":number(body,"pitch_rand_max"),
                        "volume":number(body,"volume",1.)}
                    if any(s in resource for s in ("/shared/","/physics/","/foley/","/vo/")):
                        row["status"] = "excluded_generic_or_voice_reference"
                    elif compiled not in index:
                        row["status"] = "resource_missing"
                    else:
                        if compiled not in assets:
                            encoded,_ = _read_resource(args.vpk,index[compiled],embedded,8*1024**2)
                            if sum(a["bytes"] for a in assets.values())+len(encoded)>128*1024**2:
                                raise ValueError("compiled_audio_budget_exceeded")
                            identity = f"resource_{len(assets)+1:04d}"
                            cached=reuse.get((compiled,digest(encoded)))
                            if cached:
                                (output/'pcm'/(identity+'.wav')).write_bytes(cached.read_bytes())
                            else:
                                (output/"compiled"/(identity+".vsnd_c")).write_bytes(encoded)
                            assets[compiled] = {"id":identity,"resource_sha256":digest(encoded),
                                "crc32":index[compiled].crc32,"bytes":len(encoded)}
                        row.update(status="pending_decode",**assets[compiled])
                    associations.append(row)
                for layer in re.finditer(r'(?m)^\t\t(soundevent_layer\d+)\s*=\s*"([^"\r\n]+)"',body):
                    if number(body,layer[1]+"_on",1.) != 0:
                        pending.append(layer[2])
    run([args.decoder,"-i",output/"compiled","--recursive","--game",args.vpk.parent/"gameinfo.gi",
         "-o",output/"decoded","--decompile","--threads","1"],output/"logs/decode.txt")
    pcm, pcm_sources, failures = {}, {}, []
    for resource, asset in assets.items():
        identity = asset["id"]
        target = output/"pcm"/(identity+".wav")
        if not target.exists():
            files = [p for p in (output/"decoded").rglob(identity+".*") if p.suffix.lower() in {".wav",".mp3"}]
            if len(files) != 1:
                failures.append({"resource":resource,"reason":"not_one_decoded_audio_file"})
                continue
            run([args.ffmpeg,"-v","error","-nostdin","-i",files[0],"-ac","1","-ar",str(RATE),
                 "-threads","1","-c:a","pcm_s16le",target],output/"logs"/(identity+".txt"))
        with wave.open(str(target),"rb") as w:
            if w.getnchannels()!=1 or w.getframerate()!=RATE or w.getsampwidth()!=2 or w.getnframes()>RATE*60:
                failures.append({"resource":resource,"reason":"unsupported_pcm_duration_or_format"})
                continue
            value = np.frombuffer(w.readframes(w.getnframes()),dtype="<i2").astype(np.float32)/32768
        if len(value)<RATE*.25:
            failures.append({"resource":resource,"reason":"short_component_not_distinctive_alone"})
            continue
        pcm[resource] = value
        pcm_sources[resource] = {"file":str(target.relative_to(output)),"sha256":digest(target.read_bytes()),
                                 "pcm_sha256":digest(value.tobytes()),"seconds":len(value)/RATE}
    owners = defaultdict(set)
    for template in base_templates:
        if template.get('source_pcm_sha256'):
            owners[template['source_pcm_sha256']].add(template['skill'])
    for row in associations:
        if row["resource"] in pcm_sources:
            owners[pcm_sources[row["resource"]]["pcm_sha256"]].add(row["skill"])
    for row in associations:
        if row["status"] == "pending_decode":
            if row["resource"] not in pcm:
                row["status"] = "no_usable_pcm"
            elif len(owners[pcm_sources[row["resource"]]["pcm_sha256"]]) > 1:
                row["status"] = "excluded_cross_skill_shared_pcm"
            else:
                row.update(status="available_for_bank",pcm=pcm_sources[row["resource"]])
    templates, features = base_templates, base_features
    coverage = [{'skill':skill,'hero_id':next(t['hero_id'] for t in base_templates if t['skill']==skill),
                 'templates':count,'status':'copied_fixed_official_base_not_accuracy_acceptance'}
                for skill,count in sorted(Counter(t['skill'] for t in base_templates).items())]
    phase_order = {"cast":0,"ability_related":1,"impact":2,"loop":3,"end":4}
    for spec in mapping["skills"]:
        rows = sorted((r for r in associations if r["skill"]==spec["skill"] and r["status"]=="available_for_bank"),
                      key=lambda r:(phase_order[r["phase_hint"]],r["event"],r["resource"]))
        count, seen_features = 0, set()
        for variant, selected_rows in template_jobs(rows):
            for row in selected_rows:
                value = pcm[row["resource"]]
                pitch = row["pitch"]
                if variant=="pitch_low":pitch += row["pitch_rand_min"]
                if variant=="pitch_high":pitch += row["pitch_rand_max"]
                if not .5 <= pitch <= 2:
                    continue
                if pitch != 1.:
                    value = mono8k(value,RATE*pitch)
                start = 0
                if variant=="transient" and len(value)>WINDOW:
                    cumulative=np.concatenate(([0.],np.cumsum(value.astype(np.float64)**2)))
                    energy = (cumulative[800:]-cumulative[:-800])/800
                    start = min(max(0,int(energy.argmax())-1200),len(value)-WINDOW)
                window = np.pad(value[start:start+WINDOW],(0,max(0,WINDOW-len(value[start:start+WINDOW]))))
                if float(np.sqrt(np.mean(window**2))) < 10**(-45/20):continue
                feature = descriptor(window)
                fingerprint = digest(feature.tobytes())
                if fingerprint in seen_features or float(np.linalg.norm(feature)) < .9:continue
                seen_features.add(fingerprint)
                identity = f"template_{len(templates)+1:03d}"
                excerpt = output/(identity+".wav")
                with wave.open(str(excerpt),"wb") as w:
                    w.setnchannels(1);w.setsampwidth(2);w.setframerate(RATE)
                    w.writeframes(np.clip(window*32768,-32768,32767).astype("<i2").tobytes())
                templates.append({"template_id":identity,"skill":spec["skill"],"hero_id":"npc_dota_hero_"+spec["hero"],
                    "label_basis":"installed_Valve_event_namespace_and_original_asset_not_live_accuracy_acceptance",
                    "event":row["event"],"event_phase":row["phase_hint"],"resource":row["resource"],
                    "resource_sha256":row["resource_sha256"],"event_resource":row["event_resource"],
                    "event_resource_sha256":row["event_resource_sha256"],"excerpt":excerpt.name,
                    "excerpt_sha256":digest(excerpt.read_bytes()),"variant":variant,"pitch":pitch,
                    "source_pcm_sha256":pcm_sources[row["resource"]]["pcm_sha256"],
                    "audio_start_s":start/RATE,"seconds":WINDOW/RATE})
                features.append(feature);count += 1
                if count>=args.templates_per_skill:break
            if count>=args.templates_per_skill:break
        coverage.append({"hero_id":"npc_dota_hero_"+spec["hero"],"skill":spec["skill"],
                         "templates":count,"status":"ready_for_blind_validation" if count else "no_template"})
    if digest(directory_bytes(args.vpk)) != digest(source_directory):raise ValueError("vpk_directory_changed_during_export")
    if args.base_bank and any(digest((args.base_bank/name).read_bytes()) != value for name,value in base_hashes.items()):
        raise ValueError('base_bank_changed_during_build')
    if base_revalidation is not None:
        base_meta = json.loads((args.base_bank/'bank.json').read_text(encoding='utf-8'))
        if verify_base_resources(base_meta,args.vpk,digest(source_directory)) != base_revalidation:
            raise ValueError('base_current_resources_changed_during_build')
    if not features or len(features)>512:raise ValueError("invalid_official_bank_size")
    np.save(output/"features.npy",np.asarray(features,dtype=np.float32),allow_pickle=False)
    manifest = {"schema_version":1,"feature_version":VERSION,"templates":templates,
        "selection_policy":"source_stage_and_transient_budget_v2_no_live_labels",
        "features_sha256":digest((output/"features.npy").read_bytes()),
        "source":"installed_Valve_original_sound_assets","purpose":"blind_validation_not_accuracy_claim",
        "directory_vpk_sha256":digest(source_directory),"mapping_sha256":digest(mapping_path.read_bytes()),
        "decoder_sha256":digest(args.decoder.read_bytes()),"ffmpeg_sha256":digest(args.ffmpeg.read_bytes()),
        "base_source_sha256":base_hashes or None,
        "base_current_resource_revalidation":base_revalidation,
        "builder_source_sha256":digest(Path(__file__).read_bytes())}
    report = {"coverage":coverage,"associations":associations,"unresolved_layers":unresolved,"decode_failures":failures,
        "compiled_resource_count":len(assets),"usable_pcm_count":len(pcm),"template_count":len(templates),
        "skills_with_templates":sum(r["templates"]>0 for r in coverage),
        "missing_skills":[r["skill"] for r in coverage if not r["templates"]]}
    (output/"coverage.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temporary = output/'bank.json.tmp'
    temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(output/'bank.json')  # Publish the loadable manifest only after all checks and reports.
    print(json.dumps({k:v for k,v in report.items() if k not in {"coverage","associations","unresolved_layers","decode_failures"}}))


if __name__ == "__main__":main()
