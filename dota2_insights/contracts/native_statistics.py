"""Frozen native draft contract adapter; origins in docs/native-draft-contract-origins.json."""
import copy
import hashlib
import json
import math
import re
from .common import check_hero, utc_time

CAPABILITIES = frozenset({"disable", "initiate", "damage", "waveclear", "push", "save", "farm"})
SCOPE_KEYS = frozenset({"game_version", "game_mode", "rank", "region"})
MAX_BUNDLE_BYTES = 16 * 1024 * 1024


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def bundle_checksum(bundle: dict) -> str:
    return hashlib.sha256(canonical_bytes({key: value for key, value in bundle.items()
                                          if key != "sha256"})).hexdigest()


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def validate_bundle(raw: dict, *, allow_synthetic: bool = False) -> dict:
    """Fail closed on partial/mixed-scope data. Return an independent copy."""
    required = {"schema_version", "bundle_id", "provider", "source_url", "downloaded_at_utc",
                "source_updated_at_utc", "coverage_start_utc", "coverage_end_utc", "scope",
                "pair_scope", "heroes", "relations", "sha256"}
    if (not isinstance(raw, dict) or set(raw) != required or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 1):
        raise ValueError("invalid draft bundle schema")
    if raw["provider"] not in {"stratz", "opendota_aggregate", "synthetic"}:
        raise ValueError("unsupported statistics provider")
    if raw["provider"] == "synthetic" and not allow_synthetic:
        raise ValueError("synthetic statistics cannot enter a live draft")
    if not isinstance(raw["bundle_id"], str) or not 1 <= len(raw["bundle_id"]) <= 100:
        raise ValueError("invalid bundle identity")
    if raw["source_url"] not in {"https://api.stratz.com/graphql", "https://api.opendota.com/api"}:
        raise ValueError("unknown statistics provenance")
    start, end, fetched = (utc_time(raw[key]) for key in
                            ("coverage_start_utc", "coverage_end_utc", "downloaded_at_utc"))
    if not start < end <= fetched:
        raise ValueError("invalid statistics coverage window")
    if raw["source_updated_at_utc"] is not None:
        if utc_time(raw["source_updated_at_utc"]) > fetched:
            raise ValueError("source update cannot follow download")
    scope = raw["scope"]
    if not isinstance(scope, dict) or set(scope) != SCOPE_KEYS:
        raise ValueError("statistics scope is incomplete")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 100 for value in scope.values()):
        raise ValueError("statistics filters must be explicit")
    if raw["pair_scope"] != scope:
        raise ValueError("baseline and pair filters differ")
    if not isinstance(raw["heroes"], list) or not 2 <= len(raw["heroes"]) <= 512:
        raise ValueError("invalid hero population")
    heroes = set()
    for hero in raw["heroes"]:
        if not isinstance(hero, dict) or set(hero) != {
                "hero_id", "name", "match_count", "win_count", "capabilities", "capability_source"}:
            raise ValueError("invalid hero baseline")
        check_hero(hero["hero_id"])
        if hero["hero_id"] is None or hero["hero_id"] in heroes:
            raise ValueError("duplicate or missing hero")
        heroes.add(hero["hero_id"])
        if not isinstance(hero["name"], str) or not 1 <= len(hero["name"]) <= 100:
            raise ValueError("invalid hero display name")
        if not _count(hero["match_count"]) or not _count(hero["win_count"]) or hero["win_count"] > hero["match_count"]:
            raise ValueError("invalid baseline samples")
        if not isinstance(hero["capabilities"], list) or not set(hero["capabilities"]) <= CAPABILITIES:
            raise ValueError("unknown composition capability")
        if len(set(hero["capabilities"])) != len(hero["capabilities"]):
            raise ValueError("duplicate composition capability")
        if hero["capabilities"] and not isinstance(hero["capability_source"], str):
            raise ValueError("composition rules need provenance")
        if hero["capabilities"] and not hero["capability_source"].strip():
            raise ValueError("composition rules need provenance")
    if not isinstance(raw["relations"], list) or len(raw["relations"]) > 512 * 512 * 2:
        raise ValueError("invalid relation population")
    seen = set()
    for row in raw["relations"]:
        if not isinstance(row, dict) or set(row) != {"hero_id", "other_id", "kind", "match_count", "delta"}:
            raise ValueError("invalid relation schema")
        key = (row["hero_id"], row["other_id"], row["kind"])
        if key in seen or key[0] not in heroes or key[1] not in heroes or key[0] == key[1] or key[2] not in {"with", "against"}:
            raise ValueError("invalid or duplicated relation")
        seen.add(key)
        delta = row["delta"]
        if not _count(row["match_count"]) or type(delta) not in {int, float} or not math.isfinite(delta) or abs(delta) > 1:
            raise ValueError("relation rating must be a finite fraction within -1..1")
    if raw["sha256"] != bundle_checksum(raw):
        raise ValueError("draft bundle checksum mismatch")
    return copy.deepcopy(raw)
