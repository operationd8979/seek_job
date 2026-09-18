"""Local dashboard domain operations. All mutations enter through ui-action."""
from __future__ import annotations

import copy
import re
import shutil
import uuid
from pathlib import Path

from .common import (PipelineError, age_hours, atomic_json, atomic_text, digest, inside, json_text,
                     load_config, load_json, now, canonical, yaml_read)
from .engine import Session, find_run
from .storage import Store

ACTIVE = {"queued", "running", "cancelling"}


def presets(root):
    paths = [root / "config/search-config.yaml", *sorted((root / "storage").glob("search-config-*.yaml"))]
    result = []
    for path in paths:
        try:
            config, _ = load_config(root, path)
            key = "current" if path.parent.name == "config" else path.stem.removeprefix("search-config-")
            name = {"current": "Cấu hình hiện tại",
                    "tester-frontend-hcm": "Tester / Frontend · Intern–Junior · TP.HCM",
                    "fullstack-devops-hcm-remote": "Fullstack / DevOps · Middle–Senior · HCM / Remote quốc tế"}.get(key, key)
            result.append({"id": key, "name": name,
                           "path": path.relative_to(root).as_posix(), "config": config})
        except (PipelineError, OSError):
            continue
    return result


def preset(root, key):
    item = next((p for p in presets(root) if p["id"] == key), None)
    if not item:
        raise PipelineError("Preset không tồn tại.")
    return item


def run_files(root, trash=False):
    base = root / ("state/ui/trash" if trash else "runs")
    if not base.exists():
        return []
    found = []
    for checkpoint in base.rglob("checkpoint.json"):
        try:
            config, _ = load_config(root, checkpoint.parent / "config.snapshot.yaml")
            expected = inside(root, config["output"]["runs_directory"]) / checkpoint.parent.name
            if trash or expected.resolve() == checkpoint.parent.resolve():
                found.append(checkpoint)
        except (PipelineError, OSError):
            continue
    return found


def fingerprint(record):
    fields = ["jobId", "jobTitle", "company", "sourceUrl", "descriptionHash", "sourceContentHash",
              "descriptionStatus", "availabilityStatus", "availabilityCheckedAt", "matchStatus",
              "identityStatus", "evaluatedConfigHash", "reasonCodes", "_description"]
    fields += ["locations", "jobCountries", "workMode", "employmentType", "level", "datePosted",
               "salary", "remoteScope", "eligibleCountries", "timezoneRequirements", "timezoneOverlapHours",
               "sponsorship", "authorizationRequired", "roleMatches"]
    return digest(canonical({k: record.get(k) for k in fields}))


def run_data(root, run_id):
    folder, config, _ = find_run(root, run_id)
    cp = load_json(folder / "checkpoint.json")
    snapshot = folder / "candidates.snapshot.json"
    saved = load_json(snapshot if snapshot.exists() else inside(root, config["output"]["candidates_file"]),
                      {"candidates": {}})["candidates"]
    records = {key: copy.deepcopy(saved[key]) for key in cp["outcomes"] if key in saved}
    reviews = load_json(folder / "reviews.json", {"decisions": {}, "events": []})
    for key, record in records.items():
        record["fingerprint"] = fingerprint(record)
        decision = reviews["decisions"].get(key, {})
        record["review"] = dict(decision)
        record["review"]["status"] = ("stale" if decision and decision.get("fingerprint") != record["fingerprint"]
                                      else decision.get("status", "pending"))
        record["approvalBlocks"] = approval_blocks(record)
        record["approvalWarnings"] = approval_warnings(record, config)
    return folder, config, cp, records, reviews


def approval_blocks(record):
    reasons = []
    if record.get("descriptionStatus") != "complete" or not record.get("_description", "").strip():
        reasons.append("Cần JD đầy đủ trước khi duyệt tạo CV.")
    if record.get("availabilityStatus") == "closed":
        reasons.append("Tin đã đóng.")
    if record.get("identityStatus") != "resolved" or record.get("mergedInto"):
        reasons.append("Cần giải quyết danh tính hoặc trùng lặp.")
    return reasons


def approval_warnings(record, config):
    reasons = []
    if record.get("matchStatus") != "accepted":
        reasons.append("Chưa đạt bộ lọc: " + ", ".join(record.get("reasonCodes", [])))
    if record.get("availabilityStatus") != "open":
        reasons.append("Chưa xác minh còn tuyển.")
    at = record.get("availabilityCheckedAt")
    if not at or age_hours(at) > config["freshness"]["max_availability_age_hours"]:
        reasons.append("Thông tin còn tuyển cần được kiểm tra lại.")
    return reasons


def operations(root):
    return [load_json(p) for p in sorted((root / "state/ui/operations").glob("*.json"))]


def operation_path(root, operation_id):
    if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise PipelineError("Operation ID không hợp lệ.")
    return inside(root / "state/ui/operations", operation_id + ".json")


def batch_path(root, batch_id):
    if not re.fullmatch(r"[a-f0-9]{32}", batch_id):
        raise PipelineError("Batch ID không hợp lệ.")
    return inside(root / "state/ui/batches", batch_id + ".json")


def profile_options(root, config):
    workspace = (root / config["cv_handoff"]["workspace"]).resolve()
    path = workspace / config["cv_handoff"]["config_file"]
    if not path.is_file():
        return {"workspace": str(workspace), "profiles": [], "templates": [], "error": "Không tìm thấy CV config."}
    cv = yaml_read(path.read_text(encoding="utf-8-sig"))
    default = inside(workspace, path.parent / cv["profile_root"])
    # Keep legacy flat profiles and profiles/<name> discoverable too.
    dirs = [default, *sorted((workspace / "profile").glob("*")),
            *sorted((workspace / "profiles").glob("*"))]
    result = []
    for folder in dict.fromkeys(dirs):
        file = folder / "personal.md"
        if not file.is_file():
            continue
        inside(workspace, folder)
        text = file.read_text(encoding="utf-8-sig")
        match = re.search(r"\*\*Full name:\*\*\s*(.+)", text)
        result.append({"path": folder.relative_to(workspace).as_posix(),
                       "name": match.group(1).strip() if match else folder.name})
    template_root = inside(workspace, path.parent / cv["template_root"])
    templates = [p.name for p in template_root.iterdir() if p.is_dir()] if template_root.exists() else []
    return {"workspace": str(workspace), "profiles": result, "templates": templates,
            "defaultTemplate": cv.get("default_template", "ats-single-column")}


def list_runs(root, trash=False):
    result = []
    for path in run_files(root, trash):
        cp = load_json(path)
        config, _ = load_config(root, path.parent / "config.snapshot.yaml")
        results = load_json(path.parent / "results.json", {})
        label = " / ".join(p["target_roles"][0] for p in config["search_profiles"])
        reviews = load_json(path.parent / "reviews.json", {"decisions": {}})
        snapshot = path.parent / "candidates.snapshot.json"
        records = load_json(snapshot if snapshot.exists() else inside(root, config["output"]["candidates_file"]),
                            {"candidates": {}})["candidates"]
        result.append({"id": cp["runId"], "label": label, "startedAt": cp["startedAt"],
                       "finishedAt": cp.get("finishedAt"), "status": cp["status"],
                       "counts": results.get("counts", {}), "approved": sum(
                           d["status"] == "approved" and key in records and d.get("fingerprint") == fingerprint(records[key])
                           for key, d in reviews["decisions"].items()),
                       "tasksDone": sum(t["status"] == "done" for t in cp["tasks"]),
                       "tasksTotal": len(cp["tasks"]), "trashed": trash})
    return sorted(result, key=lambda r: r["startedAt"], reverse=True)


def dashboard(root):
    return {"runs": list_runs(root), "trash": list_runs(root, True),
            "presets": presets(root), "operations": operations(root),
            "batches": [load_json(p) for p in sorted((root / "state/ui/batches").glob("*.json"))],
            "capabilities": {"codex": bool(shutil.which("codex")), "browser": False}}


def detail(root, run_id):
    folder, config, cp, records, reviews = run_data(root, run_id)
    return {"checkpoint": cp, "config": config, "jobs": list(records.values()),
            "reviewEvents": reviews["events"], "cv": profile_options(root, config),
            "legacySnapshot": not (folder / "candidates.snapshot.json").exists()}


def profile_hash(workspace, profile):
    folder = inside(workspace, profile)
    return digest(canonical({p.relative_to(folder).as_posix(): digest(p.read_text(encoding="utf-8-sig"))
                             for p in sorted(folder.rglob("*.md"))}))


def mutate(root, payload):
    root = Path(root).resolve()
    if not isinstance(payload, dict):
        raise PipelineError("Action phải là object.")
    action = payload.get("action")
    # Reuse the global writer lock, including for UI sidecars.
    config, _ = load_config(root)
    store = Store(root, config)
    with store.locked():
        active = [o for o in operations(root) if o["status"] in ACTIVE]
        if action == "cv-prepare":
            batch = load_json(batch_path(root, payload["id"]))
            workspace = Path(batch["workspace"])
            if profile_hash(workspace, batch["profile"]) != batch["profileHash"]:
                raise PipelineError("Profile đã thay đổi. Tạo batch mới để sử dụng phiên bản mới.")
            for job in batch["jobs"]:
                out = inside(workspace, job["outputDir"])
                if out.exists():
                    raise PipelineError("Thư mục CV đã tồn tại; không ghi đè phiên bản cũ.")
            for job in batch["jobs"]:
                out = inside(workspace, job["outputDir"])
                atomic_text(out / "raw/job.md", "Source: " + str(job["sourceUrl"]) + "\nCaptured batch: " +
                            batch["createdAt"] + "\n\n" + job["description"])
            return {"prepared": batch["id"]}
        if action == "operation-update":
            path = operation_path(root, payload["id"])
            data = load_json(path)
            for key in ("status", "error", "finishedAt", "pid", "batchId"):
                if key in payload:
                    data[key] = payload[key]
            if data["status"] not in ACTIVE | {"completed", "failed", "cancelled", "interrupted", "partial"}:
                raise PipelineError("Trạng thái operation không hợp lệ.")
            atomic_json(path, data)
            return data
        if action == "batch-result":
            path = batch_path(root, payload["id"])
            data = load_json(path)
            data.update(status=payload["status"], results=payload.get("results", []), finishedAt=now())
            atomic_json(path, data)
            return data
        if action == "queue":
            if active:
                raise PipelineError("Một pipeline đang chạy. Hãy đợi hoặc dừng trước khi tiếp tục.")
            kind = payload.get("kind")
            if kind not in {"search", "resume", "collect", "cv"}:
                raise PipelineError("Loại pipeline không hợp lệ.")
            run_id = payload.get("runId")
            batch_id = None
            if kind == "search":
                choice = preset(root, payload.get("preset"))
                session = Session.start(root, config_path=root / choice["path"])
                run_id = session.cp["runId"]
            else:
                find_run(root, run_id)
            if kind == "cv":
                batch_id = create_batch(root, payload)["id"]
            op = {"id": uuid.uuid4().hex, "kind": kind, "runId": run_id, "batchId": batch_id,
                  "status": "queued", "createdAt": now(), "finishedAt": None, "error": None}
            atomic_json(operation_path(root, op["id"]), op)
            return op
        run_id = payload.get("runId")
        if any(o["runId"] == run_id for o in active):
            raise PipelineError("Run đang được xử lý; hãy đợi hoặc dừng để chỉnh sửa.")
        if action == "restore":
            trash = inside(root / "state/ui/trash", str(run_id))
            marker = load_json(trash / "trash.json")
            target = inside(root, marker["originalPath"])
            if target.exists() or target.name != run_id or not target.is_relative_to(root / "runs"):
                raise PipelineError("Không thể khôi phục vào thư mục này.")
            target.parent.mkdir(parents=True, exist_ok=True)
            trash.rename(target)
            (target / "trash.json").unlink()
            return {"restored": run_id}
        folder, saved_config, cp, records, reviews = run_data(root, run_id)
        if action == "delete":
            if any(o["status"] in ACTIVE for o in operations(root)):
                raise PipelineError("Đợi pipeline hiện tại hoàn tất trước khi xóa lịch sử.")
            target = inside(root / "state/ui/trash", run_id)
            if target.exists() or not folder.resolve().is_relative_to((root / "runs").resolve()):
                raise PipelineError("Run không nằm trong thư mục lịch sử được phép.")
            atomic_json(folder / "trash.json", {"originalPath": folder.relative_to(root).as_posix(), "deletedAt": now()})
            target.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(target)
            return {"deleted": run_id}
        if action == "review":
            ids = payload.get("jobIds", [])
            status = payload.get("status")
            if not ids or status not in {"approved", "rejected", "pending"}:
                raise PipelineError("Chọn job và quyết định hợp lệ.")
            staged = []
            for key in ids:
                record = records.get(key)
                if not record or payload.get("fingerprints", {}).get(key) != record["fingerprint"]:
                    raise PipelineError("JD đã thay đổi. Tải lại và xem phiên bản mới trước khi duyệt.")
                if status == "approved":
                    if record["approvalBlocks"]:
                        raise PipelineError("; ".join(record["approvalBlocks"]))
                    if record["approvalWarnings"] and (not payload.get("override") or not payload.get("note", "").strip()):
                        raise PipelineError("Job còn cảnh báo. Cần xác nhận ngoại lệ và ghi lý do.")
                staged.append({"jobId": key, "status": status, "fingerprint": record["fingerprint"],
                               "note": str(payload.get("note", ""))[:4000], "override": bool(payload.get("override")),
                               "at": now()})
            for decision in staged:
                reviews["decisions"][decision["jobId"]] = decision
                reviews["events"].append(decision)
            store.commit([(folder / "reviews.json", json_text(reviews)),
                          (folder / "candidates.snapshot.json", json_text({"schemaVersion": 1,
                           "candidates": {k: {f: v for f, v in r.items() if f not in
                                            {"review", "fingerprint", "approvalBlocks", "approvalWarnings"}}
                                          for k, r in records.items()}}))])
            return {"reviewed": ids}
        raise PipelineError("Action không được hỗ trợ.")


def create_batch(root, payload):
    root = Path(root).resolve()
    folder, config, cp, records, reviews = run_data(root, payload["runId"])
    ids = payload.get("jobIds", [])
    if not ids or len(set(ids)) != len(ids):
        raise PipelineError("Chọn ít nhất một job đã duyệt; không chọn trùng.")
    options = profile_options(root, config)
    profile = next((p for p in options["profiles"] if p["path"] == payload.get("profile")), None)
    if not profile or payload.get("confirmedName") != profile["name"]:
        raise PipelineError("Xác nhận đúng tên người sở hữu profile trước khi tạo CV.")
    template = payload.get("template", options.get("defaultTemplate"))
    if template not in options["templates"]:
        raise PipelineError("Template CV không hợp lệ.")
    workspace = Path(options["workspace"])
    cv_config = yaml_read((workspace / config["cv_handoff"]["config_file"]).read_text(encoding="utf-8-sig"))
    output = inside(workspace, cv_config.get("output_root", "applications"))
    batch_id = uuid.uuid4().hex
    items = []
    for key in ids:
        record = records.get(key)
        if not record or record["review"]["status"] != "approved" or approval_blocks(record):
            raise PipelineError("Danh sách có job chưa duyệt, đã thay đổi hoặc thiếu JD.")
        warnings = approval_warnings(record, config)
        if warnings and not record["review"].get("override"):
            raise PipelineError("Thông tin đã cũ. Hãy duyệt lại và xác nhận ngoại lệ hoặc refresh tin.")
        items.append({"jobId": key, "company": record["company"], "jobTitle": record["jobTitle"],
                      "sourceUrl": record["sourceUrl"], "description": record["_description"],
                      "fingerprint": record["fingerprint"], "decision": record["review"],
                      "outputDir": str(output / "seek-job" / batch_id / key)})
    batch = {"id": batch_id, "runId": cp["runId"], "createdAt": now(), "status": "queued",
             "workspace": str(workspace), "profile": profile["path"], "profileName": profile["name"],
             "profileHash": profile_hash(workspace, profile["path"]), "template": template,
             "templateRoot": str(inside(workspace, cv_config["template_root"])),
             "skill": str(inside(workspace, config["cv_handoff"]["skill_file"])),
             "outputRoot": str(output / "seek-job" / batch_id), "jobs": items, "results": []}
    atomic_json(batch_path(root, batch_id), batch)
    return batch
