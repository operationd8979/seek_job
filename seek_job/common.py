from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from .contracts import CONFIG


class PipelineError(ValueError):
    """Expected, actionable failure; never include raw HTTP response credentials."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def dt(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def age_hours(value, at=None):
    return (dt(at or now()) - dt(value)).total_seconds() / 3600


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def config_hash(config):
    return digest(canonical(config))


def body_normalize(text):
    # Preserve paragraphs, headings, indentation and source language.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip(" \t") for line in lines).strip("\n") + ("\n" if text.strip() else "")


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise PipelineError(f"YAML key must be a string at line {key_node.start_mark.line + 1}")
        if key in result:
            raise PipelineError(f"Duplicate YAML key '{key}' at line {key_node.start_mark.line + 1}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
# Dates stay strings; do not mutate SafeLoader's shared resolver dictionary.
UniqueLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in value if tag != "tag:yaml.org,2002:timestamp"]
    for key, value in UniqueLoader.yaml_implicit_resolvers.items()
}


def yaml_read(text):
    try:
        return yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise PipelineError(f"Invalid YAML{f' at line {mark.line + 1}' if mark else ''}") from exc


def validate(value, schema, label="data"):
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
                    key=lambda error: str(list(error.absolute_path)))
    if errors:
        # Don't echo values that might contain a secret from a malformed input.
        messages = [f"{label}.{'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator} validation failed"
                    for e in errors[:12]]
        raise PipelineError("\n".join(messages))


def inside(root, path):
    root = Path(root).resolve()
    target = (root / path).resolve()
    if target == root or not target.is_relative_to(root):
        raise PipelineError(f"Path must stay inside {root}: {path}")
    return target


def relative(path, base):
    return Path(os.path.relpath(path, base)).as_posix()


def load_json(path, default=None):
    path = Path(path)
    if not path.exists() and default is not None:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise PipelineError(f"Cannot read valid JSON: {path}; preserve it and use recovery/rebuild") from exc


def atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def atomic_json(path, value):
    atomic_text(path, json_text(value))


SECRET_QUERY = re.compile(r"^(?:access_?token|refresh_?token|id_?token|token|api_?key|authorization|auth|password|secret|signature|sig|x-amz-.+|x-goog-.+)$", re.I)


def normalize_url(url):
    if url is None:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise PipelineError("Only public HTTP(S) URLs without credentials are allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise PipelineError("Invalid URL port") from exc
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(SECRET_QUERY.match(key) for key, _ in query):
        raise PipelineError("Credential/signed URL cannot be stored; supply the public posting URL")
    if re.search(r"(?:^|[?&])(?:access_token|refresh_token|token|password|api_key)=", parts.fragment, re.I):
        raise PipelineError("Credential-bearing URL fragment cannot be stored")
    tracking = {"fbclid", "gclid", "msclkid", "li_fat_id"}
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_") and k.lower() not in tracking]
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{port}" if port and (parts.scheme, port) not in (("http", 80), ("https", 443)) else "")
    # Retain unknown query order, locale, trailing slash, fragment routing and job IDs.
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(query), parts.fragment))


def check_public_text(text):
    patterns = (
        r"(?im)^\s*(?:authorization|cookie|set-cookie)\s*:",
        r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}",
        r"(?i)(?:access_token|refresh_token|api_key|password)\s*[=:]\s*\S+",
    )
    if any(re.search(pattern, text) for pattern in patterns):
        raise PipelineError("Input appears to contain credentials/headers; provide only public job content")


def slug(value, limit=26):
    value = (value or "unknown").replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub("[^a-z0-9]+", "-", value.lower()).strip("-")[:limit].rstrip("-") or "unknown"
    if re.fullmatch(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])", value):
        value = "job-" + value
    return value


def load_config(root, path=None):
    root = Path(root).resolve()
    path = Path(path) if path else root / "config/search-config.yaml"
    text = path.read_text(encoding="utf-8-sig")
    value = yaml_read(text)
    validate(value, CONFIG, "config")
    check_public_text(text)
    ids = [item["id"] for item in value["search_profiles"]]
    if len(ids) != len(set(ids)):
        raise PipelineError("config.search_profiles: duplicate id")
    if sum(value["matching"]["ranking_weights"].values()) <= 0:
        raise PipelineError("config.matching.ranking_weights: sum must be positive")
    outputs = {key: inside(root, val) for key, val in value["output"].items()}
    # Nested/overlapping data stores would corrupt recovery or rebuild.
    paths = list(outputs.values())
    for i, path_a in enumerate(paths):
        for path_b in paths[i + 1:]:
            if path_a == path_b or path_a.is_relative_to(path_b) or path_b.is_relative_to(path_a):
                raise PipelineError("config.output: paths must not overlap")
    for key in ("jobs_directory", "runs_directory"):
        if outputs[key] == root / "state":
            raise PipelineError(f"config.output.{key}: reserved state directory")
    reserved = ("config", "scripts", "seek_job", "schemas", "tests", "prompts", "state/staging")
    for key, path in outputs.items():
        if len(path.relative_to(root).parts) == 1 and key in ("state_file", "candidates_file"):
            raise PipelineError(f"config.output.{key}: state files must be in a dedicated directory")
        if any(path == root / name or path.is_relative_to(root / name) for name in reserved):
            raise PipelineError(f"config.output.{key}: cannot write into implementation/config directories")
    for key in ("config_file", "skill_file"):
        inside((root / value["cv_handoff"]["workspace"]).resolve(), value["cv_handoff"][key])
    for board in value["company_boards"]:
        if not value["sources"][board["source"]]["enabled"]:
            raise PipelineError(f"config.company_boards: source {board['source']} is disabled")
    if len({(b["source"], b["board"], b.get("region", "global")) for b in value["company_boards"]}) != len(value["company_boards"]):
        raise PipelineError("config.company_boards: duplicate board")
    return value, text
