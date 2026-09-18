from __future__ import annotations

import copy
import uuid
from pathlib import Path
from unittest.mock import patch

import yaml

from .common import PipelineError, load_config, now
from .engine import Session
from .handoff import validate_manifest
from .storage import Store


def synthetic_observation(timestamp=None):
    timestamp = timestamp or now()
    body = (
        "SYNTHETIC FIXTURE — NOT A REAL JOB.\n"
        "Senior Full Stack Developer at Synthetic Example Company.\n"
        "Responsibilities\nBuild .NET and C# services with Angular. Mentor junior developers.\n"
        "Requirements\nProfessional .NET, C#, Angular, SQL Server and Azure experience.\n"
        "Location: Vietnam. Full-time, remote worldwide; work from any country.\n"
        "Benefits\nPaid leave. This paid position is open for applications.\n"
        f"Date posted: {timestamp}\n"
    )
    return {
        "schemaVersion": 1, "source": "manual", "url": "https://example.com/synthetic/jobs/123",
        "tenant": "synthetic-example", "sourceJobId": "123",
        "company": "Synthetic Example Company", "jobTitle": "Senior Full Stack Developer",
        "description": body, "sourceContent": body, "descriptionStatus": "complete",
        "descriptionKind": "user_supplied", "completenessEvidence": "Complete synthetic fixture (not a real job).",
        "capturedAt": timestamp, "availabilityStatus": "open", "availabilityCheckedAt": timestamp,
        "facts": {"locations": ["Vietnam"], "jobCountries": ["VN"], "workMode": "remote",
                  "employmentType": "full-time", "level": "senior", "remoteScope": "worldwide",
                  "datePosted": timestamp},
        "evidence": {
            "locations": "Location: Vietnam.", "jobCountries": "Location: Vietnam.",
            "workMode": "remote worldwide", "employmentType": "Full-time",
            "level": "Senior Full Stack Developer", "remoteScope": "work from any country",
            "datePosted": timestamp, "availabilityStatus": "This paid position is open for applications.",
        },
    }


def fixture_root(target, config):
    target = Path(target).resolve()
    if target.exists():
        raise PipelineError("Dry-run target must be a new directory; no existing directory is overwritten")
    target.mkdir(parents=True)
    (target / "SYNTHETIC-FIXTURES-ONLY.txt").write_text(
        "Synthetic jobs only. Offline fixture workspace; never input to a real CV run.\n", encoding="utf-8")
    (target / "config").mkdir()
    (target / "config/search-config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def dry_run(root, out=None):
    root = Path(root).resolve()
    config, _ = load_config(root)
    fixture_config = copy.deepcopy(config)
    # A known self-contained scenario; user's live criteria can be completely different.
    fixture_config["search_profiles"] = [{
        "id": "synthetic", "target_roles": ["Full Stack Developer"], "levels": ["senior"],
        "must_have_skills": [".NET", "C#", "Angular"], "preferred_skills": ["Azure"],
    }]
    fixture_config["work_modes"], fixture_config["employment_types"] = ["remote"], ["full-time"]
    fixture_config["exclusions"] = {"hiring_levels": ["junior"], "unpaid": True, "title_keywords": []}
    fixture_config["geography"].update(job_countries=["VN"], work_from_country="VN",
                                       needs_sponsorship=None, minimum_timezone_overlap_hours=None)
    fixture_config["company_boards"] = []
    fixture_config["output"] = {"jobs_directory": "jobs", "runs_directory": "runs",
                                "state_file": "state/jobs-index.json", "candidates_file": "state/candidates.json"}
    fixture_config["limits"]["max_accepted_jobs_per_run"] = 2
    fixture_config["limits"]["max_candidates_per_source"] = max(2, fixture_config["limits"]["max_candidates_per_source"])
    fixture_config["cv_handoff"]["enabled"] = True
    fixture_config["cv_handoff"]["workspace"] = str((root / config["cv_handoff"]["workspace"]).resolve())
    fixture_config["cv_handoff"]["profile_gap_check"] = False
    target = Path(out).resolve() if out else root / "artifacts" / ("dry-run-" + uuid.uuid4().hex[:10])
    for folder in (root / config["output"]["jobs_directory"], root / config["output"]["runs_directory"],
                   root / "state", (root / config["output"]["state_file"]).parent,
                   (root / config["output"]["candidates_file"]).parent,
                   (root / config["cv_handoff"]["workspace"]).resolve()):
        if target == folder or target.is_relative_to(folder):
            raise PipelineError("Dry-run target cannot be in real data stores or latex_cv")
    fixture_root(target, fixture_config)
    with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden in dry run")), \
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network forbidden in dry run")):
        with Store(target, fixture_config).locked():
            session = Session.start(target)
            accepted = synthetic_observation()
            incomplete = {"schemaVersion": 1, "source": "manual", "url": "https://example.com/synthetic/blocked",
                          "jobTitle": "Synthetic inaccessible job", "company": "Synthetic Blocked Company",
                          "blockedReason": "auth_required"}
            ids = session.ingest([accepted, incomplete])
            assert session.records[ids[0]]["matchStatus"] == "accepted"
            repeated = session.ingest([accepted])
            assert repeated == [ids[0]]
            session.finish()
            valid = validate_manifest(target, session.run_dir / "cv-ready.json", check_cv=False)
            assert valid == [ids[0]]
            indexed = session.store.rebuild()
            assert indexed == 1
        return {"syntheticOnly": True, "networkUsed": False, "cvExecuted": False,
                "workspace": str(target), "runId": session.cp["runId"],
                "manifest": str(session.run_dir / "cv-ready.json"),
                "accepted": 1, "incomplete": 1, "dedupVerified": True, "rebuildVerified": True}
