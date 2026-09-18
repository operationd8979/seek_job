"""JSON Schema contracts. Exported copies in schemas/ are checked in tests."""
from __future__ import annotations

import json
from pathlib import Path

VERSION = {"const": 1}
TEXT = {"type": "string", "minLength": 1}
NULL_TEXT = {"type": ["string", "null"], "minLength": 1}
STRINGS = {"type": "array", "items": TEXT, "uniqueItems": True}
COUNTRY = {"type": "string", "pattern": "^[A-Z]{2}$"}
COUNTRIES = {"type": "array", "items": COUNTRY, "uniqueItems": True}
DATE = {"anyOf": [{"type": "string", "format": "date"}, {"type": "string", "format": "date-time"}, {"type": "null"}]}
TIME = {"type": "string", "format": "date-time"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}


def enum(*values):
    return {"enum": list(values)}


def obj(properties, required=None, extra=False):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": extra}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


def number(minimum=0, maximum=None, integer=False):
    result = {"type": "integer" if integer else "number", "minimum": minimum}
    if maximum is not None:
        result["maximum"] = maximum
    return result


SOURCE_NAMES = ["web_search", "company_career_pages", "greenhouse", "lever",
                "workday", "linkedin", "other_public_sources"]
source = obj({
    "enabled": {"type": "boolean"}, "priority": number(1, 100, True),
    "required_for_coverage": {"type": "boolean"}, "mode": enum("discovery_and_manual"),
}, ["enabled", "priority", "required_for_coverage"])
CONFIG = obj({
    "schema_version": VERSION,
    "search_profiles": array(obj({
        "id": {"type": "string", "pattern": "^[a-z0-9_-]+$"},
        "target_roles": {**STRINGS, "minItems": 1}, "levels": {**STRINGS, "minItems": 1},
        "must_have_skills": STRINGS, "preferred_skills": STRINGS,
    }), 1),
    "skill_aliases": {"type": "object", "additionalProperties": STRINGS},
    "geography": obj({
        "job_countries": {**COUNTRIES, "minItems": 1},
        "work_from_country": {"anyOf": [COUNTRY, {"type": "null"}]},
        "authorized_work_countries": COUNTRIES,
        "relocation_allowed": {"type": ["boolean", "null"]},
        "needs_sponsorship": {"type": ["boolean", "null"]},
        "minimum_timezone_overlap_hours": {"anyOf": [number(0, 24), {"type": "null"}]},
        "work_from_city": NULL_TEXT,
    }, ["job_countries", "work_from_country", "authorized_work_countries",
        "relocation_allowed", "needs_sponsorship", "minimum_timezone_overlap_hours"]),
    "work_modes": array(enum("remote", "hybrid", "on-site"), 1),
    "employment_types": {**STRINGS, "minItems": 1},
    "exclusions": obj({"hiring_levels": STRINGS, "unpaid": {"type": "boolean"}, "title_keywords": STRINGS}),
    "freshness": obj({
        "posted_within_days": number(1, 3650, True),
        "unknown_date_policy": enum("needs_review", "reject"),
        "max_availability_age_hours": number(1, 8760),
    }),
    "matching": obj({
        "unknown_hard_constraint_policy": enum("needs_review", "reject"),
        "unknown_required_skill_policy": enum("needs_review", "reject"),
        "ranking_weights": obj({"preferred_skill_coverage": number(), "freshness": number()}),
    }),
    "sources": obj({key: source for key in SOURCE_NAMES}),
    "company_boards": array(obj({
        "source": enum("greenhouse", "lever"),
        "company": TEXT, "board": {"type": "string", "pattern": "^[A-Za-z0-9_-]+$"},
        "region": enum("global", "eu"),
    }, ["source", "company", "board"])),
    "browser": obj({"mode": enum("if_available", "disabled"),
                    "on_auth_required": enum("checkpoint_and_continue_other_sources")}),
    "limits": obj({
        "max_queries_per_source": number(1, 1000, True),
        "max_pages_per_source": number(1, 1000, True),
        "max_candidates_per_source": number(1, 10000, True),
        "max_accepted_jobs_per_run": number(1, 10000, True),
        "max_run_minutes": number(1, 1440), "request_timeout_seconds": number(1, 120),
        "max_retries": number(0, 5, True), "retry_backoff_seconds": number(0, 60),
        "min_request_interval_seconds": number(0, 60),
        "candidate_retry_after_hours": number(1, 8760),
    }),
    "cv_handoff": obj({
        "enabled": {"type": "boolean"}, "workspace": TEXT, "config_file": TEXT, "skill_file": TEXT,
        "allowed_availability_statuses": array(enum("open"), 1),
        "profile_gap_check": {"type": "boolean"},
    }),
    "output": obj({key: TEXT for key in ("jobs_directory", "runs_directory", "state_file", "candidates_file")}),
})

ALIAS = obj({"source": TEXT, "tenant": NULL_TEXT, "sourceJobId": NULL_TEXT, "urls": STRINGS})
PROVENANCE = obj({
    "kind": enum("ats_api", "company_page", "public_page", "user_supplied"),
    "url": NULL_TEXT, "capturedAt": TIME, "method": TEXT,
})
CHECK = obj({
    "name": TEXT, "result": enum("pass", "fail", "unknown"), "reasonCode": TEXT,
    "quote": {"type": "string"}, "source": NULL_TEXT,
})
METADATA = obj({
    "schemaVersion": VERSION, "jobId": TEXT, "folderPath": TEXT,
    "sourceAliases": array(ALIAS), "identityStatus": enum("resolved", "possible_duplicate"),
    "jobTitle": NULL_TEXT, "company": NULL_TEXT, "locations": STRINGS,
    "jobCountries": COUNTRIES, "workMode": NULL_TEXT, "employmentType": NULL_TEXT,
    "level": NULL_TEXT, "salary": {"type": ["object", "string", "null"]},
    "discoveredVia": array(obj({"source": TEXT, "url": NULL_TEXT, "seenAt": TIME})),
    "sourceUrl": NULL_TEXT, "canonicalUrl": NULL_TEXT, "descriptionSource": PROVENANCE,
    "remoteScope": enum("worldwide", "restricted", None), "eligibleCountries": {"anyOf": [COUNTRIES, {"type": "null"}]},
    "timezoneRequirements": NULL_TEXT, "timezoneOverlapHours": {"anyOf": [number(0, 24), {"type": "null"}]},
    "sponsorship": {"type": ["boolean", "null"]}, "authorizationRequired": {"type": ["boolean", "null"]},
    "datePosted": DATE, "dateFound": TIME, "lastSeen": TIME, "fetchedAt": TIME,
    "availabilityCheckedAt": {"anyOf": [TIME, {"type": "null"}]},
    "evaluatedConfigHash": HASH, "matchedProfileIds": STRINGS,
    "evaluations": array(obj({"profileId": TEXT, "checks": array(CHECK), "status": enum("accepted", "rejected", "needs_review")})),
    "matchedRequiredSkills": STRINGS, "matchedPreferredSkills": STRINGS,
    "matchScore": number(), "scoreBreakdown": {"type": "object"},
    "descriptionStatus": enum("complete", "partial", "unavailable"),
    "matchStatus": enum("accepted", "rejected", "needs_review"),
    "availabilityStatus": enum("open", "closed", "unknown"),
    "descriptionHash": HASH, "sourceContentHash": HASH, "extractorVersion": TEXT,
    "revisions": array(obj({"path": TEXT, "descriptionHash": HASH, "createdAt": TIME})),
    "summary": {"type": "string"}, "reviewNotes": STRINGS, "profileGaps": array({"type": "object"}),
    "evidence": {"type": "object", "additionalProperties": TEXT},
    "completenessEvidence": {"type": "string"},
    "mergedInto": NULL_TEXT,
    "distinctFrom": STRINGS,
    "roleMatches": {"type": "object", "additionalProperties": obj({"result": enum("pass", "fail"), "quote": TEXT})},
})
METADATA["allOf"] = [{
    "if": {"properties": {"matchStatus": {"const": "accepted"}}},
    "then": {"properties": {
        "descriptionStatus": {"const": "complete"}, "availabilityStatus": {"const": "open"},
        "identityStatus": {"const": "resolved"}, "jobTitle": TEXT, "company": TEXT,
        "matchedProfileIds": {**STRINGS, "minItems": 1},
    }},
}]
HANDOFF_ITEM = obj({
    "jobId": TEXT, "company": TEXT, "jobTitle": TEXT, "sourceUrl": NULL_TEXT,
    "metadataPath": TEXT, "descriptionPath": TEXT, "descriptionHash": HASH,
    "descriptionStatus": {"const": "complete"}, "matchStatus": {"const": "accepted"},
    "availabilityStatus": {"const": "open"}, "availabilityCheckedAt": TIME,
    "matchedProfileIds": STRINGS, "profileGaps": array({"type": "object"}),
})
MANIFEST = obj({
    "schemaVersion": VERSION, "runId": TEXT, "generatedAt": TIME, "configHash": HASH,
    "cvWorkspace": TEXT, "handoffStatus": enum("available", "unavailable", "disabled"),
    "jobs": array(HANDOFF_ITEM),
})

# A locally captured observation, never an instruction to run shell or browser actions.
OBSERVATION = obj({
    "schemaVersion": VERSION, "source": enum(*SOURCE_NAMES, "manual"),
    "jobId": TEXT,
    "url": NULL_TEXT, "tenant": NULL_TEXT, "sourceJobId": NULL_TEXT,
    "jobTitle": NULL_TEXT, "company": NULL_TEXT,
    "description": {"type": "string"}, "descriptionFile": TEXT,
    "sourceContent": {"type": "string"}, "sourceContentFile": TEXT,
    "descriptionStatus": enum("complete", "partial", "unavailable"),
    "descriptionKind": enum("ats_api", "company_page", "public_page", "user_supplied"),
    "completenessEvidence": {"type": "string"}, "capturedAt": TIME,
    "availabilityStatus": enum("open", "closed", "unknown"),
    "availabilityCheckedAt": {"anyOf": [TIME, {"type": "null"}]},
    "facts": obj({key: METADATA["properties"][key] for key in (
        "locations", "jobCountries", "workMode", "employmentType", "level", "salary",
        "remoteScope", "eligibleCountries", "timezoneRequirements", "timezoneOverlapHours",
        "sponsorship", "authorizationRequired", "datePosted",
    )}, []),
    "evidence": {"type": "object", "additionalProperties": TEXT},
    "summary": {"type": "string"}, "reviewNotes": STRINGS,
    "blockedReason": enum("auth_required", "captcha", "rate_limited", "fetch_failed", "browser_required", "not_found"),
    "canonicalUrl": NULL_TEXT,
    "linkedAliases": array(obj({"alias": ALIAS, "quote": TEXT})),
    "roleMatches": {"type": "object", "additionalProperties": obj({"result": enum("pass", "fail"), "quote": TEXT})},
}, ["schemaVersion", "source", "url"])

SCHEMAS = {
    "search-config": CONFIG, "job-metadata": METADATA, "cv-ready": MANIFEST,
    "observation": OBSERVATION,
}
for name, schema in SCHEMAS.items():
    schema.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
                   "title": name, "$id": f"https://seek-job.local/schemas/{name}.schema.json"})


def export(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    for name, schema in SCHEMAS.items():
        (directory / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
