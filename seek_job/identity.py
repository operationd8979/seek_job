from __future__ import annotations

from difflib import SequenceMatcher

from .common import normalize_url


def keys(record):
    result = set()
    for alias in record.get("sourceAliases", []):
        if alias.get("sourceJobId") and alias.get("tenant"):
            result.add(("id", alias["source"], alias["tenant"].casefold(), alias["sourceJobId"]))
        for url in alias.get("urls", []):
            result.add(("url", normalize_url(url)))
    for field in ("sourceUrl", "canonicalUrl"):
        if record.get(field):
            result.add(("url", normalize_url(record[field])))
    return result


def incompatible_ids(a, b):
    for left in a.get("sourceAliases", []):
        for right in b.get("sourceAliases", []):
            if (left["source"], left.get("tenant")) == (right["source"], right.get("tenant")):
                if left.get("sourceJobId") and right.get("sourceJobId") and left["sourceJobId"] != right["sourceJobId"]:
                    return True
    return False


def strong_matches(incoming, records):
    incoming_keys = keys(incoming)
    return [r for r in records if not r.get("mergedInto")
            and not incompatible_ids(incoming, r) and incoming_keys.intersection(keys(r))]


def possible_matches(incoming, records):
    result = []
    company = (incoming.get("company") or "").casefold().strip()
    title = (incoming.get("jobTitle") or "").casefold().strip()
    for record in records:
        if record.get("mergedInto") or record["jobId"] == incoming["jobId"]:
            continue
        # Distinct requisitions from the same board are separate jobs, even if identical.
        if incompatible_ids(incoming, record):
            continue
        if not company or company != (record.get("company") or "").casefold().strip():
            continue
        same_name = title and title == (record.get("jobTitle") or "").casefold().strip()
        same_locations = incoming.get("locations") == record.get("locations")
        a, b = incoming.get("_description", ""), record.get("_description", "")
        similar = bool(a and b and SequenceMatcher(None, a[:20000], b[:20000], autojunk=False).ratio() >= .92)
        if (same_name and same_locations) or similar:
            result.append(record["jobId"])
    return result


def combine_aliases(*groups):
    combined = {}
    for group in groups:
        for alias in group:
            key = (alias["source"], alias.get("tenant"), alias.get("sourceJobId"))
            if key not in combined:
                combined[key] = {**alias, "urls": []}
            combined[key]["urls"] = sorted(set(combined[key]["urls"] + alias.get("urls", [])))
    return list(combined.values())
