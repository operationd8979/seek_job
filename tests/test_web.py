import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import yaml

from seek_job.agent_config import load_agent_config
from seek_job.common import PipelineError, atomic_json, load_config, load_json
from seek_job.dryrun import fixture_root, synthetic_observation
from seek_job.engine import Session
from seek_job.storage import Store
from seek_job.workflow import mutate, detail, dashboard, batch_path, operations
from seek_job.web import make_server, command, cv_prompt, reveal, Runner
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "fixture"
        config, _ = load_config(REPO, REPO / "tests/fixtures/search-config.yaml")
        self.config = copy.deepcopy(config)
        self.config["cv_handoff"].update(enabled=False, workspace="../cv")
        self.config["geography"]["work_from_country"] = "VN"
        fixture_root(self.root, self.config)
        self.cv = self.root.parent / "cv"
        (self.cv / "profile").mkdir(parents=True)
        (self.cv / "templates/ats-single-column").mkdir(parents=True)
        (self.cv / "profile/personal.md").write_text("- **Full name:** Synthetic Hang\n", encoding="utf-8")
        (self.cv / "cv.config.yaml").write_text("profile_root: profile\ntemplate_root: templates\noutput_root: applications\n", encoding="utf-8")
        with Store(self.root, self.config).locked():
            self.session = Session.start(self.root)
            self.job = self.session.ingest([synthetic_observation()])[0]
            self.session.finish()
        self.run = self.session.cp["runId"]

    def tearDown(self):
        self.temp.cleanup()

    def review(self, **extra):
        job = detail(self.root, self.run)["jobs"][0]
        return mutate(self.root, dict(action="review", runId=self.run, jobIds=[job["jobId"]],
                                     status="approved", fingerprints={job["jobId"]: job["fingerprint"]}, **extra))

    def cv_payload(self):
        return dict(action="queue", kind="cv", runId=self.run, jobIds=[self.job], profile="profile",
                    confirmedName="Synthetic Hang", template="ats-single-column")

    def test_approve_and_batch_pin_exact_jd_and_profile(self):
        self.review()
        op = mutate(self.root, self.cv_payload())
        batch = load_json(batch_path(self.root, op["batchId"]))
        self.assertEqual(batch["jobs"][0]["description"], synthetic_observation(self.session.records[self.job]["datePosted"])["description"])
        self.assertEqual(batch["profileName"], "Synthetic Hang")
        self.assertFalse((self.cv / "applications").exists())  # queue is not generation
        self.assertIn("raw/job.md", cv_prompt(batch))
        self.assertNotIn(batch["jobs"][0]["description"], cv_prompt(batch))

    def test_unapproved_and_wrong_profile_block_cv(self):
        with self.assertRaises(PipelineError):
            mutate(self.root, self.cv_payload())
        self.review()
        with self.assertRaises(PipelineError):
            mutate(self.root, dict(self.cv_payload(), confirmedName="Someone else"))
        self.assertFalse(operations(self.root))

    def test_named_profiles_share_job_approval_but_pin_separate_candidates(self):
        # Exercise migration from a flat default to profile/<name>.
        first = self.cv / "profile/hang"
        second = self.cv / "profile/dung"
        first.mkdir()
        second.mkdir()
        (self.cv / "profile/personal.md").rename(first / "personal.md")
        (second / "personal.md").write_text("- **Full name:** Synthetic Dung\n", encoding="utf-8")
        (self.cv / "cv.config.yaml").write_text(
            "profile_root: profile/hang\ntemplate_root: templates\noutput_root: applications\n", encoding="utf-8")
        profiles = detail(self.root, self.run)["cv"]["profiles"]
        self.assertEqual([p["path"] for p in profiles], ["profile/hang", "profile/dung"])
        self.review()
        decisions = detail(self.root, self.run)["reviewEvents"]
        batches = []
        for profile, name in (("profile/hang", "Synthetic Hang"), ("profile/dung", "Synthetic Dung")):
            op = mutate(self.root, dict(self.cv_payload(), profile=profile, confirmedName=name))
            batch = load_json(batch_path(self.root, op["batchId"]))
            self.assertEqual(batch["profile"], profile)
            self.assertEqual(batch["profileName"], name)
            self.assertIn(profile, cv_prompt(batch))
            batches.append(batch)
            mutate(self.root, dict(action="operation-update", id=op["id"], status="cancelled"))
        self.assertNotEqual(batches[0]["profileHash"], batches[1]["profileHash"])
        self.assertNotEqual(batches[0]["jobs"][0]["outputDir"], batches[1]["jobs"][0]["outputDir"])
        self.assertEqual(batches[0]["jobs"][0]["decision"], batches[1]["jobs"][0]["decision"])
        self.assertEqual(detail(self.root, self.run)["reviewEvents"], decisions)

    def test_legacy_additional_profile_directory_remains_selectable(self):
        extra = self.cv / "profiles/other"
        extra.mkdir(parents=True)
        (extra / "personal.md").write_text("- **Full name:** Synthetic Other\n", encoding="utf-8")
        self.assertEqual({p["path"] for p in detail(self.root, self.run)["cv"]["profiles"]},
                         {"profile", "profiles/other"})

    def test_missing_jd_cannot_be_approved_even_with_override(self):
        with Store(self.root, self.config).locked():
            s = Session(self.root, self.run)
            other = s.ingest([{"schemaVersion": 1, "source": "manual", "url": "https://example.com/blocked"}])[0]
        job = next(j for j in detail(self.root, self.run)["jobs"] if j["jobId"] == other)
        with self.assertRaises(PipelineError):
            mutate(self.root, dict(action="review", runId=self.run, jobIds=[other], status="approved",
                                   fingerprints={other: job["fingerprint"]}, override=True, note="want it"))

    def test_override_requires_reason_and_keeps_match(self):
        with Store(self.root, self.config).locked():
            s = Session(self.root, self.run)
            o = synthetic_observation()
            o["facts"]["datePosted"] = None
            o["evidence"].pop("datePosted")
            s.ingest([o])
        # Source merging preserves known facts; use an old explicit date to fail freshness.
        with Store(self.root, self.config).locked():
            s = Session(self.root, self.run)
            s.ingest([synthetic_observation("2020-01-01T00:00:00Z")])
        with self.assertRaises(PipelineError):
            self.review()
        with self.assertRaises(PipelineError):
            self.review(override=True, note="")
        self.review(override=True, note="Explicitly retain this historical example.")
        j = detail(self.root, self.run)["jobs"][0]
        self.assertEqual(j["review"]["status"], "approved")
        self.assertNotEqual(j["matchStatus"], "accepted")

    def test_jd_change_invalidates_approval(self):
        self.review()
        with Store(self.root, self.config).locked():
            s = Session(self.root, self.run)
            o = synthetic_observation()
            o["description"] += "\nUpdated responsibilities."
            o["sourceContent"] += "\nUpdated responsibilities."
            s.ingest([o])
        self.assertEqual(detail(self.root, self.run)["jobs"][0]["review"]["status"], "stale")
        with self.assertRaises(PipelineError):
            mutate(self.root, self.cv_payload())

    def test_history_snapshot_survives_new_run(self):
        before = detail(self.root, self.run)["jobs"][0]["_description"]
        with Store(self.root, self.config).locked():
            newer = Session.start(self.root)
            o = synthetic_observation()
            o["description"] += "\nNew run description."
            o["sourceContent"] += "\nNew run description."
            newer.ingest([o])
        self.assertEqual(detail(self.root, self.run)["jobs"][0]["_description"], before)

    def test_delete_restore_keeps_shared_jobs_and_cv(self):
        candidates = self.root / self.config["output"]["candidates_file"]
        before = candidates.read_bytes()
        mutate(self.root, {"action": "delete", "runId": self.run})
        self.assertEqual(len(dashboard(self.root)["runs"]), 0)
        self.assertEqual(len(dashboard(self.root)["trash"]), 1)
        self.assertEqual(before, candidates.read_bytes())
        mutate(self.root, {"action": "restore", "runId": self.run})
        self.assertEqual(len(dashboard(self.root)["runs"]), 1)

    def test_delete_cv_batch_removes_only_its_artifacts_and_keeps_approval(self):
        self.review()
        first = mutate(self.root, self.cv_payload())
        first_batch = load_json(batch_path(self.root, first["batchId"]))
        mutate(self.root, {"action": "cv-prepare", "id": first["batchId"]})
        output = Path(first_batch["outputRoot"])
        pdf = Path(first_batch["jobs"][0]["outputDir"]) / "Synthetic_CV.pdf"
        pdf.write_bytes(b"synthetic PDF fixture")
        mutate(self.root, {"action": "batch-result", "id": first["batchId"], "status": "completed"})
        mutate(self.root, {"action": "operation-update", "id": first["id"], "status": "completed"})
        second = mutate(self.root, self.cv_payload())
        with self.assertRaises(PipelineError):
            mutate(self.root, {"action": "cv-delete", "id": first["batchId"]})
        mutate(self.root, {"action": "batch-result", "id": second["batchId"], "status": "failed"})
        mutate(self.root, {"action": "operation-update", "id": second["id"], "status": "failed"})
        log = self.root / "state/ui/operations" / (first["id"] + ".log")
        log.write_text("synthetic agent log", encoding="utf-8")
        result = mutate(self.root, {"action": "cv-delete", "id": first["batchId"]})
        self.assertEqual(result["deletedBatch"], first["batchId"])
        self.assertFalse(output.exists())
        self.assertFalse(batch_path(self.root, first["batchId"]).exists())
        self.assertFalse(log.exists())
        self.assertFalse((self.root / "state/ui/operations" / (first["id"] + ".json")).exists())
        self.assertTrue(batch_path(self.root, second["batchId"]).exists())
        self.assertEqual(detail(self.root, self.run)["jobs"][0]["review"]["status"], "approved")

    def test_delete_cv_batch_rejects_tampered_output_path(self):
        self.review()
        op = mutate(self.root, self.cv_payload())
        mutate(self.root, {"action": "batch-result", "id": op["batchId"], "status": "failed"})
        mutate(self.root, {"action": "operation-update", "id": op["id"], "status": "failed"})
        path = batch_path(self.root, op["batchId"])
        batch = load_json(path)
        batch["outputRoot"] = str(self.cv / "profile")
        atomic_json(path, batch)
        with self.assertRaises(PipelineError):
            mutate(self.root, {"action": "cv-delete", "id": op["batchId"]})
        self.assertTrue((self.cv / "profile/personal.md").exists())
        self.assertTrue(path.exists())

    def test_reveal_opens_job_folder_and_refuses_unknown_or_missing(self):
        self.review()
        op = mutate(self.root, self.cv_payload())
        batch = load_json(batch_path(self.root, op["batchId"]))
        with self.assertRaises(PipelineError):
            reveal(self.root, {"batch": op["batchId"]})
        mutate(self.root, {"action": "cv-prepare", "id": op["batchId"]})
        with patch("seek_job.web.subprocess.Popen") as popen:
            reveal(self.root, {"batch": op["batchId"], "job": self.job})
            self.assertEqual(popen.call_args[0][0][-1], batch["jobs"][0]["outputDir"])
            reveal(self.root, {"batch": op["batchId"]})
            self.assertEqual(popen.call_args[0][0][-1], batch["outputRoot"])
        with self.assertRaises(PipelineError):
            reveal(self.root, {"batch": op["batchId"], "job": "unknown-job"})
        with self.assertRaises(PipelineError):
            reveal(self.root, {"batch": "../config", "job": self.job})

    def test_path_traversal_rejected(self):
        for run in ("../config", "..", "runs/anything"):
            with self.assertRaises(PipelineError):
                mutate(self.root, {"action": "delete", "runId": run})

    def test_single_operation_and_active_run_no_edit(self):
        op = mutate(self.root, {"action": "queue", "kind": "collect", "runId": self.run})
        with self.assertRaises(PipelineError):
            mutate(self.root, {"action": "queue", "kind": "collect", "runId": self.run})
        with self.assertRaises(PipelineError):
            mutate(self.root, {"action": "delete", "runId": self.run})
        mutate(self.root, {"action": "operation-update", "id": op["id"], "status": "cancelled"})
        self.review()

    def test_preset_start_does_not_modify_active_config(self):
        path = self.root / "storage/search-config-tester-frontend-hcm.yaml"
        path.parent.mkdir()
        config = copy.deepcopy(self.config)
        config["search_profiles"][0]["id"] = "tester"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        before = (self.root / "config/search-config.yaml").read_bytes()
        op = mutate(self.root, {"action": "queue", "kind": "search", "preset": "tester-frontend-hcm"})
        self.assertEqual(detail(self.root, op["runId"])["config"]["search_profiles"][0]["id"], "tester")
        self.assertEqual(before, (self.root / "config/search-config.yaml").read_bytes())
        self.assertFalse(detail(self.root, op["runId"])["checkpoint"]["capabilities"]["webSearch"])

    def test_cv_prepare_rechecks_profile_and_wont_overwrite(self):
        self.review()
        op = mutate(self.root, self.cv_payload())
        (self.cv / "profile/personal.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(PipelineError):
            mutate(self.root, {"action": "cv-prepare", "id": op["batchId"]})

    def test_batch_operations_never_launch_external_process(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Do not launch during queue")):
            self.review()
            mutate(self.root, self.cv_payload())

    def test_command_uses_argv_and_sandbox(self):
        with patch("shutil.which", return_value="codex.exe"):
            args = command(self.root, True)
            cv_args = command(self.cv)
        self.assertIn("--search", args)
        self.assertNotIn("--search", cv_args)
        for invocation in (args, cv_args):
            self.assertEqual(invocation[invocation.index("-m") + 1], "gpt-5.6-sol")
            self.assertIn('model_reasoning_effort="medium"', invocation)
        self.assertIn("workspace-write", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertEqual(args[-1], "-")

    def test_agent_config_controls_search_and_cv_and_rejects_invalid_values(self):
        path = self.root / "config/agent-config.yaml"
        path.write_text("model: gpt-5.6-terra\nreasoning_effort: low\n", encoding="utf-8")
        settings = load_agent_config(self.root)
        with patch("shutil.which", return_value="codex.exe"):
            search = command(self.root, True, settings)
            cv = command(self.cv, agent_config=settings)
        for invocation in (search, cv):
            self.assertEqual(invocation[invocation.index("-m") + 1], "gpt-5.6-terra")
            self.assertIn('model_reasoning_effort="low"', invocation)
        self.assertEqual(dashboard(self.root)["agent"], settings)
        path.write_text("model: [invalid]\nreasoning_effort: low\n", encoding="utf-8")
        with self.assertRaises(PipelineError):
            load_agent_config(self.root)

    def test_failed_runner_is_recorded_and_releases_slot(self):
        op = mutate(self.root, {"action": "queue", "kind": "collect", "runId": self.run})
        runner = Runner(self.root)
        with patch("seek_job.web.action", side_effect=lambda root, data: mutate(root, data)), \
             patch.object(runner, "execute", return_value=17):
            runner.work(op)
        saved = operations(self.root)[0]
        self.assertEqual(saved["status"], "failed")
        self.assertIn("17", saved["error"])
        self.assertIsNone(runner.process)

    def test_failed_cv_agent_marks_batch_failed_without_rendering(self):
        self.review()
        op = mutate(self.root, self.cv_payload())
        runner = Runner(self.root)
        with patch("seek_job.web.action", side_effect=lambda root, data: mutate(root, data)), \
             patch.object(runner, "execute", return_value=17) as execute:
            runner.work(op)
        saved = operations(self.root)[0]
        batch = load_json(batch_path(self.root, op["batchId"]))
        self.assertEqual(saved["status"], "failed")
        self.assertIn("17", saved["error"])
        self.assertEqual(batch["status"], "failed")
        self.assertEqual(execute.call_count, 1)

    def test_cv_worker_verifies_render_and_build_before_publishing(self):
        self.review()
        op = mutate(self.root, self.cv_payload())
        batch = load_json(batch_path(self.root, op["batchId"]))
        out = Path(batch["jobs"][0]["outputDir"])
        runner = Runner(self.root)
        calls = []
        def fake_execute(args, log, prompt=None, cwd=None):
            calls.append(args)
            if args == ["synthetic-agent"]:
                (out / "raw/plan.json").write_text(json.dumps({"template": "ats-single-column",
                    "job": {"title": batch["jobs"][0]["jobTitle"]}}), encoding="utf-8")
            elif "render_cv.py" in args[1]:
                self.assertEqual(args[args.index("--job-title") + 1], batch["jobs"][0]["jobTitle"])
                (out / "raw/cv.tex").write_text("SYNTHETIC ONLY", encoding="utf-8")
            elif "build_and_validate.py" in args[1]:
                self.assertNotIn("--max-pages", args)
                (out / "Synthetic_CV.pdf").write_bytes(b"%PDF SYNTHETIC TEST ONLY")
            return 0
        with patch("seek_job.web.action", side_effect=lambda root, data: mutate(root, data)), \
             patch("seek_job.web.command", return_value=["synthetic-agent"]), \
             patch.object(runner, "execute", side_effect=fake_execute):
            runner.work(op)
        result = load_json(batch_path(self.root, batch["id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(calls), 3)
        self.assertTrue(result["results"][0]["pdfHashes"])
        self.assertEqual((out / "raw/job.md").read_text(encoding="utf-8").split("\n\n", 1)[1], batch["jobs"][0]["description"])

    def test_cv_worker_uses_approved_title_even_if_plan_shortens_it(self):
        self.review()
        op = mutate(self.root, self.cv_payload())
        batch = load_json(batch_path(self.root, op["batchId"]))
        out = Path(batch["jobs"][0]["outputDir"])
        runner = Runner(self.root)
        def fake_execute(args, log, prompt=None, cwd=None):
            if args == ["synthetic-agent"]:
                (out / "raw/plan.json").write_text(json.dumps({
                    "template": "ats-single-column", "job": {"title": "Shortened title"}
                }), encoding="utf-8")
            elif "render_cv.py" in args[1]:
                self.assertEqual(args[args.index("--job-title") + 1], batch["jobs"][0]["jobTitle"])
                (out / "raw/cv.tex").write_text("SYNTHETIC ONLY", encoding="utf-8")
            elif "build_and_validate.py" in args[1]:
                (out / "Synthetic_CV.pdf").write_bytes(b"%PDF SYNTHETIC TEST ONLY")
            return 0
        with patch("seek_job.web.action", side_effect=lambda root, data: mutate(root, data)), \
             patch("seek_job.web.command", return_value=["synthetic-agent"]), \
             patch.object(runner, "execute", side_effect=fake_execute) as execute:
            runner.work(op)
        result = load_json(batch_path(self.root, batch["id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(execute.call_count, 3)

    def test_process_tree_is_reaped_without_agent(self):
        from seek_job.web import ProcessTree
        import os
        if os.name != "nt":
            self.skipTest("Windows job object lifecycle")
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                creationflags=subprocess.CREATE_NO_WINDOW)
        tree = ProcessTree(proc)
        tree.close()
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.poll())

    def test_http_origin_csrf_and_readonly_get(self):
        server = make_server(self.root, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            state = json.load(urlopen(base + "/api/state"))
            self.assertEqual(len(state["runs"]), 1)
            self.assertEqual(state["agent"]["model"], "gpt-5.6-sol")
            self.assertIn(b"YOUR CAREER WORKSPACE", urlopen(base).read())
            data = json.dumps({"action": "delete", "runId": self.run}).encode()
            with self.assertRaises(HTTPError) as ctx:
                urlopen(Request(base + "/api/action", data=data, headers={"Origin": "https://attacker.example"}))
            self.assertEqual(ctx.exception.code, 403)
            self.review()
            op = mutate(self.root, self.cv_payload())
            mutate(self.root, {"action": "cv-prepare", "id": op["batchId"]})
            reveal_body = json.dumps({"batch": op["batchId"], "job": self.job}).encode()
            reveal_request = Request(base + "/api/reveal", data=reveal_body,
                                     headers={"Origin": base, "X-CSRF-Token": state["csrf"], "Content-Type": "application/json"})
            with patch("seek_job.web.subprocess.Popen"):
                self.assertTrue(json.load(urlopen(reveal_request))["opened"])
            mutate(self.root, {"action": "batch-result", "id": op["batchId"], "status": "failed"})
            mutate(self.root, {"action": "operation-update", "id": op["id"], "status": "failed"})
            delete_cv = json.dumps({"action": "cv-delete", "id": op["batchId"]}).encode()
            cv_request = Request(base + "/api/action", data=delete_cv,
                                 headers={"Origin": base, "X-CSRF-Token": state["csrf"], "Content-Type": "application/json"})
            self.assertEqual(json.load(urlopen(cv_request))["deletedBatch"], op["batchId"])
            self.assertEqual(json.load(urlopen(base + "/api/state"))["batches"], [])
            request = Request(base + "/api/action", data=data,
                              headers={"Origin": base, "X-CSRF-Token": state["csrf"], "Content-Type": "application/json"})
            self.assertEqual(json.load(urlopen(request))["deleted"], self.run)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
