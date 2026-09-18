"""Bounded public HTTP work; web search and interactive browser tasks stay with the agent."""
from __future__ import annotations

from datetime import timedelta

from .common import PipelineError, age_hours, dt, now
from .sources import FetchError, HttpClient, board_page, fetch_candidate


def collect(session, client=None, only_job=None):
    active_source = None

    def before_request(url):
        if not session.budget_ok():
            raise FetchError("budget_exhausted")
        last = session.cp.get("lastRequestAt")
        if last:
            import time
            delay = session.config["limits"]["min_request_interval_seconds"] - age_hours(last) * 3600
            if delay > 0:
                time.sleep(min(delay, 60))
        session.cp["lastRequestAt"] = now()
        metrics = session.cp["sourceMetrics"].setdefault(active_source, {"pages": 0, "queries": 0, "found": 0, "requests": 0})
        metrics["requests"] += 1
        # Persist request intent and budgets before a potentially interrupted HTTP call.
        session.persist()

    client = client or HttpClient(session.config["limits"], before_request)
    limits = session.config["limits"]
    if only_job and only_job not in session.records:
        raise PipelineError("Unknown job ID")
    if not only_job:
        tasks = [t for t in session.cp["tasks"] if t["kind"] == "board" and t["status"] in ("pending", "running")]
        while tasks and session.budget_ok():
            next_round = []
            for task in tasks:
                active_source = task["source"]
                metrics = session.cp["sourceMetrics"].setdefault(active_source, {"pages": 0, "queries": 0, "found": 0, "requests": 0})
                remaining = limits["max_candidates_per_source"] - metrics["found"]
                if remaining <= 0 or metrics["pages"] >= limits["max_pages_per_source"] or not session.budget_ok():
                    task.update(status="truncated", note="Source candidate/page/time budget reached.")
                    session.persist()
                    continue
                if task.get("retryAfter") and age_hours(task["retryAfter"]) < 0:
                    continue
                task["status"] = "running"
                session.persist()
                try:
                    observations, more = board_page(client, task["board"], task["cursor"], min(remaining, 30))
                    session.ingest(observations, attempted=True)
                    task["cursor"] += len(observations)
                    metrics = session.cp["sourceMetrics"][active_source]
                    metrics["pages"] += 1
                    metrics["found"] += len(observations)
                    task["pages"] += 1
                    task["found"] += len(observations)
                    task.update(status="pending" if more else "done", note=None)
                    if not more:
                        for error in session.cp["errors"]:
                            if error.get("taskId") == task["id"] and not error.get("resolvedAt"):
                                error["resolvedAt"] = now()
                    if more:
                        next_round.append(task)
                except FetchError as exc:
                    task.update(status="blocked", note=exc.reason)
                    seconds = max(exc.retry_after or 0, limits["candidate_retry_after_hours"] * 3600)
                    task["retryAfter"] = (dt(now()) + timedelta(seconds=seconds)).isoformat()
                    session.cp["errors"].append({"taskId": task["id"], "reason": exc.reason, "at": now()})
                except (ValueError, KeyError, TypeError) as exc:
                    task.update(status="blocked", note="adapter_payload_invalid")
                    session.cp["errors"].append({"taskId": task["id"], "reason": "adapter_payload_invalid", "at": now()})
                session.persist()
            tasks = next_round
    candidates = [r for r in session.records.values() if not r.get("mergedInto")
                  and (r["jobId"] == only_job if only_job else r["currentStep"] == "fetch")]
    for record in candidates:
        if not session.budget_ok():
            break
        if record.get("retryAfter") and age_hours(record["retryAfter"]) < 0:
            continue
        active_source = record["sourceAliases"][0]["source"]
        if active_source == "manual" and not record["sourceUrl"]:
            continue
        try:
            observation = fetch_candidate(client, record, session.config)
            session.ingest([observation], attempted=True)
        except FetchError as exc:
            if exc.reason == "budget_exhausted":
                session.cp["notes"].append("Active time budget reached; unfetched candidates remain queued.")
                break
            observation = {"schemaVersion": 1, "jobId": record["jobId"], "source": active_source,
                           "url": record["sourceUrl"], "blockedReason": exc.reason,
                           "availabilityStatus": "unknown"}
            session.ingest([observation], attempted=True)
            if exc.retry_after:
                record["retryAfter"] = (dt(now()) + timedelta(seconds=max(
                    exc.retry_after, limits["candidate_retry_after_hours"] * 3600))).isoformat()
            session.cp["errors"].append({"jobId": record["jobId"], "reason": exc.reason, "at": now()})
            session.persist([record] if record.get("folderPath") else [])
        except (ValueError, KeyError, TypeError):
            record["pendingManualAction"] = "adapter_payload_invalid"
            record["currentStep"] = "awaiting_manual"
            session.record_outcome(record, "failed")
            session.cp["errors"].append({"jobId": record["jobId"], "reason": "adapter_payload_invalid", "at": now()})
            session.persist()
    session.cp.update(status="paused", pausedAt=now(), finishedAt=None)
    session.persist()
    return session.cp["runId"]
