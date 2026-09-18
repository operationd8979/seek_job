"""Validate human/agent observations; facts need quotes in captured public content."""
from __future__ import annotations

import copy
import uuid
from datetime import timedelta
from pathlib import Path

from .common import (PipelineError, age_hours, body_normalize, check_public_text, digest,
                     dt, inside, load_json, normalize_url, now, validate)
from .contracts import METADATA, OBSERVATION
from .identity import combine_aliases

FACTS = ("locations", "jobCountries", "workMode", "employmentType", "level", "salary",
         "remoteScope", "eligibleCountries", "timezoneRequirements", "timezoneOverlapHours",
         "sponsorship", "authorizationRequired", "datePosted")


def read_observations(path):
    path = Path(path).resolve()
    data = load_json(path)
    records = data if isinstance(data, list) else [data]
    result = []
    for observation in records:
        validate(observation, OBSERVATION, "observation")
        observation = copy.deepcopy(observation)
        for field in ("description", "sourceContent"):
            file_key = field + "File"
            if file_key in observation:
                if field in observation:
                    raise PipelineError(f"Provide {field} or {file_key}, not both")
                target = inside(path.parent, observation.pop(file_key))
                observation[field] = target.read_text(encoding="utf-8-sig")
        result.append(observation)
    return result


def prepare(observation, config, timestamp=None):
    validate(observation, OBSERVATION, "observation")
    at = timestamp or now()
    captured = observation.get("capturedAt", at)
    if age_hours(captured, at) < -0.1:
        raise PipelineError("capturedAt cannot be in the future")
    description = body_normalize(observation.get("description", ""))
    source_content = observation.get("sourceContent", description).replace("\r\n", "\n").replace("\r", "\n")
    check_public_text(source_content)
    check_public_text(description)
    check_public_text(str(observation))
    url = normalize_url(observation["url"])
    if observation["source"] != "manual" and url is None:
        raise PipelineError("A non-manual observation requires a public URL")
    evidence = observation.get("evidence", {})
    evidence_corpus = source_content + "\n" + description
    for field, quote in evidence.items():
        if quote not in evidence_corpus:
            raise PipelineError(f"Evidence quote for {field} not found in captured source content")
    for field, value in observation.get("facts", {}).items():
        if value not in (None, [], "") and field not in evidence:
            raise PipelineError(f"facts.{field} requires an evidence quote")
    status = observation.get("descriptionStatus", "partial" if description else "unavailable")
    complete_evidence = observation.get("completenessEvidence", "")
    if status == "complete" and (not description.strip() or not complete_evidence.strip()):
        raise PipelineError("Complete JD requires full body and completenessEvidence")
    availability = observation.get("availabilityStatus", "unknown")
    checked = observation.get("availabilityCheckedAt")
    if availability != "unknown":
        if not checked or "availabilityStatus" not in evidence:
            raise PipelineError("Open/closed requires availabilityCheckedAt and source evidence")
        if age_hours(checked, at) < -0.1:
            raise PipelineError("availabilityCheckedAt cannot be in the future")
    aliases = [{"source": observation["source"], "tenant": observation.get("tenant"),
                "sourceJobId": observation.get("sourceJobId"), "urls": [url] if url else []}]
    linked_urls = set()
    for link in observation.get("linkedAliases", []):
        if link["quote"] not in evidence_corpus:
            raise PipelineError("Linked alias evidence missing from source")
        alias = copy.deepcopy(link["alias"])
        for link_url in alias["urls"]:
            if link_url not in link["quote"]:
                raise PipelineError("Linked alias URL must appear in the evidence quote")
        if not alias["urls"]:
            raise PipelineError("Cross-source identity needs a directly evidenced URL")
        alias["urls"] = [normalize_url(u) for u in alias["urls"]]
        linked_urls.update(alias["urls"])
        aliases.append(alias)
    canonical = normalize_url(observation.get("canonicalUrl"))
    if canonical and canonical != url and canonical not in linked_urls:
        raise PipelineError("A different canonical URL requires a linkedAlias with evidence")
    known_profiles = {p["id"] for p in config["search_profiles"]}
    for profile, match in observation.get("roleMatches", {}).items():
        if profile not in known_profiles or match["quote"] not in evidence_corpus:
            raise PipelineError("Role match requires a known profile and a source quote")
    job_id = "job-" + uuid.uuid4().hex
    record = {
        "schemaVersion": 1, "jobId": job_id, "folderPath": None,
        "sourceAliases": combine_aliases(aliases), "identityStatus": "resolved",
        "jobTitle": observation.get("jobTitle"), "company": observation.get("company"),
        "locations": [], "jobCountries": [], "workMode": None, "employmentType": None,
        "level": None, "salary": None, "remoteScope": None, "eligibleCountries": None,
        "timezoneRequirements": None, "timezoneOverlapHours": None,
        "sponsorship": None, "authorizationRequired": None, "datePosted": None,
        "discoveredVia": [{"source": observation["source"], "url": url, "seenAt": at}],
        "sourceUrl": observation["url"], "canonicalUrl": canonical,
        "descriptionSource": {"kind": observation.get("descriptionKind", "user_supplied" if observation["source"] == "manual" else "public_page"),
                              "url": observation["url"], "capturedAt": captured, "method": "validated_observation"},
        "dateFound": at, "lastSeen": at, "fetchedAt": captured,
        "availabilityCheckedAt": checked, "availabilityStatus": availability,
        "descriptionStatus": status, "descriptionHash": digest(description),
        "sourceContentHash": digest(source_content), "extractorVersion": "seek-job/1",
        "revisions": [], "summary": observation.get("summary", ""),
        "reviewNotes": observation.get("reviewNotes", []), "profileGaps": [],
        "evidence": evidence, "completenessEvidence": complete_evidence,
        "mergedInto": None, "_description": description, "_sourceContent": source_content,
        "roleMatches": observation.get("roleMatches", {}), "distinctFrom": [],
        "currentStep": "evaluate" if description else "fetch", "attempts": 0,
        "lastAttemptAt": None, "retryAfter": None, "pendingManualAction": None,
        "possibleDuplicates": [], "runAction": "deferred",
        "_hasAvailability": "availabilityStatus" in observation,
        "_requestedJobId": observation.get("jobId"),
    }
    record.update(observation.get("facts", {}))
    if observation.get("blockedReason"):
        record["pendingManualAction"] = observation["blockedReason"]
        record["currentStep"] = "awaiting_manual"
        record["retryAfter"] = (dt(at) + timedelta(hours=config["limits"]["candidate_retry_after_hours"])).isoformat()
    return record


def metadata(record):
    result = {key: record.get(key) for key in METADATA["properties"]}
    validate(result, METADATA, "job")
    return result
