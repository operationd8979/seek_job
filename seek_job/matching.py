from __future__ import annotations

import re

from .common import age_hours, config_hash, now


def contains(text, phrase):
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.I) is not None


def skill_evidence(text, names):
    for line in text.splitlines():
        for name in names:
            match = re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", line, re.I)
            if not match:
                continue
            # A conservative negation window prevents "no Angular experience required".
            window = line[max(0, match.start() - 30):match.end() + 45]
            if re.search(r"\b(no|not|without)\b|not required|not used", window, re.I):
                continue
            return line.strip()
    return None


def role_match(title, targets):
    # Ignore seniority words by looking for the configured role as a word sequence.
    normalize = lambda value: re.sub(r"[\s_-]+", " ", value.lower()).strip()
    title = normalize(title)
    return any(contains(title, normalize(target)) for target in targets)


def evaluate(record, config, at=None):
    at = at or now()
    body = record["_description"]
    title = record.get("jobTitle") or ""
    evidence = record.get("evidence", {})
    source = record.get("sourceUrl") or "local:user_supplied"
    geo = config["geography"]
    common = []

    def check(name, result, reason, quote=""):
        return {"name": name, "result": result, "reasonCode": reason, "quote": quote, "source": source}

    def add(name, result, reason, quote=""):
        common.append(check(name, result, reason, quote))

    for field, allowed in (("workMode", config["work_modes"]), ("employmentType", config["employment_types"])):
        value = record.get(field)
        add(field, "unknown" if value is None else "pass" if value in allowed else "fail",
            field + ("_unknown" if value is None else "_allowed" if value in allowed else "_excluded"),
            evidence.get(field, ""))
    locations = record.get("jobCountries", [])
    scope, eligible = record.get("remoteScope"), record.get("eligibleCountries")
    remote = record.get("workMode") == "remote"
    if remote and scope == "worldwide":
        add("jobCountries", "pass", "worldwide_remote", evidence.get("remoteScope", ""))
    elif locations:
        result = bool(set(locations).intersection(geo["job_countries"]))
        add("jobCountries", "pass" if result else "fail", "job_country_allowed" if result else "job_country_excluded",
            evidence.get("jobCountries", ""))
    else:
        add("jobCountries", "unknown", "job_country_unknown")
    if remote:
        if scope == "worldwide":
            add("remoteEligibility", "pass", "worldwide_remote", evidence.get("remoteScope", ""))
        elif scope == "restricted" and eligible:
            country = geo["work_from_country"]
            result = "unknown" if country is None else "pass" if country in eligible else "fail"
            add("remoteEligibility", result, "remote_country_" + result, evidence.get("eligibleCountries", ""))
        else:
            add("remoteEligibility", "unknown", "remote_scope_unknown")
    elif record.get("workMode") in ("hybrid", "on-site"):
        city = geo.get("work_from_city")
        local = (geo["work_from_country"] in locations and city
                 and any(contains(location, city) for location in record["locations"]))
        if local or geo["relocation_allowed"] is True:
            add("commute", "pass", "location_or_relocation_allowed", evidence.get("locations", ""))
        elif geo["relocation_allowed"] is False and geo["work_from_country"] and locations and geo["work_from_country"] not in locations:
            add("commute", "fail", "relocation_excluded", evidence.get("locations", ""))
        else:
            add("commute", "unknown", "commute_unknown")
    if geo["needs_sponsorship"] is True:
        val = record["sponsorship"]
        add("sponsorship", "unknown" if val is None else "pass" if val else "fail",
            "sponsorship_" + ("unknown" if val is None else "available" if val else "unavailable"),
            evidence.get("sponsorship", ""))
    if record["authorizationRequired"] is True:
        countries = eligible or locations
        authorized = bool(set(countries).intersection(geo["authorized_work_countries"]))
        can_sponsor = record["sponsorship"] is True and geo["needs_sponsorship"] is True
        add("authorization", "pass" if authorized or can_sponsor else "unknown",
            "authorization_supported" if authorized or can_sponsor else "authorization_unknown",
            evidence.get("authorizationRequired", ""))
    minimum = geo["minimum_timezone_overlap_hours"]
    if minimum is not None:
        overlap = record["timezoneOverlapHours"]
        add("timezoneOverlap", "unknown" if overlap is None else "pass" if overlap >= minimum else "fail",
            "timezone_overlap_unknown" if overlap is None else "timezone_overlap_checked",
            evidence.get("timezoneOverlapHours", ""))
    excluded = next((word for word in config["exclusions"]["title_keywords"] if contains(title, word)), None)
    add("excludedTitle", "fail" if excluded else "pass", "excluded_title" if excluded else "title_not_excluded",
        title if excluded else "")
    unpaid = re.search(r"\bunpaid\s+(?:role|position|internship|job)\b|\b(?:role|position|job)\s+is\s+unpaid\b", body, re.I)
    if config["exclusions"]["unpaid"]:
        salary_unpaid = isinstance(record["salary"], str) and contains(record["salary"], "unpaid")
        add("paid", "fail" if unpaid or salary_unpaid else "pass",
            "explicitly_unpaid" if unpaid or salary_unpaid else "no_unpaid_constraint",
            unpaid.group(0) if unpaid else evidence.get("salary", "") if salary_unpaid else "")
    date = record["datePosted"]
    freshness = 0.0
    if date:
        days = age_hours(date, at) / 24
        if days < -1:
            add("datePosted", "unknown", "future_date_requires_review", evidence.get("datePosted", ""))
        else:
            within = days <= config["freshness"]["posted_within_days"]
            add("datePosted", "pass" if within else "fail", "recent" if within else "too_old", evidence.get("datePosted", ""))
            freshness = max(0.0, min(1.0, 1 - max(0, days) / config["freshness"]["posted_within_days"]))
    else:
        add("datePosted", "unknown", "date_unknown")
    availability = record["availabilityStatus"]
    checked = record["availabilityCheckedAt"]
    if availability == "closed":
        add("availability", "fail", "closed", evidence.get("availabilityStatus", ""))
    elif availability != "open" or not checked:
        add("availability", "unknown", "availability_unknown")
    elif age_hours(checked, at) > config["freshness"]["max_availability_age_hours"]:
        add("availability", "unknown", "availability_stale")
    else:
        add("availability", "pass", "open_verified", evidence.get("availabilityStatus", ""))
    add("description", "pass" if record["descriptionStatus"] == "complete" else "unknown",
        "description_" + record["descriptionStatus"], record["completenessEvidence"])
    add("identity", "pass" if record["identityStatus"] == "resolved" else "unknown",
        "identity_" + record["identityStatus"])
    add("postingIdentity", "pass" if record.get("company") and title else "unknown",
        "posting_identity_present" if record.get("company") and title else "posting_identity_missing", title)

    evaluations, all_required, all_preferred, scores = [], set(), set(), []
    for profile in config["search_profiles"]:
        checks = list(common)
        explicit = record.get("roleMatches", {}).get(profile["id"])
        role_ok = role_match(title, profile["target_roles"])
        checks.append(check("role", explicit["result"] if explicit else "pass" if role_ok else "unknown",
                            "role_evidenced" if explicit else "role_title_match" if role_ok else "role_needs_review",
                            explicit["quote"] if explicit else title))
        level = record.get("level")
        level_quote = evidence.get("level", "")
        # Only explicitly stated level in title; never infer from years of experience.
        if not level:
            known = profile["levels"] + config["exclusions"]["hiring_levels"]
            found = [value for value in known if contains(title, value)]
            if len(set(found)) == 1:
                level, level_quote = found[0], title
        level_ok = level in profile["levels"] and level not in config["exclusions"]["hiring_levels"]
        checks.append(check("level", "unknown" if not level else "pass" if level_ok else "fail",
                            "level_unknown" if not level else "level_allowed" if level_ok else "level_excluded", level_quote))
        required, preferred = [], []
        for skill in profile["must_have_skills"]:
            quote = skill_evidence(body, [skill] + config["skill_aliases"].get(skill, []))
            checks.append(check("skill:" + skill, "pass" if quote else "unknown",
                                "skill_mentioned" if quote else "skill_not_evidenced", quote or ""))
            if quote:
                required.append(skill)
        for skill in profile["preferred_skills"]:
            if skill_evidence(body, [skill] + config["skill_aliases"].get(skill, [])):
                preferred.append(skill)
        has_failure = any(c["result"] == "fail" for c in checks)
        unknowns = [c for c in checks if c["result"] == "unknown"]
        reject_unknown = False
        for c in unknowns:
            policy = config["matching"]["unknown_hard_constraint_policy"]
            if c["name"].startswith("skill:"):
                policy = config["matching"]["unknown_required_skill_policy"]
            elif c["name"] == "datePosted":
                policy = config["freshness"]["unknown_date_policy"]
            # Acquisition/identity problems stay reviewable, not semantic rejections.
            elif c["name"] in ("description", "identity", "postingIdentity", "availability"):
                policy = "needs_review"
            reject_unknown = reject_unknown or policy == "reject"
        status = "rejected" if has_failure or reject_unknown else "needs_review" if unknowns else "accepted"
        evaluations.append({"profileId": profile["id"], "checks": checks, "status": status})
        all_required.update(required)
        all_preferred.update(preferred)
        coverage = len(preferred) / len(profile["preferred_skills"]) if profile["preferred_skills"] else 0.0
        weights = config["matching"]["ranking_weights"]
        score = (coverage * weights["preferred_skill_coverage"] + freshness * weights["freshness"])
        scores.append({"profileId": profile["id"], "preferredSkillCoverage": coverage,
                       "freshness": freshness, "score": round(score, 4), "eligible": status == "accepted"})
    accepted = [e["profileId"] for e in evaluations if e["status"] == "accepted"]
    record["matchStatus"] = ("accepted" if accepted else "needs_review"
                             if any(e["status"] == "needs_review" for e in evaluations) else "rejected")
    record["matchedProfileIds"] = accepted
    record["evaluations"] = evaluations
    record["matchedRequiredSkills"] = sorted(all_required)
    record["matchedPreferredSkills"] = sorted(all_preferred)
    eligible_scores = [s["score"] for s in scores if s["eligible"]]
    record["matchScore"] = max(eligible_scores, default=0)
    record["scoreBreakdown"] = {"profiles": scores, "unknownFreshnessValue": 0}
    record["evaluatedConfigHash"] = config_hash(config)
    record["reasonCodes"] = sorted({c["reasonCode"] for e in evaluations for c in e["checks"] if c["result"] != "pass"})
    return record
