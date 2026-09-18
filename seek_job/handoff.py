from __future__ import annotations

import re
from pathlib import Path

import yaml

from .common import (PipelineError, age_hours, body_normalize, config_hash, digest, inside,
                     load_config, load_json, now, relative, validate, yaml_read)
from .contracts import MANIFEST, METADATA


def cv_context(root, config):
    settings = config["cv_handoff"]
    workspace = (Path(root) / settings["workspace"]).resolve()
    result = {"workspace": workspace, "status": "disabled" if not settings["enabled"] else "available",
              "notes": [], "skills": {}}
    if not settings["enabled"]:
        return result
    config_file, skill_file = (inside(workspace, settings[k]) for k in ("config_file", "skill_file"))
    if not config_file.is_file() or not skill_file.is_file():
        result.update(status="unavailable", notes=["latex_cv config/skill missing; search may continue."])
        return result
    try:
        cv_config = yaml_read(config_file.read_text(encoding="utf-8-sig"))
        if not isinstance(cv_config, dict) or not isinstance(cv_config.get("profile_root"), str):
            raise PipelineError("Invalid CV profile_root")
        # CV path values are relative to its config, not to seek_job or the skill directory.
        profile = inside(config_file.parent, cv_config["profile_root"])
        result["profile"] = profile
        if settings["profile_gap_check"]:
            skill_text = (profile / "skills.md").read_text(encoding="utf-8-sig")
            for name, tier, evidence in re.findall(
                    r"^\s*-\s+\*\*(.+?)\*\*\s*[—–-]\s*(professional|working|familiar|unverified)\s*[—–-]\s*evidence:\s*(.*)$",
                    skill_text, re.M):
                result["skills"][name.casefold()] = {"name": name, "tier": tier, "evidence": re.sub(r"<!--.*", "", evidence).strip()}
            preferences = profile / "preferences.md"
            if preferences.exists():
                text = preferences.read_text(encoding="utf-8-sig")
                match = re.search(r"\*\*Target roles:\*\*\s*(.+)", text)
                if match:
                    targets = {r.casefold() for p in config["search_profiles"] for r in p["target_roles"]}
                    profile_targets = {r.strip().casefold() for r in match[1].split(",")}
                    if targets != profile_targets:
                        result["notes"].append("Search roles differ from profile/preferences.md; search config was not changed.")
    except (OSError, PipelineError):
        result["notes"].append("Profile gap check unavailable; do not infer candidate skills.")
    return result


def profile_gaps(record, config, context):
    if not config["cv_handoff"]["profile_gap_check"] or not config["cv_handoff"]["enabled"]:
        return []
    result = []
    for skill in sorted(set(record.get("matchedRequiredSkills", []) + record.get("matchedPreferredSkills", []))):
        aliases = [skill] + config["skill_aliases"].get(skill, [])
        facts = [context["skills"][name.casefold()] for name in aliases if name.casefold() in context["skills"]]
        fact = facts[0] if facts else {"tier": "absent", "evidence": None}
        if fact["tier"] != "professional":
            result.append({"skill": skill, "tier": fact["tier"], "evidence": fact["evidence"],
                           "note": "Not a verified professional claim; CV must follow profile evidence."})
    return result


def job_markdown(record):
    front = {key: record[key] for key in ("schemaVersion", "jobId", "jobTitle", "company", "sourceUrl",
                                         "dateFound", "fetchedAt", "descriptionStatus")}
    return "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False) + "---\n" + record["_description"]


def read_job(path):
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise PipelineError("JD must have YAML front matter and a separate full body")
    header, body = text[4:].split("\n---\n", 1)
    front = yaml_read(header)
    if not isinstance(front, dict):
        raise PipelineError("Invalid JD front matter")
    return front, body_normalize(body)


def eligible(record, config, timestamp):
    return (not record.get("mergedInto") and record.get("folderPath")
            and record["evaluatedConfigHash"] == config_hash(config)
            and record["descriptionStatus"] == "complete" and record["matchStatus"] == "accepted"
            and record["identityStatus"] == "resolved"
            and record["availabilityStatus"] in config["cv_handoff"]["allowed_availability_statuses"]
            and record["availabilityCheckedAt"]
            and 0 <= age_hours(record["availabilityCheckedAt"], timestamp) <= config["freshness"]["max_availability_age_hours"])


def manifest(root, run_dir, checkpoint, config, records, context):
    timestamp = now()
    items = []
    if config["cv_handoff"]["enabled"]:
        for record in sorted(records, key=lambda r: (-r.get("matchScore", 0), r["jobId"])):
            if not eligible(record, config, timestamp):
                continue
            folder = inside(root, record["folderPath"])
            item = {key: record[key] for key in (
                "jobId", "company", "jobTitle", "sourceUrl", "descriptionHash", "descriptionStatus",
                "matchStatus", "availabilityStatus", "availabilityCheckedAt", "matchedProfileIds", "profileGaps")}
            item.update(metadataPath=relative(folder / "metadata.json", run_dir),
                        descriptionPath=relative(folder / "job-description.md", run_dir))
            items.append(item)
    result = {"schemaVersion": 1, "runId": checkpoint["runId"], "generatedAt": timestamp,
              "configHash": checkpoint["configHash"], "cvWorkspace": relative(context["workspace"], run_dir),
              "handoffStatus": context["status"], "jobs": items}
    validate(result, MANIFEST, "manifest")
    return result


def validate_manifest(root, path, job_ids=None, check_cv=True):
    root, path = Path(root).resolve(), Path(path).resolve()
    inside(root, path)
    data = load_json(path)
    validate(data, MANIFEST, "manifest")
    config, _ = load_config(root, path.parent / "config.snapshot.yaml")
    if data["configHash"] != config_hash(config):
        raise PipelineError("Manifest configHash does not match the run snapshot")
    checkpoint = load_json(path.parent / "checkpoint.json")
    if checkpoint["runId"] != data["runId"]:
        raise PipelineError("Manifest belongs to a different run")
    context = cv_context(root, config)
    resolved_cv = (path.parent / data["cvWorkspace"]).resolve()
    if resolved_cv != context["workspace"]:
        raise PipelineError("Manifest CV workspace differs from the run config")
    if check_cv and (context["status"] != "available" or data["handoffStatus"] != "available"):
        raise PipelineError("CV handoff unavailable/disabled; check configured latex_cv files")
    selected = set(job_ids or [])
    if len({item["jobId"] for item in data["jobs"]}) != len(data["jobs"]):
        raise PipelineError("Duplicate jobId in CV manifest")
    if selected - {item["jobId"] for item in data["jobs"]}:
        raise PipelineError("Requested jobId is not CV-ready in this manifest")
    pending = list((root / "state/staging").glob("*.json"))
    if any(not load_json(p).get("completed") for p in pending):
        raise PipelineError("An interrupted transaction requires recovery before CV handoff")
    valid = []
    for item in data["jobs"]:
        if selected and item["jobId"] not in selected:
            continue
        meta_path = inside(root, path.parent / item["metadataPath"])
        description_path = inside(root, path.parent / item["descriptionPath"])
        metadata = load_json(meta_path)
        validate(metadata, METADATA, "metadata")
        folder = inside(root, metadata["folderPath"])
        inside(inside(root, config["output"]["jobs_directory"]), folder)
        if meta_path != folder / "metadata.json" or description_path != folder / "job-description.md":
            raise PipelineError("Manifest paths do not identify the metadata's job folder")
        if not (folder / ".committed.json").is_file():
            raise PipelineError("Job folder has not been committed")
        marker = load_json(folder / ".committed.json")
        if marker.get("jobId") != metadata["jobId"] or marker.get("descriptionHash") != metadata["descriptionHash"]:
            raise PipelineError("Commit marker does not match the job version")
        for key in ("jobId", "company", "jobTitle", "sourceUrl", "descriptionHash", "descriptionStatus",
                    "matchStatus", "availabilityStatus", "availabilityCheckedAt", "matchedProfileIds", "profileGaps"):
            if metadata[key] != item[key]:
                raise PipelineError(f"Stale manifest: {key} changed; export a fresh manifest")
        if not eligible(metadata, config, now()):
            raise PipelineError("Job is no longer CV-ready or availability is stale")
        front, body = read_job(description_path)
        for key in ("jobId", "jobTitle", "company", "sourceUrl", "descriptionStatus"):
            if front.get(key) != metadata[key]:
                raise PipelineError(f"JD front matter mismatch: {key}")
        if digest(body) != item["descriptionHash"]:
            raise PipelineError("JD hash changed; do not silently use a newer description")
        if digest((folder / "source-content.txt").read_text(encoding="utf-8")) != metadata["sourceContentHash"]:
            raise PipelineError("Captured source hash changed")
        valid.append(item["jobId"])
    return valid


def handoff_prompt(manifest_path, root):
    template = Path(__file__).resolve().parent.parent / "prompts/handoff-to-latex-cv.md"
    return template.read_text(encoding="utf-8").replace("{{MANIFEST}}", str(manifest_path.resolve())).replace("{{SEEK_ROOT}}", str(Path(root).resolve()))
