"""Frozen native draft contract adapter; origins in docs/native-draft-contract-origins.json."""
import copy
import hashlib
import json
import math
import re
from .common import check_hero, utc_time

CAPABILITY_TAGS = ("disable", "initiate", "damage", "waveclear", "push", "save", "farm")
# Detailed reviews require schema v2 and the same source/patch gates as v1.
# Provider DISABLER/INITIATOR/NUKER labels cannot establish these traits.
ALL_CAPABILITY_TAGS = (*CAPABILITY_TAGS, "reliable_control", "reliable_initiation",
                   "physical_damage", "magical_damage", "early_pressure", "late_scaling")
ASSESSMENTS = frozenset({"yes", "no", "unknown"})
MAX_RULES_BYTES = 4 * 1024 * 1024
MAX_HEROES = 512
MAX_SOURCES = 1024
# Complete kits may include invoked skills, linked helpers and inherited AD
# variants. Invoker's installed definition contains 29 non-talent entries.
MAX_ABILITY_IDS = 32
_ID = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")


def rules_checksum(raw: dict) -> str:
    payload = {key: value for key, value in raw.items() if key != "sha256"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fields(value, expected, error):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(error)


def _identifier(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _text(value, limit=500):
    return isinstance(value, str) and 1 <= len(value) <= limit and bool(value.strip())


def _ids(values, *, known=None, allow_empty=False, limit=16):
    if not isinstance(values, list) or not (0 if allow_empty else 1) <= len(values) <= limit:
        raise ValueError("invalid_evidence_ids")
    if any(not _identifier(value) or known is not None and value not in known for value in values):
        raise ValueError("invalid_evidence_reference")
    if len(values) != len(set(values)):
        raise ValueError("duplicate_evidence_reference")


def validate_rules(raw: dict, *, allow_synthetic=False) -> dict:
    """Strict schema and provenance links; return a detached validated copy."""
    _fields(raw, {"schema_version", "ruleset_id", "source_kind", "reviewed_at_utc",
                  "source_build", "applies_to_game_version", "version_evidence",
                  "criteria_version", "criteria", "sources", "heroes", "sha256"}, "invalid_rules_schema")
    if type(raw["schema_version"]) is not int or raw["schema_version"] not in {1, 2}:
        raise ValueError("unsupported_rules_schema")
    allowed_tags = CAPABILITY_TAGS if raw["schema_version"] == 1 else ALL_CAPABILITY_TAGS
    if not _identifier(raw["ruleset_id"]) or not _identifier(raw["criteria_version"]):
        raise ValueError("invalid_rules_identity")
    if raw["source_kind"] not in {"installed_valve_resources", "test_fixture"}:
        raise ValueError("invalid_rules_source")
    if raw["source_kind"] == "test_fixture" and not allow_synthetic:
        raise ValueError("synthetic_rules_not_allowed")
    utc_time(raw["reviewed_at_utc"])
    _fields(raw["source_build"], {"client_version", "source_revision", "version_date"}, "invalid_source_build")
    for key in ("client_version", "source_revision"):
        value = raw["source_build"][key]
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 32:
            raise ValueError("invalid_source_build")
    date = raw["source_build"]["version_date"]
    if not isinstance(date, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) is None:
        raise ValueError("invalid_source_build_date")
    utc_time(date + "T00:00:00Z")
    criteria = raw["criteria"]
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 32:
        raise ValueError("invalid_capability_criteria")
    by_criterion = {}
    for row in criteria:
        _fields(row, {"criterion_id", "capability", "definition"}, "invalid_capability_criterion")
        if not _identifier(row["criterion_id"]) or row["criterion_id"] in by_criterion:
            raise ValueError("duplicate_or_invalid_criterion")
        if row["capability"] not in allowed_tags or not _text(row["definition"]):
            raise ValueError("invalid_capability_criterion")
        by_criterion[row["criterion_id"]] = row
    sources = raw["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError("invalid_capability_sources")
    by_source = {}
    for row in sources:
        _fields(row, {"source_id", "kind", "publisher", "resource_id", "sha256"}, "invalid_capability_source")
        if not _identifier(row["source_id"]) or row["source_id"] in by_source or not _identifier(row["resource_id"]):
            raise ValueError("invalid_or_duplicate_source_identity")
        if row["kind"] not in {"hero_definition", "ability_descriptions", "game_build", "version_confirmation"}:
            raise ValueError("invalid_capability_source_kind")
        publisher = "test_fixture" if raw["source_kind"] == "test_fixture" else "Valve"
        if row["publisher"] != publisher or not isinstance(row["sha256"], str) or not _SHA.fullmatch(row["sha256"]):
            raise ValueError("invalid_capability_source_provenance")
        by_source[row["source_id"]] = row
    version, evidence = raw["applies_to_game_version"], raw["version_evidence"]
    if version is None:
        if evidence is not None:
            raise ValueError("unbound_version_evidence")
    else:
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError("invalid_rules_game_version")
        _fields(evidence, {"source_ids", "basis"}, "missing_version_evidence")
        _ids(evidence["source_ids"], known=by_source)
        if not _text(evidence["basis"]) or not any(by_source[key]["kind"] == "version_confirmation"
                                                  for key in evidence["source_ids"]):
            raise ValueError("missing_semantic_version_confirmation")
    if not isinstance(raw["heroes"], list) or not 1 <= len(raw["heroes"]) <= MAX_HEROES:
        raise ValueError("invalid_capability_heroes")
    heroes, numeric_ids = set(), set()
    for hero in raw["heroes"]:
        _fields(hero, {"hero_id", "numeric_hero_id", "assessments"}, "invalid_capability_hero")
        check_hero(hero["hero_id"])
        numeric = hero["numeric_hero_id"]
        if hero["hero_id"] is None or hero["hero_id"] in heroes or type(numeric) is not int or not 1 <= numeric <= 65535 or numeric in numeric_ids:
            raise ValueError("duplicate_or_invalid_capability_hero")
        heroes.add(hero["hero_id"])
        numeric_ids.add(numeric)
        assessments = hero["assessments"]
        if not isinstance(assessments, dict) or not 1 <= len(assessments) <= len(allowed_tags):
            raise ValueError("invalid_hero_assessments")
        for capability, row in assessments.items():
            _fields(row, {"value", "criterion_id", "ability_ids", "source_ids", "basis", "review_scope"}, "invalid_capability_assessment")
            if capability not in allowed_tags or row["value"] not in ASSESSMENTS:
                raise ValueError("invalid_capability_assessment_value")
            criterion = by_criterion.get(row["criterion_id"])
            if criterion is None or criterion["capability"] != capability or not _text(row["basis"]):
                raise ValueError("invalid_assessment_criterion")
            unknown = row["value"] == "unknown"
            _ids(row["ability_ids"], allow_empty=unknown, limit=MAX_ABILITY_IDS)
            _ids(row["source_ids"], known=by_source, allow_empty=unknown)
            if unknown:
                if row["review_scope"] != "not_reviewed":
                    raise ValueError("invalid_unknown_review_scope")
            else:
                expected_scope = "base_ability" if row["value"] == "yes" else "complete_base_kit"
                kinds = {by_source[key]["kind"] for key in row["source_ids"]}
                if row["review_scope"] != expected_scope or not {"hero_definition", "ability_descriptions"} <= kinds:
                    raise ValueError("incomplete_mechanic_review")
    if not isinstance(raw["sha256"], str) or not _SHA.fullmatch(raw["sha256"]) or rules_checksum(raw) != raw["sha256"]:
        raise ValueError("capability_checksum_mismatch")
    return copy.deepcopy(raw)
