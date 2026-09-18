from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent_config import load_agent_config
from .common import PipelineError, atomic_json, load_config, load_json
from .engine import Session, find_run
from .handoff import validate_manifest
from .observations import FACTS, read_observations
from .storage import Store

ROOT = Path(__file__).resolve().parent.parent


def parser():
    p = argparse.ArgumentParser(description="Manual job discovery, evidence review, and local CV handoff.")
    p.add_argument("--root", type=Path, default=ROOT, help="seek_job project root (default: location of this package)")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate current YAML without network access")
    start = commands.add_parser("start", help="Create run, plan queries, re-evaluate stored jobs; no network")
    start.add_argument("--web-search-available", action="store_true", help="Set only when the calling agent can use web search")
    start.add_argument("--browser-available", action="store_true", help="Set only when actual browser takeover tools exist")
    start.add_argument("--config", type=Path, help="Preset path; current config is not overwritten")
    ui = commands.add_parser("ui", help="Serve local pipeline dashboard")
    ui.add_argument("--port", type=int, default=8765)
    action = commands.add_parser("ui-action", help="Dashboard mutations through the CLI")
    action.add_argument("--input", type=Path, required=True)
    for name, help_text in (
        ("status", "Show persisted checkpoint"), ("resume", "Resume snapshot, recover and re-evaluate; no network"),
        ("ingest", "Import discovered URLs or captured JD observations; no network"),
        ("collect", "Fetch public ATS boards and queued public pages (network)"),
        ("task", "Record actual agent search/browser task result"),
        ("finish", "Export final reports and CV manifest; no network"),
        ("export", "Export one candidate as an observation for evidence enrichment"),
        ("distinct", "Explicitly resolve suspected duplicates as separate postings"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--run", required=True, help="Run ID printed by start")
        if name == "ingest":
            sub.add_argument("--input", type=Path, required=True)
        elif name == "collect":
            sub.add_argument("--job-id", help="Refresh one existing job instead of board discovery")
        elif name == "task":
            sub.add_argument("--task-id", required=True)
            sub.add_argument("--status", choices=["done", "blocked", "pending", "truncated", "skipped"], required=True)
            sub.add_argument("--note", required=True, help="What was actually checked; no credentials")
            sub.add_argument("--pages", type=int, default=0)
            sub.add_argument("--found", type=int, default=0)
            sub.add_argument("--active-seconds", type=float, default=0, help="Actual time spent in external search/browser tools (excluding user login wait)")
        elif name == "export":
            sub.add_argument("--job-id", required=True)
            sub.add_argument("--out", type=Path, required=True)
        elif name == "distinct":
            sub.add_argument("--job-id", action="append", required=True)
            sub.add_argument("--note", required=True)
        elif name == "resume":
            sub.add_argument("--web-search-available", action="store_true")
            sub.add_argument("--browser-available", action="store_true")
    commands.add_parser("recover", help="Replay interrupted writes while holding the writer lock")
    commands.add_parser("rebuild-index", help="Rebuild accepted-job index; preserve backup and candidate queue")
    handoff = commands.add_parser("validate-handoff", help="Read-only check; never runs latex_cv")
    handoff.add_argument("--manifest", type=Path, required=True)
    handoff.add_argument("--job-id", action="append")
    handoff.add_argument("--skip-cv-workspace", action="store_true", help="Validate data only, e.g. isolated fixtures")
    dry = commands.add_parser("dry-run", help="Synthetic fixtures in an isolated directory; network is disabled")
    dry.add_argument("--out", type=Path, help="New isolated directory (default: new folder under artifacts/)")
    return p


def run(args):
    root = args.root.resolve()
    if args.command == "ui":
        from .web import serve
        return serve(root, args.port)
    if args.command == "ui-action":
        from .workflow import mutate
        from .common import inside
        path = inside(root / "inbox", args.input.resolve())
        return mutate(root, load_json(path))
    if hasattr(args, "run"):
        _, config, _ = find_run(root, args.run)
    elif args.command == "validate-handoff":
        config, _ = load_config(root, args.manifest.resolve().parent / "config.snapshot.yaml")
    else:
        config, _ = load_config(root, args.config if args.command == "start" else None)
    if args.command == "validate":
        from .handoff import cv_context
        context = cv_context(root, config)
        return {"valid": True, "cvHandoff": context["status"], "agent": load_agent_config(root),
                "notes": context["notes"]}
    if args.command == "validate-handoff":
        return {"validJobIds": validate_manifest(root, args.manifest, args.job_id, not args.skip_cv_workspace),
                "cvExecuted": False}
    if args.command == "dry-run":
        from .dryrun import dry_run
        return dry_run(root, args.out)
    store = Store(root, config)
    with store.locked():
        if args.command == "recover":
            return {"recovered": True}
        if args.command == "rebuild-index":
            return {"indexedJobs": store.rebuild(), "candidateQueueRebuilt": False}
        if args.command == "start":
            session = Session.start(root, args.web_search_available, args.browser_available, args.config)
            return {"runId": session.cp["runId"], "runDirectory": str(session.run_dir), "networkUsed": False}
        session = Session(root, args.run)
        if args.command == "status":
            return session.cp
        if args.command == "resume":
            if args.web_search_available:
                session.cp["capabilities"]["webSearch"] = True
                for task in session.cp["tasks"]:
                    if task["note"] == "capability_missing:web_search":
                        task.update(status="pending", note=None)
            if args.browser_available and config["browser"]["mode"] != "disabled":
                session.cp["capabilities"]["browserTakeover"] = True
            session.refresh()
            session.cp.update(status="paused", finishedAt=None)
            session.persist()
            return {"runId": args.run, "configHash": session.cp["configHash"], "notes": session.cp["notes"]}
        if args.command == "ingest":
            return {"jobIds": session.ingest(read_observations(args.input))}
        if args.command == "collect":
            from .collection import collect
            return {"runId": collect(session, only_job=args.job_id)}
        if args.command == "task":
            session.mark_task(args.task_id, args.status, args.note, args.pages, args.found, args.active_seconds)
            return next(t for t in session.cp["tasks"] if t["id"] == args.task_id)
        if args.command == "finish":
            return {"runId": args.run, "status": session.finish(),
                    "manifest": str(session.run_dir / "cv-ready.json")}
        if args.command == "distinct":
            session.resolve_distinct(args.job_id, args.note)
            return {"resolvedAsDistinct": args.job_id}
        if args.command == "export":
            record = session.records.get(args.job_id)
            if not record:
                raise PipelineError("Unknown job ID")
            alias = record["sourceAliases"][0]
            observation = {
                "schemaVersion": 1, "jobId": record["jobId"],
                "source": alias["source"], "tenant": alias["tenant"], "sourceJobId": alias["sourceJobId"],
                "url": record["sourceUrl"], "company": record["company"], "jobTitle": record["jobTitle"],
                "description": record["_description"], "sourceContent": record["_sourceContent"],
                "descriptionStatus": record["descriptionStatus"], "descriptionKind": record["descriptionSource"]["kind"],
                "completenessEvidence": record["completenessEvidence"], "capturedAt": record["fetchedAt"],
                "availabilityStatus": record["availabilityStatus"], "availabilityCheckedAt": record["availabilityCheckedAt"],
                "facts": {key: record[key] for key in FACTS}, "evidence": record["evidence"],
                "summary": record["summary"], "reviewNotes": record["reviewNotes"],
                "roleMatches": record.get("roleMatches", {}),
            }
            from .common import inside
            out = inside(root, args.out.resolve())
            if out.exists():
                raise PipelineError("Export target already exists; choose a new inbox file")
            if not out.is_relative_to(root / "inbox"):
                raise PipelineError("Export observation under seek_job/inbox/ to keep data separate from state")
            atomic_json(out, observation)
            return {"observation": str(out)}
    raise PipelineError("Unsupported command")


def main(argv=None):
    try:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
        result = run(parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PipelineError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
