"""Read-only public adapters. No login automation, cookies, application POSTs or search scraping."""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .common import PipelineError, body_normalize, normalize_url, now


class FetchError(PipelineError):
    def __init__(self, reason, retry_after=None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


def public_url(url):
    url = normalize_url(url)
    host = urlsplit(url).hostname
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        raise FetchError("browser_required")
    try:
        addresses = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise FetchError("fetch_failed") from exc
    if not addresses or any(not ipaddress.ip_address(addr[4][0]).is_global for addr in addresses):
        raise PipelineError("HTTP adapter only permits public Internet addresses")
    return url


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, public_url(newurl))


class HttpClient:
    def __init__(self, limits, on_request=None):
        self.limits = limits
        self.last_request = 0
        self.on_request = on_request
        self.opener = build_opener(PublicRedirects())

    def get(self, url):
        url = public_url(url)
        for attempt in range(self.limits["max_retries"] + 1):
            delay = self.limits["min_request_interval_seconds"] - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            if self.on_request:
                self.on_request(url)
            self.last_request = time.monotonic()
            try:
                request = Request(url, headers={"User-Agent": "SeekJob/1.0 (manual personal job discovery)",
                                                "Accept": "application/json, text/html;q=0.9"})
                with self.opener.open(request, timeout=self.limits["request_timeout_seconds"]) as response:
                    raw = response.read(5_000_001)
                    if len(raw) > 5_000_000:
                        raise FetchError("fetch_failed")
                    return raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            except HTTPError as exc:
                if exc.code in (401, 403):
                    raise FetchError("auth_required") from exc
                if exc.code == 429:
                    header = exc.headers.get("Retry-After")
                    retry = None
                    if header:
                        try:
                            retry = max(0, int(header))
                        except ValueError:
                            try:
                                retry = max(0, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
                            except (ValueError, TypeError):
                                pass
                    # Defer to checkpoint instead of waiting through a server cooldown.
                    raise FetchError("rate_limited", retry) from exc
                if exc.code in (404, 410):
                    raise FetchError("not_found") from exc
                if exc.code < 500 or attempt == self.limits["max_retries"]:
                    raise FetchError("fetch_failed") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt == self.limits["max_retries"]:
                    raise FetchError("fetch_failed") from exc
            time.sleep(min(60, self.limits["retry_backoff_seconds"] * 2 ** attempt))
        raise FetchError("fetch_failed")

    def json(self, url):
        try:
            return json.loads(self.get(url))
        except ValueError as exc:
            raise FetchError("fetch_failed") from exc


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif not self.skip and tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "section"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1
        elif not self.skip and tag in ("p", "div", "li", "h1", "h2", "h3", "section"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain(value):
    value = html.unescape(value or "")
    parser = TextParser()
    parser.feed(value)
    text = "".join(parser.parts)
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    return body_normalize(text)


def base_observation(source, url, tenant, job_id, company, title):
    return {"schemaVersion": 1, "source": source, "url": url, "tenant": tenant,
            "sourceJobId": str(job_id) if job_id is not None else None,
            "company": company, "jobTitle": title, "facts": {}, "evidence": {}, "capturedAt": now()}


def add_fact(obs, key, value):
    if value not in (None, "", []):
        obs["facts"][key] = value
        # Structured data provenance: the precise key and value in the whitelisted payload.
        obs["evidence"][key] = json.dumps({key: value}, ensure_ascii=False)


def finish_ats(obs, body, raw_parts):
    obs.update(description=body, descriptionStatus="complete" if body.strip() else "unavailable",
               descriptionKind="ats_api", availabilityStatus="open", availabilityCheckedAt=obs["capturedAt"],
               completenessEvidence="Full public ATS posting payload, including all provided description sections.")
    marker = "Posting returned by the public published-jobs ATS endpoint."
    obs["evidence"]["availabilityStatus"] = marker
    obs["sourceContent"] = "\n".join([marker, *raw_parts, *obs["evidence"].values()])
    return obs


def greenhouse_post(data, board):
    obs = base_observation("greenhouse", data["absolute_url"], board["board"], data["id"],
                           data.get("company_name") or board["company"], data["title"])
    if data.get("internal_job_id", "unknown") is None:
        raise FetchError("not_found")  # prospect/general application, not a job requisition
    raw = data.get("content", "")
    disclaimer = data.get("ai_disclaimer") if data.get("include_ai_disclaimer") else None
    if disclaimer and disclaimer not in raw:
        raw += "\n" + disclaimer
    add_fact(obs, "locations", [data["location"]["name"]] if data.get("location", {}).get("name") else None)
    add_fact(obs, "datePosted", data.get("first_published"))  # never updated_at
    obs = finish_ats(obs, plain(raw), [raw])
    deadline = data.get("application_deadline")
    if deadline:
        from .common import dt
        try:
            closed = dt(deadline) < dt(obs["capturedAt"])
        except ValueError:
            closed = False
        if closed:
            obs["availabilityStatus"] = "closed"
            marker = f"application_deadline: {deadline}"
            obs["sourceContent"] += "\n" + marker
            obs["evidence"]["availabilityStatus"] = marker
    return obs


def lever_post(data, board):
    obs = base_observation("lever", data["hostedUrl"], board["board"], data["id"], board["company"], data["text"])
    sections = [data.get("description") or data.get("descriptionPlain") or ""]
    for entry in data.get("lists", []):
        sections.extend([entry.get("text", ""), entry.get("content", "")])
    sections.extend([data.get("additional") or data.get("additionalPlain") or "",
                     data.get("salaryDescription") or data.get("salaryDescriptionPlain") or ""])
    if data.get("salaryRange"):
        sections.append(json.dumps(data["salaryRange"], ensure_ascii=False))
        add_fact(obs, "salary", data["salaryRange"])
    categories = data.get("categories", {})
    locations = categories.get("allLocations") or ([categories["location"]] if categories.get("location") else [])
    add_fact(obs, "locations", locations)
    country = data.get("country")
    if isinstance(country, str) and re.fullmatch("[A-Z]{2}", country):
        add_fact(obs, "jobCountries", [country])
    workplace = data.get("workplaceType")
    if workplace in ("remote", "hybrid", "on-site"):
        add_fact(obs, "workMode", workplace)
    commitment = categories.get("commitment", "").lower()
    if commitment:
        add_fact(obs, "employmentType", commitment.replace("full time", "full-time").replace("part time", "part-time"))
    # Lever's public schema does not promise a posting date; leave unknown.
    raw = "\n\n".join(sections)
    return finish_ats(obs, plain(raw), [raw])


def board_page(client, board, offset=0, limit=30):
    token = quote(board["board"], safe="")
    if board["source"] == "greenhouse":
        data = client.json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
        jobs = data["jobs"]
        candidates = [base_observation("greenhouse", p["absolute_url"], board["board"], p["id"], board["company"], p["title"])
                      for p in jobs if p.get("internal_job_id", "unknown") is not None]
        return candidates[offset:offset + limit], offset + limit < len(candidates)
    host = "api.eu.lever.co" if board.get("region") == "eu" else "api.lever.co"
    data = client.json(f"https://{host}/v0/postings/{token}?mode=json&skip={offset}&limit={limit}")
    if not isinstance(data, list):
        raise FetchError("fetch_failed")
    return [lever_post(item, board) for item in data], len(data) == limit


def infer_alias(url):
    parts = urlsplit(url)
    pieces = parts.path.strip("/").split("/")
    if parts.hostname in ("boards.greenhouse.io", "job-boards.greenhouse.io") and len(pieces) >= 3 and pieces[1] == "jobs":
        return "greenhouse", pieces[0], pieces[2], "global"
    if parts.hostname in ("jobs.lever.co", "jobs.eu.lever.co") and len(pieces) >= 2:
        return "lever", pieces[0], pieces[1], "eu" if parts.hostname == "jobs.eu.lever.co" else "global"
    return None


def fetch_candidate(client, record, config):
    alias = next((a for a in record["sourceAliases"] if a["source"] in ("greenhouse", "lever") and a["tenant"] and a["sourceJobId"]), None)
    region = "global"
    if not alias and record["sourceUrl"]:
        inferred = infer_alias(record["sourceUrl"])
        if inferred:
            source, tenant, job_id, region = inferred
            alias = {"source": source, "tenant": tenant, "sourceJobId": job_id}
    if alias:
        board = next((b for b in config["company_boards"] if b["source"] == alias["source"] and b["board"] == alias["tenant"]),
                     {"source": alias["source"], "board": alias["tenant"], "company": record.get("company"), "region": region})
        token, job_id = quote(alias["tenant"], safe=""), quote(alias["sourceJobId"], safe="")
        if alias["source"] == "greenhouse":
            data = client.json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}")
            result = greenhouse_post(data, board)
        else:
            host = "api.eu.lever.co" if board.get("region") == "eu" else "api.lever.co"
            data = client.json(f"https://{host}/v0/postings/{token}/{job_id}")
            result = lever_post(data, board)
        if result["sourceJobId"] != alias["sourceJobId"]:
            raise FetchError("fetch_failed")
        result["jobId"] = record["jobId"]
        return result
    if not record["sourceUrl"]:
        raise FetchError("browser_required")
    return structured_page(client.get(record["sourceUrl"]), record)


def structured_page(raw, record):
    """Generic JSON-LD only; ambiguous, dynamic or incomplete pages go to manual review."""
    visible = plain(raw)
    if re.search(r"verify you are human|captcha|sign in to (?:view|continue)", visible, re.I):
        raise FetchError("captcha" if "captcha" in visible.lower() else "auth_required")
    found = []

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if types == "JobPosting" or isinstance(types, list) and "JobPosting" in types:
                found.append(value)
            for key in ("@graph", "mainEntity"):
                if key in value:
                    walk(value[key])

    for payload in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw, re.I | re.S):
        try:
            walk(json.loads(payload))
        except ValueError:
            continue
    if len(found) != 1:
        raise FetchError("browser_required")
    data = found[0]
    if isinstance(data.get("url"), str) and normalize_url(data["url"]) != normalize_url(record["sourceUrl"]):
        raise FetchError("browser_required")
    body = plain(data.get("description", ""))
    title = data.get("title")
    company = data.get("hiringOrganization", {}).get("name")
    collapse = lambda s: re.sub(r"\s+", " ", s).strip()
    verified = bool(body and title and company and collapse(body) in collapse(visible)
                    and title.casefold() in visible.casefold() and company.casefold() in visible.casefold())
    official = any(a["source"] == "company_career_pages" for a in record.get("sourceAliases", []))
    origin_source = record.get("sourceAliases", [{"source": "other_public_sources"}])[0]["source"]
    obs = base_observation(origin_source,
                           record["sourceUrl"], None, None, company, title)
    obs.update(jobId=record["jobId"], description=body, descriptionKind="company_page" if official else "public_page",
               descriptionStatus="complete" if verified else "partial",
               completenessEvidence="JSON-LD description/title/company verified against visible HTML." if verified else "",
               availabilityStatus="unknown")
    add_fact(obs, "datePosted", data.get("datePosted"))
    if data.get("jobLocationType") == "TELECOMMUTE":
        add_fact(obs, "workMode", "remote")
    if isinstance(data.get("employmentType"), str):
        add_fact(obs, "employmentType", data["employmentType"].lower().replace("_", "-"))
    locations, countries = [], []
    raw_locations = data.get("jobLocation", [])
    for item in raw_locations if isinstance(raw_locations, list) else [raw_locations]:
        address = item.get("address", {}) if isinstance(item, dict) else {}
        country = address.get("addressCountry")
        if isinstance(country, str) and re.fullmatch("[A-Z]{2}", country):
            countries.append(country)
        location = ", ".join(str(address[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if isinstance(address.get(k), str))
        if location:
            locations.append(location)
    add_fact(obs, "locations", sorted(set(locations)))
    add_fact(obs, "jobCountries", sorted(set(countries)))
    closed = re.search(r"this (?:job|position|role) (?:is no longer available|has been filled|is closed)|no longer accepting applications", visible, re.I)
    apply = re.search(r"\bapply (?:now|for this (?:job|position|role))\b", visible, re.I)
    if closed or verified and apply:
        obs.update(availabilityStatus="closed" if closed else "open", availabilityCheckedAt=obs["capturedAt"])
        obs["evidence"]["availabilityStatus"] = (closed or apply).group(0)
    deadline = data.get("validThrough")
    if isinstance(deadline, str):
        from .common import dt
        try:
            expired = deadline < now()[:10] if len(deadline) == 10 else dt(deadline) < dt(now())
        except ValueError:
            expired = False
        if expired:
            obs.update(availabilityStatus="closed", availabilityCheckedAt=obs["capturedAt"])
            obs["evidence"]["availabilityStatus"] = json.dumps({"validThrough": deadline})
    obs["sourceContent"] = "\n".join([visible, *obs["evidence"].values()])
    return obs
