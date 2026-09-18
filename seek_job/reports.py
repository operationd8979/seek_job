from __future__ import annotations

from collections import Counter

from .common import json_text, now, relative
from .handoff import handoff_prompt, manifest


def safe(value):
    return str(value or "unknown").replace("\n", " ").replace("|", r"\|").replace("[", r"\[").replace("]", r"\]")


def summary(record):
    source = record["sourceUrl"] or "User-supplied local JD"
    lines = [f"# {safe(record['jobTitle'])}", "",
             f"- Company: {safe(record['company'])}",
             f"- Location: {safe(', '.join(record['locations']))}",
             f"- Work mode: {safe(record['workMode'])}",
             f"- Employment type: {safe(record['employmentType'])}",
             f"- Level: {safe(record['level'])}",
             f"- Posted: {safe(record['datePosted'])}",
             f"- Source: {source}",
             f"- Match: {record['matchStatus']} / score {record['matchScore']}",
             "", record["summary"] or (
                 f"The posting is for {safe(record['jobTitle'])} at {safe(record['company'])}. "
                 f"The source lists {safe(record['workMode'])} work and {safe(record['employmentType'])} employment. "
                 f"Evidence matches these configured skills: {', '.join(record['matchedRequiredSkills']) or 'none'}."),
             "", "## Evidence and review", "",
             "- Matched required skills: " + ", ".join(record["matchedRequiredSkills"]),
             "- Matched preferred skills: " + ", ".join(record["matchedPreferredSkills"]),
             "- Review: " + "; ".join(record.get("reasonCodes", []) + record["reviewNotes"] or ["none"]),
             "- Profile gaps: " + "; ".join(f"{g['skill']} ({g['tier']})" for g in record["profileGaps"]),
             "", "[Full job description](job-description.md)", ""]
    return "\n".join(lines)


def report_writes(session):
    cp, records = session.cp, session.records
    outcomes = cp["outcomes"]
    touched = [records[key] for key in outcomes if key in records]
    actions = Counter(outcome["action"] for outcome in outcomes.values())
    counts = {
        "uniqueCandidatesThisRun": len(outcomes), "totalStoredCandidates": len(records),
        "actions": dict(actions),
        "matches": dict(Counter(r["matchStatus"] for r in touched)),
        "incomplete": sum(r["descriptionStatus"] != "complete" for r in touched),
        "possibleDuplicates": sum(r["identityStatus"] == "possible_duplicate" for r in touched),
        "duplicates": len(cp["duplicates"]),
    }
    coverage = {}
    for name, config in session.config["sources"].items():
        tasks = [t for t in cp["tasks"] if t["source"] == name]
        statuses = Counter(t["status"] for t in tasks)
        coverage[name] = {"enabled": config["enabled"], "required": config["required_for_coverage"],
                          "tasks": dict(statuses), "metrics": cp["sourceMetrics"].get(name, {})}
    result = {"schemaVersion": 1, "runId": cp["runId"], "status": cp["status"],
              "configHash": cp["configHash"], "generatedAt": now(), "counts": counts,
              "coverage": coverage, "outcomes": outcomes, "errors": cp["errors"],
              "manualActions": [{"jobId": r["jobId"], "reason": r["pendingManualAction"], "retryAfter": r["retryAfter"]}
                                for r in touched if r.get("pendingManualAction")],
              "profileNotes": session.cv["notes"]}
    manifest_data = manifest(session.root, session.run_dir, cp, session.config, records.values(), session.cv)
    writes = [(session.run_dir / "results.json", json_text(result)),
              (session.run_dir / "cv-ready.json", json_text(manifest_data)),
              (session.run_dir / "cv-handoff.md", handoff_prompt(session.run_dir / "cv-ready.json", session.root))]
    lines = [f"# Run {cp['runId']}", "", f"- Status: {cp['status']}",
             f"- Started: {cp['startedAt']}", f"- Finished: {cp.get('finishedAt') or 'pending'}",
             f"- Paused: {cp.get('pausedAt') or 'no'}",
             f"- Active seconds: {cp['elapsedSeconds']:.1f}",
             f"- Config: [{cp['configHash']}](config.snapshot.yaml)",
             f"- Capabilities: {cp['capabilities']}", "",
             "## Counts (axes overlap; do not add them together)", "",
             "~~~json", json_text(counts).strip(), "~~~", "", "## Source coverage", "",
             "| Source | Required | Task outcomes | Metrics |", "| --- | --- | --- | --- |"]
    for name, item in coverage.items():
        if item["enabled"]:
            lines.append(f"| {name} | {item['required']} | {safe(item['tasks'])} | {safe(item['metrics'])} |")
    lines.extend(["", "Coverage describes planned work, never the whole job market.",
                  "", "## CV-ready jobs", "", "[Manifest](cv-ready.json) · [CV handoff prompt](cv-handoff.md)", ""])
    for item in manifest_data["jobs"]:
        lines.append(f"- {item['jobId']}: {safe(item['company'])} — {safe(item['jobTitle'])}; "
                     f"[local metadata]({item['metadataPath']}); source: {item['sourceUrl'] or 'user supplied'}")
    if not manifest_data["jobs"]:
        lines.append("No jobs satisfy all handoff conditions.")
    lines.extend(["", "## Manual actions and limitations", ""])
    for note in cp["notes"] + session.cv["notes"]:
        lines.append("- " + safe(note))
    for action in result["manualActions"]:
        lines.append(f"- {action['jobId']}: {action['reason']}; retry after {action['retryAfter']}")
    for task in cp["tasks"]:
        if task["status"] != "done":
            lines.append(f"- {task['id']} [{task['source']}]: {task['status']} — {safe(task.get('note'))}")
    for record in touched:
        for gap in record["profileGaps"]:
            lines.append(f"- {record['jobId']}: {safe(gap['skill'])} — profile tier {gap['tier']}.")
    lines.extend(["", "## Details", ""])
    groups = {
        "new-jobs": [r for r in touched if outcomes[r["jobId"]]["action"] == "created"],
        "updated-jobs": [r for r in touched if outcomes[r["jobId"]]["action"] == "updated"],
        "incomplete-jobs": [r for r in touched if r["descriptionStatus"] != "complete"],
        "needs-review": [r for r in touched if r["matchStatus"] == "needs_review"],
        "duplicates": [r for r in touched if r["jobId"] in cp["duplicates"] or r["identityStatus"] == "possible_duplicate"],
        "rejected-jobs": [r for r in touched if r["matchStatus"] == "rejected"],
    }
    for name, members in groups.items():
        lines.append(f"- [{name}]({name}.md)")
        body = [f"# {name}", ""]
        for record in members:
            body.append(f"- {record['jobId']}: {safe(record['company'])} — {safe(record['jobTitle'])}")
            body.append(f"  - Source: {record['sourceUrl'] or 'user supplied'}")
            if record.get("folderPath"):
                body.append(f"  - [Job folder]({relative(session.root / record['folderPath'], session.run_dir)})")
            body.append("  - Reasons: " + "; ".join(record.get("reasonCodes", []) or ["none"]))
        if not members:
            body.append("None.")
        writes.append((session.run_dir / f"{name}.md", "\n".join(body) + "\n"))
    errors = ["# Errors", ""] + [f"- {safe(error)}" for error in cp["errors"]]
    if not cp["errors"]:
        errors.append("None.")
    writes.append((session.run_dir / "errors.md", "\n".join(errors) + "\n"))
    lines.extend(["- [errors](errors.md)", ""])
    writes.append((session.run_dir / "run-summary.md", "\n".join(lines)))
    return writes
