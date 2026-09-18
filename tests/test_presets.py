"""Offline checks for the Hang/Dung presets and optional geographic filters."""
from pathlib import Path
import unittest

from seek_job.common import load_config
from seek_job.dryrun import synthetic_observation
from seek_job.engine import plan
from seek_job.matching import evaluate
from seek_job.observations import prepare

ROOT = Path(__file__).resolve().parents[1]


class PresetTests(unittest.TestCase):
    def config(self, person):
        return load_config(ROOT, ROOT / "storage" / f"search-config-{person}.yaml")[0]

    def job(self, person, title, location, mode, level, scope=None, countries=None):
        config = self.config(person)
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

    def test_hang_intern_tester_in_hcm(self):
        record, _ = self.job("hang", "Intern Tester", "HCM", "on-site", "intern")
        self.assertEqual(record["matchStatus"], "accepted")
        self.assertIn("hang_tester", record["matchedProfileIds"])

    def test_hang_react_frontend_does_not_require_all_frameworks(self):
        config = self.config("hang")
        _, obs = self.job("hang", "Junior Frontend Developer", "TP.HCM", "hybrid", "junior")
        obs["description"] = obs["description"].replace("Angular", "React").replace(".NET", "React")
        record = evaluate(prepare(obs, config), config)
        self.assertEqual(record["matchStatus"], "accepted")

    def test_hang_city_without_evidence_cannot_pass(self):
        record, _ = self.job("hang", "Junior Tester", "Hanoi", "on-site", "junior")
        self.assertNotEqual(record["matchStatus"], "accepted")
        self.assertIn("target_city_not_evidenced", record["reasonCodes"])

    def test_dung_hcm_senior_devops(self):
        record, _ = self.job("dung", "Senior DevOps Engineer", "Sài Gòn", "hybrid", "senior")
        self.assertEqual(record["matchStatus"], "accepted")

    def test_dung_remote_international_allows_vietnam(self):
        record, _ = self.job("dung", "Middle Full Stack Developer", "London", "remote", "middle",
                             "restricted", ["GB", "VN"])
        self.assertEqual(record["matchStatus"], "accepted")

    def test_dung_uk_only_remote_rejected(self):
        record, _ = self.job("dung", "Senior DevOps Engineer", "London", "remote", "senior",
                             "restricted", ["GB"])
        self.assertEqual(record["matchStatus"], "rejected")
        self.assertIn("remote_country_fail", record["reasonCodes"])

    def test_dung_unknown_remote_eligibility_review(self):
        record, _ = self.job("dung", "Senior DevOps Engineer", "London", "remote", "senior")
        self.assertEqual(record["matchStatus"], "needs_review")

    def test_mid_level_title_alias_not_ambiguous_with_mid(self):
        config = self.config("dung")
        _, obs = self.job("dung", "Mid-level Full Stack Developer", "HCM", "on-site", "middle")
        obs["facts"]["level"] = None
        record = evaluate(prepare(obs, config), config)
        self.assertEqual(record["matchStatus"], "accepted")

    def test_queries_cover_local_and_international_remote(self):
        capabilities = {"webSearch": True, "browserTakeover": False}
        hang = plan(self.config("hang"), capabilities)
        self.assertTrue(all("Ho Chi Minh City" in t["query"] for t in hang))
        dung = plan(self.config("dung"), capabilities)
        self.assertTrue(any("worldwide remote" in t["query"] for t in dung))
        self.assertTrue(any("Ho Chi Minh City" in t["query"] for t in dung))

    def test_outputs_isolated_between_people(self):
        hang, dung = self.config("hang"), self.config("dung")
        self.assertFalse(set(hang["output"].values()) & set(dung["output"].values()))


if __name__ == "__main__":
    unittest.main()
