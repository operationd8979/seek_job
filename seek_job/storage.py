"""Single-writer storage with a durable redo journal for multi-file commits."""
from __future__ import annotations

import copy
import os
import socket
import uuid
from contextlib import contextmanager
from pathlib import Path

from .common import (PipelineError, atomic_json, atomic_text, inside, json_text,
                     load_json, now, relative, validate)
from .contracts import METADATA


class Store:
    def __init__(self, root, config):
        self.root = Path(root).resolve()
        self.config = config
        self.index_path = inside(self.root, config["output"]["state_file"])
        self.candidates_path = inside(self.root, config["output"]["candidates_file"])
        self.jobs = inside(self.root, config["output"]["jobs_directory"])
        self.runs = inside(self.root, config["output"]["runs_directory"])
        self.stage = self.root / "state/staging"
        self.fault_after = None  # fault injection used only by isolated tests

    @contextmanager
    def locked(self):
        lock = self.root / "state/writer.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        # Advisory OS lock releases on process death; lock file is not a stale lock.
        stream = lock.open("a+b")
        acquired = False
        try:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b" ")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise PipelineError("Another writer holds state/writer.lock; wait for it to finish") from exc
            stream.seek(1)
            stream.truncate()
            stream.write(json_text({"pid": os.getpid(), "host": socket.gethostname(), "startedAt": now()}).encode())
            stream.flush()
            for folder in {self.index_path.parent, self.candidates_path.parent}:
                if folder.exists() and any("conflict" in p.name.lower() for p in folder.iterdir()):
                    raise PipelineError("Conflicted state copy detected; resolve sync conflict before writing")
            self.recover()
            yield self
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()

    def recover(self):
        if not self.stage.exists():
            return
        for path in sorted(self.stage.glob("*.json")):
            journal = load_json(path)
            if journal.get("schemaVersion") != 1 or not isinstance(journal.get("writes"), list):
                raise PipelineError(f"Invalid journal: {path}")
            if not journal.get("completed"):
                self._apply(path, journal)

    def commit(self, writes):
        self.stage.mkdir(parents=True, exist_ok=True)
        operations = []
        for path, content in writes:
            target = inside(self.root, Path(path))
            if target.is_relative_to(self.stage) or target.name == "writer.lock":
                raise PipelineError("Transaction cannot modify its own journal or lock")
            operations.append({"path": relative(target, self.root), "content": content})
        journal = {"schemaVersion": 1, "createdAt": now(), "completed": False, "writes": operations}
        path = self.stage / f"{uuid.uuid4().hex}.json"
        atomic_json(path, journal)  # durable intent before changing visible artifacts
        self._apply(path, journal)

    def _apply(self, path, journal):
        for i, op in enumerate(journal["writes"]):
            target = inside(self.root, op["path"])
            if target.is_relative_to(self.stage) or target.name == "writer.lock":
                raise PipelineError("Unsafe transaction target")
            atomic_text(target, op["content"])
            if self.fault_after == i + 1:
                raise RuntimeError("Injected interruption")
        # Keep a compact receipt. Pending journals retain all data needed for replay.
        atomic_json(path, {"schemaVersion": 1, "createdAt": journal["createdAt"],
                           "completed": True, "writes": []})

    def read(self):
        index = load_json(self.index_path, {"schemaVersion": 1, "jobs": {}})
        candidates = load_json(self.candidates_path, {"schemaVersion": 1, "candidates": {}})
        for doc, key in ((index, "jobs"), (candidates, "candidates")):
            if doc.get("schemaVersion") != 1 or not isinstance(doc.get(key), dict):
                raise PipelineError(f"Invalid state shape: {key}; use recovery/rebuild")
            for job_id, record in doc[key].items():
                if record.get("jobId") != job_id:
                    raise PipelineError(f"State identity mismatch in {key}")
        for metadata in index["jobs"].values():
            validate(metadata, METADATA, "index.job")
        candidate_schema = copy.deepcopy(METADATA)
        candidate_schema["properties"]["folderPath"] = {"type": ["string", "null"]}
        candidate_schema["additionalProperties"] = True
        candidate_schema["properties"].update({
            "_description": {"type": "string"}, "_sourceContent": {"type": "string"},
            "currentStep": {"type": "string"}, "pendingManualAction": {"type": ["string", "null"]},
            "attempts": {"type": "integer", "minimum": 0},
            "possibleDuplicates": {"type": "array", "items": {"type": "string"}},
        })
        candidate_schema["required"] += ["_description", "_sourceContent", "currentStep", "attempts", "possibleDuplicates"]
        for record in candidates["candidates"].values():
            validate(record, candidate_schema, "state.candidate")
            if record["folderPath"]:
                inside(self.jobs, inside(self.root, record["folderPath"]))
        return index, candidates

    def run_path(self, run_id):
        if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in run_id):
            raise PipelineError("Invalid run ID")
        path = inside(self.runs, run_id)
        if not (path / "checkpoint.json").is_file():
            raise PipelineError("Run not found")
        return path

    def rebuild(self):
        from .identity import keys
        from .handoff import read_job
        from .common import digest
        result = {"schemaVersion": 1, "jobs": {}}
        aliases = {}
        for path in sorted(self.jobs.glob("*/metadata.json")):
            if not (path.parent / ".committed.json").exists():
                continue
            metadata = load_json(path)
            validate(metadata, METADATA, str(path))
            if inside(self.root, metadata["folderPath"]) != path.parent.resolve():
                raise PipelineError(f"Metadata folderPath mismatch: {path}")
            marker = load_json(path.parent / ".committed.json")
            front, body = read_job(path.parent / "job-description.md")
            if (marker.get("jobId") != metadata["jobId"] or front.get("jobId") != metadata["jobId"]
                    or marker.get("descriptionHash") != metadata["descriptionHash"]
                    or digest(body) != metadata["descriptionHash"]
                    or digest((path.parent / "source-content.txt").read_text(encoding="utf-8")) != metadata["sourceContentHash"]):
                raise PipelineError(f"Corrupt committed job, refusing rebuild: {path}")
            job_id = metadata["jobId"]
            if job_id in result["jobs"]:
                raise PipelineError(f"Duplicate jobId during rebuild: {job_id}")
            if not metadata["mergedInto"]:
                for key in keys(metadata):
                    if key in aliases and aliases[key] != job_id:
                        raise PipelineError("Identity conflict during rebuild; nothing overwritten")
                    aliases[key] = job_id
            result["jobs"][job_id] = metadata
        writes = []
        if self.index_path.exists():
            writes.append((self.index_path.with_name(self.index_path.name + ".backup-" + uuid.uuid4().hex[:8]),
                           self.index_path.read_text(encoding="utf-8")))
        writes.append((self.index_path, json_text(result)))
        self.commit(writes)
        return len(result["jobs"])
