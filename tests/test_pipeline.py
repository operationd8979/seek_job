"""Synthetic/offline integration tests. Never fetch jobs or execute latex_cv."""
from __future__ import annotations

import copy
import json
import socket
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import yaml

from seek_job.common import (PipelineError, atomic_json, config_hash, digest, dt, inside,
                             load_config, load_json, normalize_url, now, slug, yaml_read)
from seek_job.contracts import SCHEMAS, CONFIG, OBSERVATION
from seek_job.dryrun import fixture_root, synthetic_observation
from seek_job.engine import Session
from seek_job.handoff import read_job, validate_manifest
from seek_job.identity import keys
from seek_job.matching import evaluate, skill_evidence
from seek_job.observations import prepare
from seek_job.sources import FetchError, greenhouse_post, lever_post, structured_page, HttpClient
from seek_job.storage import Store

REPO = Path(__file__).resolve().parents[1]


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.net = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        self.net.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "fixture"
        self.config, _ = load_config(REPO, REPO / "tests/fixtures/search-config.yaml")
        self.config = copy.deepcopy(self.config)
        cv_root = Path(self.tmp.name) / "synthetic_latex_cv"
        (cv_root / "profile").mkdir(parents=True)
        (cv_root / ".codex/skills/latex-cv-tailor").mkdir(parents=True)
        (cv_root / "cv.config.yaml").write_text("profile_root: ./profile\n", encoding="utf-8")
        (cv_root / ".codex/skills/latex-cv-tailor/SKILL.md").write_text(
            "# SYNTHETIC skill contract fixture; never execute a CV workflow.\n", encoding="utf-8")
        (cv_root / "profile/skills.md").write_text(
            "- **.NET** — unverified — evidence: none\n- **Angular** — unverified — evidence: none\n",
            encoding="utf-8")
        (cv_root / "profile/preferences.md").write_text(
            "- **Target roles:** Synthetic Developer\n", encoding="utf-8")
        self.config["cv_handoff"]["workspace"] = str(cv_root)
        self.config["cv_handoff"]["profile_gap_check"] = False
        self.config["geography"]["work_from_country"] = "VN"
        fixture_root(self.root, self.config)
        self.lock = Store(self.root, self.config).locked()
        self.lock.__enter__()
        self.session = Session.start(self.root)

    def tearDown(self):
        self.lock.__exit__(None, None, None)
        self.tmp.cleanup()
        self.net.stop()

    def ingest(self, observation=None):
        return self.session.ingest([observation or synthetic_observation()])[0]

    def record(self, observation=None):
        return self.session.records[self.ingest(observation)]

    def save_config(self):
        (self.root / "config/search-config.yaml").write_text(
            yaml.safe_dump(self.config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def test_accepted_roundtrip_and_manifest(self):
        record = self.record()
        self.assertEqual(record["matchStatus"], "accepted")
        self.assertEqual(record["matchedProfileIds"], ["fullstack_dotnet"])
        folder = self.root / record["folderPath"]
        for filename in ("metadata.json", "summary.md", "job-description.md", "source-content.txt"):
            self.assertTrue((folder / filename).is_file())
        front, body = read_job(folder / "job-description.md")
        self.assertEqual(front["jobId"], record["jobId"])
        self.assertEqual(digest(body), record["descriptionHash"])
        self.assertEqual(validate_manifest(self.root, self.session.run_dir / "cv-ready.json"), [record["jobId"]])

    def test_duplicate_keeps_id_and_folder_on_title_change(self):
        obs = synthetic_observation()
        job_id = self.ingest(obs)
        original = self.session.records[job_id]["folderPath"]
        obs["jobTitle"] = "Senior Full Stack Engineer"
        obs["description"] += "\nSenior Full Stack Engineer is the updated title.\n"
        obs["sourceContent"] = obs["description"]
        self.assertEqual(self.ingest(obs), job_id)
        self.assertEqual(self.session.records[job_id]["folderPath"], original)
        self.assertEqual(len(list((self.root / "jobs").iterdir())), 1)

    def test_same_source_id_different_tenant_not_merged(self):
        a = synthetic_observation()
        b = copy.deepcopy(a)
        b.update(tenant="other-company", company="Other Company", url="https://example.com/other/123")
        ids = self.session.ingest([a, b])
        self.assertNotEqual(ids[0], ids[1])

    def test_distinct_requisition_ids_not_fuzzy_merged(self):
        a = synthetic_observation()
        b = copy.deepcopy(a)
        b.update(sourceJobId="456", url="https://example.com/synthetic/jobs/456")
        ids = self.session.ingest([a, b])
        self.assertNotEqual(*ids)
        self.assertTrue(all(self.session.records[i]["identityStatus"] == "resolved" for i in ids))

    def test_similarity_requires_review_then_distinct_resolution(self):
        a = synthetic_observation()
        b = copy.deepcopy(a)
        b.update(source="linkedin", tenant=None, sourceJobId=None, url="https://www.linkedin.com/jobs/view/456")
        ids = self.session.ingest([a, b])
        self.assertTrue(all(self.session.records[i]["identityStatus"] == "possible_duplicate" for i in ids))
        self.assertEqual(load_json(self.session.run_dir / "cv-ready.json")["jobs"], [])
        self.session.resolve_distinct(ids, "Synthetic explicit confirmation: separate requisitions.")
        self.assertTrue(all(self.session.records[i]["identityStatus"] == "resolved" for i in ids))

    def test_cross_source_direct_link_preserves_job_id(self):
        a = synthetic_observation()
        job_id = self.ingest(a)
        b = copy.deepcopy(a)
        b.update(source="linkedin", tenant="linkedin", sourceJobId="999", url="https://www.linkedin.com/jobs/view/999")
        link = "Official posting: " + a["url"]
        b["sourceContent"] += "\n" + link
        b["linkedAliases"] = [{"alias": {"source": "manual", "tenant": a["tenant"], "sourceJobId": "123", "urls": [a["url"]]}, "quote": link}]
        self.assertEqual(self.ingest(b), job_id)
        self.assertEqual(len(self.session.records[job_id]["sourceAliases"]), 2)

    def test_merge_two_preexisting_records_keeps_redirect(self):
        a = synthetic_observation()
        b = copy.deepcopy(a)
        b.update(source="linkedin", tenant="linkedin", sourceJobId="999", url="https://www.linkedin.com/jobs/view/999")
        ids = self.session.ingest([a, b])
        link = "Official posting: " + a["url"]
        b["sourceContent"] += "\n" + link
        b["linkedAliases"] = [{"alias": {"source": "manual", "tenant": a["tenant"], "sourceJobId": "123", "urls": [a["url"]]}, "quote": link}]
        survivor = self.ingest(b)
        loser = next(i for i in ids if i != survivor)
        self.assertEqual(self.session.records[loser]["mergedInto"], survivor)
        self.assertEqual(len(load_json(self.session.run_dir / "cv-ready.json")["jobs"]), 1)

    def test_incomplete_upgrades_same_candidate(self):
        full = synthetic_observation()
        partial = {key: full[key] for key in ("schemaVersion", "source", "url", "tenant", "sourceJobId")}
        job_id = self.ingest(partial)
        self.assertIsNone(self.session.records[job_id]["folderPath"])
        self.assertEqual(self.ingest(full), job_id)
        self.assertEqual(self.session.records[job_id]["descriptionStatus"], "complete")

    def test_partial_never_overwrites_full_jd(self):
        record = self.record()
        old_hash = record["descriptionHash"]
        partial = {"schemaVersion": 1, "source": "manual", "url": record["sourceUrl"],
                   "description": "Short snippet only", "descriptionStatus": "partial"}
        self.ingest(partial)
        self.assertEqual(record["descriptionHash"], old_hash)
        self.assertEqual(record["descriptionStatus"], "complete")

    def test_changed_jd_revision_and_stale_manifest(self):
        obs = synthetic_observation()
        record = self.record(obs)
        manifest_path = self.session.run_dir / "cv-ready.json"
        old_manifest = manifest_path.read_text(encoding="utf-8")
        obs["description"] += "\nAdditional benefit: synthetic paid training.\n"
        obs["sourceContent"] = obs["description"]
        self.ingest(obs)
        self.assertEqual(len(record["revisions"]), 1)
        self.assertTrue((self.root / record["revisions"][0]["path"] / "job-description.md").exists())
        manifest_path.write_text(old_manifest, encoding="utf-8")
        with self.assertRaisesRegex(PipelineError, "Stale manifest"):
            validate_manifest(self.root, manifest_path)

    def test_jd_tampering_detected(self):
        record = self.record()
        path = self.root / record["folderPath"] / "job-description.md"
        path.write_text(path.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
        with self.assertRaisesRegex(PipelineError, "hash"):
            validate_manifest(self.root, self.session.run_dir / "cv-ready.json")

    def test_remote_uk_not_allowed_from_vietnam(self):
        obs = synthetic_observation()
        obs["facts"].update(remoteScope="restricted", eligibleCountries=["GB"], jobCountries=["GB"])
        quote = "Remote UK only; applicants must work from the United Kingdom."
        obs["sourceContent"] += quote
        for key in ("remoteScope", "eligibleCountries", "jobCountries"):
            obs["evidence"][key] = quote
        record = self.record(obs)
        self.assertEqual(record["matchStatus"], "rejected")
        self.assertIn("remote_country_fail", record["reasonCodes"])

    def test_missing_eligibility_is_unknown(self):
        obs = synthetic_observation()
        obs["facts"].pop("remoteScope")
        record = self.record(obs)
        self.assertEqual(record["matchStatus"], "needs_review")
        self.assertIn("remote_scope_unknown", record["reasonCodes"])

    def test_senior_mentoring_junior_not_excluded(self):
        record = self.record()
        self.assertEqual(record["matchStatus"], "accepted")
        self.assertNotIn("level_excluded", record["reasonCodes"])

    def test_all_required_skills_and_alias_boundaries(self):
        self.assertIsNone(skill_evidence("AngularJS JavaScript", ["Angular", "Java"]))
        self.assertIsNotNone(skill_evidence("C# and ASP.NET Core", [".NET", "ASP.NET Core"]))
        self.assertIsNone(skill_evidence("No Angular experience required.", ["Angular"]))
        obs = synthetic_observation()
        obs["description"] = obs["description"].replace("C#", "Rust")
        obs["sourceContent"] = obs["description"]
        record = self.record(obs)
        self.assertEqual(record["matchStatus"], "needs_review")
        self.assertIn("skill_not_evidenced", record["reasonCodes"])

    def test_config_change_reevaluates_rejected_and_resume_uses_snapshot(self):
        self.config["search_profiles"][0]["levels"] = ["junior"]
        self.save_config()
        # A new run uses new config; the existing run still uses its saved version.
        resumed = Session(self.root, self.session.cp["runId"])
        self.assertEqual(resumed.config["search_profiles"][0]["levels"], ["senior", "lead"])
        fresh = Session.start(self.root)
        job_id = fresh.ingest([synthetic_observation()])[0]
        self.assertEqual(fresh.records[job_id]["matchStatus"], "needs_review")  # backend role remains unconfirmed
        self.config["search_profiles"][0]["levels"] = ["senior", "lead"]
        self.save_config()
        newer = Session.start(self.root)
        self.assertEqual(newer.records[job_id]["matchStatus"], "accepted")

    def test_old_availability_never_handoff(self):
        obs = synthetic_observation()
        obs["availabilityCheckedAt"] = (dt(now()) - timedelta(hours=72)).isoformat()
        record = self.record(obs)
        self.assertEqual(record["matchStatus"], "needs_review")
        self.assertEqual(load_json(self.session.run_dir / "cv-ready.json")["jobs"], [])

    def test_unknown_date_review(self):
        obs = synthetic_observation()
        obs["facts"].pop("datePosted")
        record = self.record(obs)
        self.assertEqual(record["matchStatus"], "needs_review")

    def test_closed_preserves_folder_but_not_manifest(self):
        obs = synthetic_observation()
        record = self.record(obs)
        obs["availabilityStatus"] = "closed"
        obs["sourceContent"] += "\nThis position is closed."
        obs["evidence"]["availabilityStatus"] = "This position is closed."
        self.ingest(obs)
        self.assertTrue((self.root / record["folderPath"]).is_dir())
        self.assertEqual(record["matchStatus"], "rejected")
        self.assertEqual(load_json(self.session.run_dir / "cv-ready.json")["jobs"], [])

    def test_blocked_refresh_preserves_body_and_sets_unknown(self):
        record = self.record()
        original_hash = record["descriptionHash"]
        self.ingest({"schemaVersion": 1, "source": "manual", "url": record["sourceUrl"], "jobId": record["jobId"],
                     "blockedReason": "auth_required", "availabilityStatus": "unknown"})
        self.assertEqual(record["descriptionHash"], original_hash)
        self.assertEqual(record["availabilityStatus"], "unknown")
        self.assertEqual(record["currentStep"], "awaiting_manual")
        self.assertIsNotNone(record["retryAfter"])

    def test_quota_keeps_deferred_state(self):
        self.session.config["limits"]["max_accepted_jobs_per_run"] = 1
        # Session config mutation here is test-only; snapshot consistency is tested separately.
        a = synthetic_observation()
        b = copy.deepcopy(a)
        b.update(sourceJobId="456", url="https://example.com/synthetic/jobs/456")
        ids = self.session.ingest([a, b])
        self.assertEqual(sum(bool(self.session.records[i]["folderPath"]) for i in ids), 1)
        self.assertEqual(sum(self.session.records[i]["currentStep"] == "quota_deferred" for i in ids), 1)

    def test_rebuild_recovers_id_alias_and_backup(self):
        record = self.record()
        self.session.store.index_path.write_text("{bad", encoding="utf-8")
        self.assertEqual(self.session.store.rebuild(), 1)
        index = load_json(self.session.store.index_path)
        self.assertEqual(index["jobs"][record["jobId"]]["sourceAliases"], record["sourceAliases"])
        self.assertTrue(list(self.session.store.index_path.parent.glob("*.backup-*")))

    def test_interrupted_transaction_recovery_multiple_boundaries(self):
        for fail_at in (1, 2, 4, 6):
            with self.subTest(fail_at=fail_at):
                self.session.store.fault_after = fail_at
                with self.assertRaises(RuntimeError):
                    self.session.ingest([synthetic_observation()])
                self.session.store.fault_after = None
                self.session.store.recover()
                self.session = Session(self.root, self.session.cp["runId"])
                record = next(r for r in self.session.records.values() if r.get("folderPath"))
                self.assertEqual(load_json(self.session.store.index_path)["jobs"][record["jobId"]]["descriptionHash"], record["descriptionHash"])
                self.assertTrue(validate_manifest(self.root, self.session.run_dir / "cv-ready.json"))

    def test_single_writer_lock(self):
        with self.assertRaisesRegex(PipelineError, "Another writer"):
            with Store(self.root, self.config).locked():
                pass

    def test_manifest_path_traversal_rejected(self):
        self.record()
        path = self.session.run_dir / "cv-ready.json"
        data = load_json(path)
        data["jobs"][0]["descriptionPath"] = "../../../../outside.md"
        atomic_json(path, data)
        with self.assertRaises(PipelineError):
            validate_manifest(self.root, path)

    def test_input_evidence_required_and_not_fabricated(self):
        obs = synthetic_observation()
        obs["evidence"]["remoteScope"] = "This quote does not exist"
        with self.assertRaisesRegex(PipelineError, "Evidence quote"):
            self.ingest(obs)
        self.assertEqual(self.session.records, {})

    def test_coverage_not_complete_when_required_source_blocked(self):
        self.record()
        self.assertEqual(self.session.finish(), "partial")
        self.assertTrue(any(t["status"] == "blocked" for t in self.session.cp["tasks"]))

    def test_new_run_reuses_accepted_without_duplicate_folder(self):
        job_id = self.ingest()
        fresh = Session.start(self.root)
        self.assertEqual(fresh.ingest([synthetic_observation()]), [job_id])
        self.assertEqual(len(list((self.root / "jobs").iterdir())), 1)

    def test_score_deterministic(self):
        record = self.record()
        score = record["matchScore"]
        evaluate(record, self.session.config, record["fetchedAt"])
        self.assertAlmostEqual(score, record["matchScore"], places=2)

    def test_resume_uses_snapshot_when_current_config_invalid(self):
        (self.root / "config/search-config.yaml").write_text("broken: [", encoding="utf-8")
        resumed = Session(self.root, self.session.cp["runId"])
        self.assertEqual(resumed.config["schema_version"], 1)
        self.assertTrue(any("invalid" in note for note in resumed.cp["notes"]))

    def test_resume_locates_previous_runs_directory(self):
        self.config["output"]["runs_directory"] = "new-runs"
        self.save_config()
        resumed = Session(self.root, self.session.cp["runId"])
        self.assertEqual(resumed.run_dir, self.session.run_dir)

    def test_original_url_and_normalized_identity(self):
        obs = synthetic_observation()
        obs["url"] += "?gh_jid=123&utm_source=test"
        record = self.record(obs)
        self.assertIn("utm_source", record["sourceUrl"])
        self.assertNotIn("utm_source", record["sourceAliases"][0]["urls"][0])
        self.assertIn("gh_jid=123", record["sourceAliases"][0]["urls"][0])

    def test_crlf_source_integrity_roundtrip(self):
        obs = synthetic_observation()
        obs["description"] = obs["description"].replace("\n", "\r\n")
        obs["sourceContent"] = obs["description"]
        record = self.record(obs)
        self.assertEqual(validate_manifest(self.root, self.session.run_dir / "cv-ready.json"), [record["jobId"]])

    def test_fake_board_collection_and_detail_fetch(self):
        from seek_job.collection import collect
        self.config["company_boards"] = [{"source": "greenhouse", "company": "Synthetic", "board": "fixture"}]
        self.save_config()
        session = Session.start(self.root)
        post = {"id": 1, "title": "Senior .NET Developer", "absolute_url": "https://example.com/jobs/1",
                "company_name": "Synthetic", "content": "<p>SYNTHETIC JD: .NET, C# and Azure.</p>",
                "location": {"name": "Vietnam"}, "first_published": now()}

        class FakeClient:
            def __init__(self):
                self.calls = []
            def json(self, url):
                self.calls.append(url)
                return {"jobs": [post]} if url.endswith("/jobs") else post

        client = FakeClient()
        collect(session, client)
        self.assertEqual(len(client.calls), 2)
        record = next(iter(session.records.values()))
        self.assertEqual(record["descriptionStatus"], "complete")
        self.assertEqual(record["sourceAliases"][0]["sourceJobId"], "1")
        self.assertEqual(next(t for t in session.cp["tasks"] if t["kind"] == "board")["status"], "done")
        self.assertEqual(record["matchStatus"], "needs_review")  # geography/work mode not inferred

    def test_fake_fetch_failure_and_resume_capture(self):
        from seek_job.collection import collect
        record = self.record()
        old_hash = record["descriptionHash"]

        class FakeClient:
            def get(self, url):
                raise FetchError("auth_required")

        collect(self.session, FakeClient(), only_job=record["jobId"])
        self.assertEqual(record["descriptionHash"], old_hash)
        self.assertEqual(record["availabilityStatus"], "unknown")
        self.assertEqual(record["pendingManualAction"], "auth_required")
        self.ingest(synthetic_observation())
        self.assertIsNone(record["pendingManualAction"])
        self.assertTrue(self.session.cp["errors"][0].get("resolvedAt"))

    def test_task_records_external_time_and_budget(self):
        task = self.session.cp["tasks"][0]
        before = self.session.cp["elapsedSeconds"]
        self.session.mark_task(task["id"], "done", "Actual synthetic search", pages=1, found=0, active_seconds=12)
        self.assertGreaterEqual(self.session.cp["elapsedSeconds"], before + 12)
        self.assertEqual(task["status"], "done")
        with self.assertRaises(PipelineError):
            self.session.mark_task(self.session.cp["tasks"][1]["id"], "done", "No timing supplied")

    def test_corrupt_candidate_state_not_reset(self):
        self.record()
        state = load_json(self.session.store.candidates_path)
        record = next(iter(state["candidates"].values()))
        record["_description"] = 42
        atomic_json(self.session.store.candidates_path, state)
        with self.assertRaisesRegex(PipelineError, "state.candidate"):
            Session(self.root, self.session.cp["runId"])

    def test_source_tampering_prevents_handoff_and_rebuild(self):
        record = self.record()
        (self.root / record["folderPath"] / "source-content.txt").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(PipelineError, "source hash"):
            validate_manifest(self.root, self.session.run_dir / "cv-ready.json")
        with self.assertRaisesRegex(PipelineError, "Corrupt"):
            self.session.store.rebuild()

    def test_profile_gap_reader_never_modifies_cv_workspace(self):
        from seek_job.handoff import cv_context, profile_gaps
        self.config["cv_handoff"]["profile_gap_check"] = True
        cv_root = Path(self.config["cv_handoff"]["workspace"])
        relevant = [cv_root / "profile/skills.md", cv_root / "profile/preferences.md", cv_root / "cv.config.yaml"]
        hashes_before = [digest(p.read_text(encoding="utf-8")) for p in relevant]
        context = cv_context(self.root, self.config)
        record = self.record()
        gaps = profile_gaps(record, self.config, context)
        self.assertIn({"skill": ".NET", "tier": "unverified"}, [{"skill": g["skill"], "tier": g["tier"]} for g in gaps])
        self.assertEqual(hashes_before, [digest(p.read_text(encoding="utf-8")) for p in relevant])

    def test_public_duplicate_cannot_replace_official_jd(self):
        official = synthetic_observation()
        official["descriptionKind"] = "ats_api"
        record = self.record(official)
        original_hash = record["descriptionHash"]
        public = copy.deepcopy(official)
        public.update(source="linkedin", tenant="linkedin", sourceJobId="999",
                      url="https://www.linkedin.com/jobs/view/999", descriptionKind="public_page")
        public["description"] += "\nAggregator-specific changed requirements.\n"
        link = "Official posting: " + official["url"]
        public["sourceContent"] = public["description"] + "\n" + link
        public["linkedAliases"] = [{"alias": {
            "source": "manual", "tenant": official["tenant"], "sourceJobId": "123",
            "urls": [official["url"]]}, "quote": link}]
        self.assertEqual(self.ingest(public), record["jobId"])
        self.assertEqual(record["descriptionHash"], original_hash)
        self.assertEqual(record["descriptionSource"]["kind"], "ats_api")

    def test_stale_accepted_candidate_queued_for_refresh(self):
        record = self.record()
        record["availabilityCheckedAt"] = (dt(now()) - timedelta(days=3)).isoformat()
        self.session.refresh()
        self.assertEqual(record["currentStep"], "fetch")
        self.assertEqual(record["matchStatus"], "needs_review")

    def test_explicit_identity_decisions_survive_rebuild(self):
        a = synthetic_observation()
        b = copy.deepcopy(a)
        b.update(source="linkedin", tenant=None, sourceJobId=None, url="https://www.linkedin.com/jobs/view/456")
        ids = self.session.ingest([a, b])
        self.session.resolve_distinct(ids, "Synthetic explicit confirmation")
        self.session.store.rebuild()
        atomic_json(self.session.store.candidates_path, {"schemaVersion": 1, "candidates": {}})
        rebuilt = Session(self.root, self.session.cp["runId"])
        self.assertEqual(rebuilt.records[ids[0]]["distinctFrom"], [ids[1]])

    def test_finish_preserves_updated_run_outcome(self):
        obs = synthetic_observation()
        job_id = self.ingest(obs)
        fresh = Session.start(self.root)
        obs["description"] += "\nUpdated synthetic requirement: Docker.\n"
        obs["sourceContent"] = obs["description"]
        fresh.ingest([obs])
        fresh.finish()
        self.assertEqual(fresh.cp["outcomes"][job_id]["action"], "updated")
        self.assertIn(job_id, (fresh.run_dir / "updated-jobs.md").read_text(encoding="utf-8"))


class ContractTests(unittest.TestCase):
    def test_exported_schemas_current(self):
        for name, schema in SCHEMAS.items():
            self.assertEqual(load_json(REPO / "schemas" / f"{name}.schema.json"), schema)

    def test_yaml_duplicate_keys_and_types(self):
        with self.assertRaisesRegex(PipelineError, "Duplicate YAML"):
            yaml_read("a: 1\na: 2\n")
        self.assertEqual(yaml_read("date: 2026-09-18")["date"], "2026-09-18")
        from seek_job.common import validate
        config, _ = load_config(REPO, REPO / "tests/fixtures/search-config.yaml")
        config["limits"]["max_retries"] = "2"
        with self.assertRaisesRegex(PipelineError, "max_retries"):
            validate(config, CONFIG, "config")

    def test_url_identity_and_tracking(self):
        self.assertEqual(normalize_url("https://EXAMPLE.com/job?gh_jid=42&locale=en&utm_source=x"),
                         "https://example.com/job?gh_jid=42&locale=en")
        self.assertNotEqual(normalize_url("https://example.com/jobs?id=1"), normalize_url("https://example.com/jobs?id=2"))
        self.assertIn("#/job/123", normalize_url("https://example.com/#/job/123"))
        for url in ("https://u:p@example.com", "https://example.com?token=SECRET"):
            with self.assertRaises(PipelineError):
                normalize_url(url)

    def test_filename_and_path_windows(self):
        self.assertEqual(slug("CON"), "job-con")
        self.assertEqual(slug("Đặng <A>:*?"), "dang-a")
        self.assertLessEqual(len(slug("a" * 300)), 26)
        with self.assertRaises(PipelineError):
            inside(REPO, "../outside")

    def test_greenhouse_date_and_prospect(self):
        data = {"id": 1, "title": "Synthetic", "absolute_url": "https://example.com/job/1",
                "content": "<p>Requirements .NET</p><p>Benefits training</p>", "updated_at": now(),
                "location": {"name": "Vietnam"}}
        obs = greenhouse_post(data, {"board": "fixture", "company": "Synthetic"})
        self.assertNotIn("datePosted", obs["facts"])
        self.assertIn("Benefits training", obs["description"])
        data["internal_job_id"] = None
        with self.assertRaises(FetchError):
            greenhouse_post(data, {"board": "fixture", "company": "Synthetic"})

    def test_lever_preserves_all_sections(self):
        data = {"id": "x", "text": "Synthetic", "hostedUrl": "https://example.com/jobs/x",
                "description": "<p>Introduction</p>", "lists": [{"text": "Requirements", "content": "<li>C#</li>"}],
                "additional": "<p>Benefits</p>", "salaryDescription": "<p>Salary range $1-$2</p>",
                "workplaceType": "remote", "country": "VN", "categories": {"commitment": "Full-time"}}
        obs = lever_post(data, {"board": "fixture", "company": "Synthetic"})
        for phrase in ("Introduction", "Requirements", "C#", "Benefits", "Salary range"):
            self.assertIn(phrase, obs["description"])
        config, _ = load_config(REPO, REPO / "tests/fixtures/search-config.yaml")
        prepare(obs, config)

    def test_jsonld_hidden_description_not_complete(self):
        data = {"@type": "JobPosting", "title": "Synthetic", "hiringOrganization": {"name": "Example"}, "description": "Private JD"}
        raw = '<script type="application/ld+json">' + json.dumps(data) + "</script><h1>Login</h1>"
        result = structured_page(raw, {"sourceUrl": "https://example.com/job", "jobId": "test"})
        self.assertEqual(result["descriptionStatus"], "partial")
        self.assertEqual(result["availabilityStatus"], "unknown")

    def test_http_errors_never_assume_closed(self):
        from urllib.error import HTTPError
        config, _ = load_config(REPO, REPO / "tests/fixtures/search-config.yaml")
        limits = copy.deepcopy(config["limits"])
        limits["min_request_interval_seconds"] = 0
        for code, reason in ((403, "auth_required"), (429, "rate_limited"), (404, "not_found")):
            client = HttpClient(limits)
            error = HTTPError("https://example.com", code, "test", {}, None)
            with patch("seek_job.sources.public_url", side_effect=lambda u: u), patch.object(client.opener, "open", side_effect=error):
                with self.assertRaises(FetchError) as raised:
                    client.get("https://example.com")
                self.assertEqual(raised.exception.reason, reason)


if __name__ == "__main__":
    unittest.main()
