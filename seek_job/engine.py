from __future__ import annotations

import copy
import itertools
import time
import uuid
from datetime import timedelta
from pathlib import Path

from .common import (PipelineError, age_hours, config_hash, dt, inside, json_text, load_config,
                     load_json, now, relative, slug, check_public_text, normalize_url)
from .handoff import cv_context, job_markdown, profile_gaps, read_job
from .identity import combine_aliases, possible_matches, strong_matches
from .matching import evaluate
from .observations import metadata, prepare
from .reports import report_writes, summary
from .storage import Store


def plan(config, capabilities):
    tasks = []
    domains = {"greenhouse": "site:job-boards.greenhouse.io", "lever": "site:jobs.lever.co",
               "workday": "site:myworkdayjobs.com", "linkedin": "site:linkedin.com/jobs",
               "company_career_pages": "careers", "web_search": "", "other_public_sources": "jobs"}
    by_source = {}
    for source, settings in sorted(config["sources"].items(), key=lambda item: item[1]["priority"]):
        if not settings["enabled"]:
            continue
        boards = [b for b in config["company_boards"] if b["source"] == source]
        source_tasks = [{"source": source, "kind": "board", "board": b, "cursor": 0} for b in boards]
        if not boards:
            profile_queries = []
            for profile in config["search_profiles"]:
                queries = []
                for role, mode in itertools.product(profile["target_roles"], config["work_modes"]):
                    geo = config["geography"]
                    if mode == "remote" and geo.get("allow_international_remote", False):
                        places = list(dict.fromkeys(["worldwide", geo["work_from_country"] or "international"]))
                    else:
                        places = geo.get("job_cities") or geo["job_countries"]
                    for place in places:
                        skills = profile["must_have_skills"][:1]
                        query = " ".join([domains[source], f'"{role}"', place, mode] + [f'"{s}"' for s in skills]).strip()
                        queries.append({"source": source, "kind": "search", "query": query, "profileId": profile["id"]})
                profile_queries.append(iter(queries))
            for batch in itertools.zip_longest(*profile_queries):
                source_tasks.extend(task for task in batch if task)
            source_tasks = source_tasks[:min(config["limits"]["max_queries_per_source"],
                                             config["limits"]["max_pages_per_source"])]
        by_source[source] = source_tasks
    # Round-robin source tasks respects priority without exhausting one source first.
    for batch in itertools.zip_longest(*by_source.values()):
        for task in batch:
            if task is None:
                continue
            task.update(id=f"task-{len(tasks) + 1:03d}", status="pending", pages=0, found=0, note=None)
            if task["kind"] == "search" and not capabilities["webSearch"]:
                task.update(status="blocked", note="capability_missing:web_search")
            tasks.append(task)
    return tasks


def find_run(root, run_id):
    root = Path(root).resolve()
    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id):
        raise PipelineError("Invalid run ID")
    candidates = list(root.glob(f"**/{run_id}/checkpoint.json"))
    matches = []
    for candidate in candidates:
        snapshot = candidate.parent / "config.snapshot.yaml"
        config, text = load_config(root, snapshot)
        expected = inside(root, config["output"]["runs_directory"]) / run_id
        # Ignore nested synthetic workspaces; their relative root is different.
        if candidate.parent.resolve() == expected.resolve():
            matches.append((candidate.parent, config, text))
    if len(matches) != 1:
        raise PipelineError("Run not found or run ID is ambiguous in this workspace")
    return matches[0]


class Session:
    def __init__(self, root, run_id):
        self.root = Path(root).resolve()
        self.run_dir, self.config, _ = find_run(self.root, run_id)
        try:
            current, _ = load_config(self.root)
        except (PipelineError, OSError):
            current = None
        self.store = Store(self.root, self.config)
        self.cp = load_json(self.run_dir / "checkpoint.json")
        if self.cp.get("schemaVersion") != 1 or self.cp["configHash"] != config_hash(self.config):
            raise PipelineError("Checkpoint/config snapshot mismatch")
        self.index, candidate_state = self.store.read()
        self.records = candidate_state["candidates"]
        # Reconstructed accepted jobs remain usable if only the candidate queue was lost.
        for job_id, meta in self.index["jobs"].items():
            if job_id not in self.records:
                folder = inside(self.root, meta["folderPath"])
                _, body = read_job(folder / "job-description.md")
                self.records[job_id] = {
                    **copy.deepcopy(meta), "_description": body,
                    "_sourceContent": (folder / "source-content.txt").read_text(encoding="utf-8"),
                    "currentStep": "evaluate", "attempts": 0,
                    "lastAttemptAt": None, "retryAfter": None, "pendingManualAction": None,
                    "possibleDuplicates": [], "runAction": "unchanged",
                }
        self.cv = cv_context(self.root, self.config)
        self.tick = time.monotonic()
        if current is None or config_hash(current) != self.cp["configHash"]:
            note = "Current config differs or is invalid; this run continues with its saved snapshot."
            if note not in self.cp["notes"]:
                self.cp["notes"].append(note)

    @classmethod
    def start(cls, root, web_search=False, browser=False):
        root = Path(root).resolve()
        config, config_text = load_config(root)
        store = Store(root, config)
        run_id = now()[:10] + "--" + uuid.uuid4().hex[:12]
        run_dir = inside(store.runs, run_id)
        capabilities = {"webSearch": web_search, "browserTakeover": browser and config["browser"]["mode"] != "disabled",
                        "publicHttp": True}
        cp = {"schemaVersion": 1, "runId": run_id, "configHash": config_hash(config),
              "startedAt": now(), "finishedAt": None, "pausedAt": None, "status": "paused",
              "capabilities": capabilities, "tasks": plan(config, capabilities),
              "elapsedSeconds": 0, "outcomes": {}, "duplicates": [], "errors": [], "notes": [],
              "sourceMetrics": {}, "publishedJobIds": [], "lastRequestAt": None}
        if not capabilities["browserTakeover"]:
            cp["notes"].append("Browser takeover unavailable; checkpoint and import a manually captured JD when needed.")
        store.commit([(run_dir / "config.snapshot.yaml", config_text),
                      (run_dir / "checkpoint.json", json_text(cp))])
        session = cls(root, run_id)
        session.refresh()
        return session

    def budget_ok(self):
        elapsed = self.cp["elapsedSeconds"] + time.monotonic() - self.tick
        return elapsed < self.config["limits"]["max_run_minutes"] * 60

    def _account_time(self):
        timestamp = time.monotonic()
        self.cp["elapsedSeconds"] += timestamp - self.tick
        self.tick = timestamp

    def record_outcome(self, record, action):
        previous = self.cp["outcomes"].get(record["jobId"])
        # Summarize the strongest action across this run, not the last no-op refresh.
        if previous and action == "unchanged":
            action = previous["action"]
        # A job created in this run remains a "new job" after enrichment.
        if previous and previous["action"] == "created" and action in ("updated", "unchanged"):
            action = "created"
        self.cp["outcomes"][record["jobId"]] = {"action": action, "at": now(),
                                               "reasonCodes": record.get("reasonCodes", [])}
        record["runAction"] = action

    def _folder(self, record):
        if record.get("folderPath"):
            return inside(self.root, record["folderPath"])
        base = "--".join([slug(record["company"]), slug(record["jobTitle"]),
                          slug(", ".join(record["locations"]))])
        for length in (12, 20, 32):
            path = self.store.jobs / f"{base}--{record['jobId'][4:4 + length]}"
            if len(str(path / "job-description.md")) > 245:
                path = self.store.jobs / f"{slug(record['company'], 12)}--{slug(record['jobTitle'], 12)}--job--{record['jobId'][4:4 + length]}"
            if len(str(path / "job-description.md")) > 245:
                raise PipelineError("Workspace path too long for portable Windows job output")
            if not path.exists() or load_json(path / "metadata.json").get("jobId") == record["jobId"]:
                return inside(self.root, path)
        raise PipelineError("Job folder ID collision")

    def _job_writes(self, record):
        if not record.get("folderPath") and record["matchStatus"] != "accepted":
            return []
        if not record.get("folderPath") and record["jobId"] not in self.cp["publishedJobIds"]:
            if len(self.cp["publishedJobIds"]) >= self.config["limits"]["max_accepted_jobs_per_run"]:
                record["currentStep"] = "quota_deferred"
                return []
            self.cp["publishedJobIds"].append(record["jobId"])
        folder = self._folder(record)
        record["folderPath"] = relative(folder, self.root)
        if record["matchStatus"] == "accepted" and not record.get("pendingManualAction"):
            record["currentStep"] = "done"
        writes = []
        existing = self.index["jobs"].get(record["jobId"])
        if existing and existing["descriptionHash"] != record["descriptionHash"]:
            history = folder / "revisions" / (now().replace(":", "") + "-" + uuid.uuid4().hex[:8])
            for filename in ("metadata.json", "job-description.md", "source-content.txt", "summary.md"):
                old_path = folder / filename
                if old_path.exists():
                    writes.append((history / filename, old_path.read_text(encoding="utf-8")))
            record["revisions"].append({"path": relative(history, self.root),
                                        "descriptionHash": existing["descriptionHash"], "createdAt": now()})
        meta = metadata(record)
        self.index["jobs"][record["jobId"]] = meta
        writes.extend([
            (folder / "metadata.json", json_text(meta)),
            (folder / "summary.md", summary(record)),
            (folder / "job-description.md", job_markdown(record)),
            (folder / "source-content.txt", record["_sourceContent"]),
            (folder / ".committed.json", json_text({"jobId": record["jobId"], "descriptionHash": record["descriptionHash"]})),
        ])
        return writes

    def persist(self, changed=()):
        self._account_time()
        writes = []
        # Rank before assigning the new-publication quota.
        for record in sorted(changed, key=lambda r: (-r.get("matchScore", 0), r["jobId"])):
            had_folder = bool(record.get("folderPath"))
            writes.extend(self._job_writes(record))
            if not had_folder and record.get("folderPath"):
                self.record_outcome(record, "created")
            elif record.get("currentStep") == "quota_deferred":
                self.record_outcome(record, "deferred")
        writes.extend([
            (self.store.index_path, json_text(self.index)),
            (self.store.candidates_path, json_text({"schemaVersion": 1, "candidates": self.records})),
            (self.run_dir / "checkpoint.json", json_text(self.cp)),
        ])
        writes.extend(report_writes(self))
        self.store.commit(writes)

    def refresh(self):
        changed = []
        for record in self.records.values():
            if record.get("mergedInto"):
                continue
            before = copy.deepcopy(record)
            if record.get("evaluatedConfigHash") != self.cp["configHash"]:
                record["roleMatches"] = {}
            evaluate(record, self.config)
            if record["matchStatus"] != "rejected" and record.get("sourceUrl"):
                stale = not record["availabilityCheckedAt"] or age_hours(record["availabilityCheckedAt"]) > self.config["freshness"]["max_availability_age_hours"]
                if stale and not record.get("pendingManualAction"):
                    record["currentStep"] = "fetch"
                retryable = record.get("pendingManualAction") in ("fetch_failed", "rate_limited", "not_found")
                if retryable and record.get("retryAfter") and age_hours(record["retryAfter"]) >= 0:
                    record["pendingManualAction"], record["currentStep"] = None, "fetch"
            record["profileGaps"] = profile_gaps(record, self.config, self.cv)
            self.record_outcome(record, "updated" if before.get("matchStatus") != record["matchStatus"] else "unchanged")
            changed.append(record)
        self.persist(changed)

    def ingest(self, observations, attempted=False):
        # Validate every observation before altering the in-memory candidate set.
        incoming_records = [prepare(obs, self.config) for obs in observations]
        changed, ids = {}, []
        for incoming in incoming_records:
            source = incoming["discoveredVia"][0]["source"]
            if source != "manual" and not self.config["sources"][source]["enabled"]:
                raise PipelineError(f"Source disabled in run config: {source}")
            matches = strong_matches(incoming, self.records.values())
            requested = incoming.pop("_requestedJobId", None)
            if requested:
                target = self.records.get(requested)
                if not target:
                    raise PipelineError("Observation jobId does not identify an existing candidate")
                while target.get("mergedInto"):
                    target = self.records[target["mergedInto"]]
                if target not in matches:
                    # Explicit candidate enrichment is allowed, but never cross distinct known IDs.
                    from .identity import incompatible_ids
                    if incompatible_ids(incoming, target):
                        raise PipelineError("Requested jobId conflicts with source posting ID")
                    matches.append(target)
            if not matches:
                seen_this_run = sum(source in self.records[k].get("_runSources", {}).get(self.cp["runId"], [])
                                    for k in self.cp["outcomes"] if k in self.records)
                if seen_this_run >= self.config["limits"]["max_candidates_per_source"]:
                    note = f"{source}: candidate quota reached; remaining input deferred outside this run."
                    if note not in self.cp["notes"]:
                        self.cp["notes"].append(note)
                    continue
                record = incoming
                old = None
                self.records[record["jobId"]] = record
            else:
                matches.sort(key=lambda r: (not bool(r.get("folderPath")), r["dateFound"], r["jobId"]))
                record = matches[0]
                old = copy.deepcopy(record)
                if record["jobId"] not in self.cp["duplicates"]:
                    self.cp["duplicates"].append(record["jobId"])
                source_priority = {"ats_api": 0, "company_page": 1, "user_supplied": 2, "public_page": 3}
                same_source = bool(record["sourceUrl"] and incoming["sourceUrl"]
                                   and normalize_url(record["sourceUrl"]) == normalize_url(incoming["sourceUrl"]))
                authoritative = (same_source or source_priority[incoming["descriptionSource"]["kind"]]
                                 <= source_priority[record["descriptionSource"]["kind"]])
                upgrade = (incoming["descriptionStatus"] == "complete"
                           and (record["descriptionStatus"] != "complete" or authoritative
                                and dt(incoming["fetchedAt"]) >= dt(record["fetchedAt"])))
                if upgrade or record["descriptionStatus"] != "complete" and incoming["_description"]:
                    preserved = {k: record.get(k) for k in ("jobId", "folderPath", "dateFound", "revisions",
                                                            "attempts", "lastAttemptAt", "distinctFrom")}
                    combined_aliases = combine_aliases(record["sourceAliases"], incoming["sourceAliases"])
                    via = record["discoveredVia"] + incoming["discoveredVia"]
                    record.update(incoming)
                    record.update(preserved)
                    record["sourceAliases"], record["discoveredVia"] = combined_aliases, via
                else:
                    record["sourceAliases"] = combine_aliases(record["sourceAliases"], incoming["sourceAliases"])
                    record["discoveredVia"] += incoming["discoveredVia"]
                    for field in ("company", "jobTitle"):
                        if not record.get(field):
                            record[field] = incoming[field]
                    newer_check = (not record["availabilityCheckedAt"] or not incoming["availabilityCheckedAt"]
                                   or dt(incoming["availabilityCheckedAt"]) >= dt(record["availabilityCheckedAt"]))
                    if incoming["_hasAvailability"] and authoritative and newer_check:
                        record["availabilityStatus"] = incoming["availabilityStatus"]
                        record["availabilityCheckedAt"] = incoming["availabilityCheckedAt"]
                        if "availabilityStatus" in incoming["evidence"]:
                            # Evidence remains anchored in the observation and is retained for audit.
                            quote = incoming["evidence"]["availabilityStatus"]
                            record["_sourceContent"] += "\n" + quote
                            record["evidence"]["availabilityStatus"] = quote
                        if incoming["availabilityStatus"] != "unknown" and not incoming["pendingManualAction"]:
                            record["pendingManualAction"] = None
                    if incoming["pendingManualAction"]:
                        record["pendingManualAction"] = incoming["pendingManualAction"]
                        record["currentStep"], record["retryAfter"] = incoming["currentStep"], incoming["retryAfter"]
                record["lastSeen"] = incoming["lastSeen"]
                for loser in matches[1:]:
                    record["sourceAliases"] = combine_aliases(record["sourceAliases"], loser["sourceAliases"])
                    loser.update(mergedInto=record["jobId"], matchStatus="needs_review", matchedProfileIds=[],
                                 identityStatus="possible_duplicate", currentStep="merged")
                    loser["reasonCodes"] = ["merged_into:" + record["jobId"]]
                    self.record_outcome(loser, "updated")
                    changed[loser["jobId"]] = loser
            record.setdefault("_runSources", {}).setdefault(self.cp["runId"], [])
            if source not in record["_runSources"][self.cp["runId"]]:
                record["_runSources"][self.cp["runId"]].append(source)
            if attempted:
                record["attempts"] = (record.get("attempts") or 0) + 1
                record["lastAttemptAt"] = now()
            if record["pendingManualAction"] is None:
                record["currentStep"] = "evaluate" if record["_description"] else "fetch"
                record["retryAfter"] = None
                if incoming["descriptionStatus"] == "complete" or incoming["_hasAvailability"]:
                    for error in self.cp["errors"]:
                        if error.get("jobId") == record["jobId"] and not error.get("resolvedAt"):
                            error["resolvedAt"] = now()
            from .common import digest
            record["sourceContentHash"] = digest(record["_sourceContent"])
            distinct = record.get("distinctFrom") or []
            possible = [key for key in possible_matches(record, self.records.values()) if key not in distinct]
            record["possibleDuplicates"] = possible
            record["identityStatus"] = "possible_duplicate" if possible else "resolved"
            for other_id in possible:
                other = self.records[other_id]
                other["identityStatus"] = "possible_duplicate"
                if record["jobId"] not in other["possibleDuplicates"]:
                    other["possibleDuplicates"].append(record["jobId"])
                evaluate(other, self.config)
                self.record_outcome(other, "updated")
                changed[other_id] = other
            evaluate(record, self.config)
            record["profileGaps"] = profile_gaps(record, self.config, self.cv)
            action = "deferred" if not record.get("folderPath") else "updated"
            if old and all(old.get(k) == record.get(k) for k in (
                    "descriptionHash", "matchStatus", "availabilityStatus", "sourceAliases", "jobTitle", "company")):
                action = "unchanged"
            if incoming["pendingManualAction"] in ("fetch_failed", "not_found"):
                action = "failed"
            self.record_outcome(record, action)
            changed[record["jobId"]] = record
            ids.append(record["jobId"])
        self.cp.update(status="paused", finishedAt=None, pausedAt=now())
        self.persist(changed.values())
        return ids

    def mark_task(self, task_id, status, note, pages=0, found=0, active_seconds=0):
        check_public_text(note)
        task = next((t for t in self.cp["tasks"] if t["id"] == task_id), None)
        if not task:
            raise PipelineError("Unknown task ID")
        if pages < 0 or found < 0 or active_seconds < 0:
            raise PipelineError("Task pages/found/active seconds cannot be negative")
        if task.get("retryAfter") and age_hours(task["retryAfter"]) < 0 and status == "pending":
            raise PipelineError("Task retryAfter has not elapsed")
        if task["kind"] == "search" and status == "done" and (pages < 1 or active_seconds <= 0):
            raise PipelineError("Completed search requires actual pages and --active-seconds spent outside the CLI")
        self.cp["elapsedSeconds"] += active_seconds
        metrics = self.cp["sourceMetrics"].setdefault(task["source"], {"pages": 0, "queries": 0, "found": 0, "requests": 0})
        metrics["pages"] += pages
        metrics["found"] += found
        if task["kind"] == "search" and task["status"] != "done" and status == "done":
            metrics["queries"] += 1
        if metrics["pages"] > self.config["limits"]["max_pages_per_source"]:
            status, note = "truncated", "Page budget exceeded; coverage is partial."
        if not self.budget_ok():
            status, note = "truncated", "Active time budget reached; coverage is partial."
        task.update(status=status, note=note, pages=task["pages"] + pages, found=task["found"] + found)
        if status == "done":
            for error in self.cp["errors"]:
                if error.get("taskId") == task_id and not error.get("resolvedAt"):
                    error["resolvedAt"] = now()
        self.persist()

    def finish(self):
        self.refresh()
        incomplete_tasks = any(t["status"] != "done" for t in self.cp["tasks"])
        pending = any(r.get("pendingManualAction") or r.get("currentStep") in ("fetch", "quota_deferred")
                      or r["descriptionStatus"] != "complete" and r["matchStatus"] != "rejected"
                      for r in self.records.values() if not r.get("mergedInto"))
        unresolved_errors = any(not error.get("resolvedAt") for error in self.cp["errors"])
        self.cp["status"] = "partial" if incomplete_tasks or pending or unresolved_errors or not self.budget_ok() else "complete"
        self.cp["finishedAt"], self.cp["pausedAt"] = now(), None
        self.persist()
        return self.cp["status"]

    def resolve_distinct(self, ids, note):
        if len(set(ids)) < 2 or not note.strip():
            raise PipelineError("Distinct resolution needs at least two job IDs and an explicit reason")
        check_public_text(note)
        for job_id in ids:
            if job_id not in self.records:
                raise PipelineError("Unknown job ID")
        for job_id in ids:
            record = self.records[job_id]
            record["distinctFrom"] = sorted(set((record.get("distinctFrom") or []) + [i for i in ids if i != job_id]))
            record["possibleDuplicates"] = [i for i in record["possibleDuplicates"] if i not in ids]
            record["identityStatus"] = "possible_duplicate" if record["possibleDuplicates"] else "resolved"
            record["reviewNotes"].append("Explicit distinct-posting resolution: " + note)
            evaluate(record, self.config)
            self.record_outcome(record, "updated")
        self.persist([self.records[i] for i in ids])
