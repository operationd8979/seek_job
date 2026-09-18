"""Offline checks for the search-goal presets and optional geographic filters."""
from pathlib import Path
import unittest

from seek_job.common import load_config
from seek_job.dryrun import synthetic_observation
from seek_job.engine import plan
from seek_job.matching import evaluate
from seek_job.observations import prepare

ROOT = Path(__file__).resolve().parents[1]


class PresetTests(unittest.TestCase):
    def config(self, goal):
        return load_config(ROOT, ROOT / "storage" / f"search-config-{goal}.yaml")[0]

    def job(self, goal, title, location, mode, level, scope=None, countries=None):
        config = self.config(goal)
        obs = synthetic_observation()
        obs["jobTitle"] = title
        obs["facts"].update(locations=[location], workMode=mode, level=level,
                            remoteScope=scope, eligibleCountries=countries,
                            jobCountries=["GB"] if location == "London" else ["VN"])
        obs["sourceContent"] += f"\n{title}\nLocation: {location}\nMode: {mode}\nLevel: {level}\n"
        if scope:
            obs["sourceContent"] += f"Remote scope: {scope}; eligible countries: {countries}\n"
        obs["evidence"].update(locations=f"Location: {location}", jobCountries=f"Location: {location}",
                               workMode=f"Mode: {mode}", level=f"Level: {level}")
        if scope:
            for field in ("remoteScope", "eligibleCountries"):
                obs["evidence"][field] = f"Remote scope: {scope}; eligible countries: {countries}"
        return evaluate(prepare(obs, config), config), obs

    def test_tester_frontend_intern_tester_in_hcm(self):
        record, _ = self.job("tester-frontend-hcm", "Intern Tester", "HCM", "on-site", "intern")
        self.assertEqual(record["matchStatus"], "accepted")
        self.assertIn("tester", record["matchedProfileIds"])

    def test_tester_frontend_react_frontend_does_not_require_all_frameworks(self):
        config = self.config("tester-frontend-hcm")
        _, obs = self.job("tester-frontend-hcm", "Junior Frontend Developer", "TP.HCM", "hybrid", "junior")
        obs["description"] = obs["description"].replace("Angular", "React").replace(".NET", "React")
        record = evaluate(prepare(obs, config), config)
        self.assertEqual(record["matchStatus"], "accepted")

    def test_tester_frontend_city_without_evidence_cannot_pass(self):
        record, _ = self.job("tester-frontend-hcm", "Junior Tester", "Hanoi", "on-site", "junior")
        self.assertNotEqual(record["matchStatus"], "accepted")
        self.assertIn("target_city_not_evidenced", record["reasonCodes"])

    def test_fullstack_devops_hcm_senior_devops(self):
        record, _ = self.job("fullstack-devops-hcm-remote", "Senior DevOps Engineer", "Sài Gòn", "hybrid", "senior")
        self.assertEqual(record["matchStatus"], "accepted")

    def test_fullstack_devops_remote_international_allows_vietnam(self):
        record, _ = self.job("fullstack-devops-hcm-remote", "Middle Full Stack Developer", "London", "remote", "middle",
                             "restricted", ["GB", "VN"])
        self.assertEqual(record["matchStatus"], "accepted")

    def test_fullstack_devops_uk_only_remote_rejected(self):
        record, _ = self.job("fullstack-devops-hcm-remote", "Senior DevOps Engineer", "London", "remote", "senior",
                             "restricted", ["GB"])
        self.assertEqual(record["matchStatus"], "rejected")
        self.assertIn("remote_country_fail", record["reasonCodes"])

    def test_fullstack_devops_unknown_remote_eligibility_review(self):
        record, _ = self.job("fullstack-devops-hcm-remote", "Senior DevOps Engineer", "London", "remote", "senior")
        self.assertEqual(record["matchStatus"], "needs_review")

    def test_mid_level_title_alias_not_ambiguous_with_mid(self):
        config = self.config("fullstack-devops-hcm-remote")
        _, obs = self.job("fullstack-devops-hcm-remote", "Mid-level Full Stack Developer", "HCM", "on-site", "middle")
        obs["facts"]["level"] = None
        record = evaluate(prepare(obs, config), config)
        self.assertEqual(record["matchStatus"], "accepted")

    def test_queries_cover_local_and_international_remote(self):
        capabilities = {"webSearch": True, "browserTakeover": False}
        tester_frontend = plan(self.config("tester-frontend-hcm"), capabilities)
        self.assertTrue(all("Ho Chi Minh City" in t["query"] for t in tester_frontend))
        fullstack_devops = plan(self.config("fullstack-devops-hcm-remote"), capabilities)
        self.assertTrue(any("worldwide remote" in t["query"] for t in fullstack_devops))
        self.assertTrue(any("Ho Chi Minh City" in t["query"] for t in fullstack_devops))

    def test_outputs_isolated_between_search_goals(self):
        tester_frontend, fullstack_devops = self.config("tester-frontend-hcm"), self.config("fullstack-devops-hcm-remote")
        self.assertFalse(set(tester_frontend["output"].values()) & set(fullstack_devops["output"].values()))


if __name__ == "__main__":
    unittest.main()
