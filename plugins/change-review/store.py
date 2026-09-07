"""Plugin-owned durable snapshots, diffs, and conflict-safe undo.

This module deliberately has no dependency on Runtime V2.  It owns bytes and
metadata only; callers decide how the UI event and model-only notification are
persisted.  Snapshot ids are opaque capabilities scoped to one session store.
"""

from __future__ import annotations

import difflib
import gzip
import hashlib
import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


MAX_DIFF_BYTES = 1024 * 1024
MAX_DIFF_LINES = 20_000
STORE_VERSION = 1
REVIEW_DIR_NAME = "change_reviews"


class ChangeReviewError(RuntimeError):
    status_code = 500
    code = "change_review_error"

    def __init__(self, message: str, *, paths: Optional[List[str]] = None):
        super().__init__(message)
        self.paths = list(paths or [])


class InvalidSnapshotError(ChangeReviewError):
    status_code = 422
    code = "invalid_snapshot"


class SnapshotGoneError(ChangeReviewError):
    status_code = 410
    code = "snapshot_gone"


class ChangeConflictError(ChangeReviewError):
    status_code = 409
    code = "file_changed"


@dataclass(frozen=True)
class Capture:
    capture_id: str


_locks_guard = threading.Lock()
_locks: Dict[str, threading.RLock] = {}


def _store_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _read_regular_file(path: Path) -> Optional[bytes]:
    try:
        if path.is_file() and not path.is_symlink():
            return path.read_bytes()
    except OSError:
        pass
    return None


def _text_info(data: bytes) -> tuple[Optional[str], int, Optional[str]]:
    if not data:
        return "", 0, None
    if len(data) > MAX_DIFF_BYTES:
        # Avoid decoding a large payload merely to decide that its line diff
        # must be omitted. The byte-level count is still useful in the stats.
        line_count = data.count(b"\n") + (1 if not data.endswith(b"\n") else 0)
        return None, line_count, "too_large_bytes"
    if b"\x00" in data:
        line_count = data.count(b"\n") + (1 if not data.endswith(b"\n") else 0)
        return None, line_count, "binary"
    try:
        # Text tools often receive LF while an existing Windows file is CRLF.
        # Normalize only the review representation; snapshots and undo retain
        # the exact original bytes.
        text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        return text, line_count, None
    except UnicodeDecodeError:
        line_count = data.count(b"\n") + (1 if not data.endswith(b"\n") else 0)
        return None, line_count, "binary"


def _state(path: Path, data: Optional[bytes] = None) -> dict:
    try:
        if path.is_dir() and not path.is_symlink():
            return {"exists": True, "kind": "directory", "sha256": None, "bytes": 0, "lines": 0}
    except OSError:
        pass
    if data is None:
        data = _read_regular_file(path)
    if data is None:
        return {"exists": False, "kind": "missing", "sha256": None, "bytes": 0, "lines": 0}
    _text, lines, _reason = _text_info(data)
    return {
        "exists": True,
        "kind": "file",
        "sha256": _sha256(data),
        "bytes": len(data),
        "lines": lines,
    }


def _same_state(left: dict, right: dict) -> bool:
    if bool(left.get("exists")) != bool(right.get("exists")):
        return False
    if not left.get("exists"):
        return True
    left_kind = str(left.get("kind") or "file")
    right_kind = str(right.get("kind") or "file")
    if left_kind != right_kind:
        return False
    return left_kind == "directory" or str(left.get("sha256") or "") == str(right.get("sha256") or "")


def _review_same(
    before: dict,
    before_data: Optional[bytes],
    after: dict,
    after_data: Optional[bytes],
) -> bool:
    """Treat newline-only text rewrites as the same review state.

    The file snapshots and undo conflict checks remain byte-exact. This
    narrower equivalence is only for suppressing misleading whole-file review
    rows when a Windows text tool changes LF/CRLF while adding/removing a line.
    """
    if _same_state(before, after):
        return True
    if (
        before.get("exists") and after.get("exists")
        and before.get("kind") == after.get("kind") == "file"
        and before_data is not None and after_data is not None
    ):
        before_text, _before_lines, before_reason = _text_info(before_data)
        after_text, _after_lines, after_reason = _text_info(after_data)
        return before_reason is None and after_reason is None and before_text == after_text
    return False


class FileChangeReviewStore:
    """One durable change-review store located inside a session directory."""

    def __init__(self, session_dir: str | Path):
        self.session_dir = Path(session_dir).resolve()
        self.root = self.session_dir / REVIEW_DIR_NAME
        self.index_path = self.root / "index.json"
        self.blob_dir = self.root / "blobs"
        self.lock = _store_lock(self.root)

    def _empty(self) -> dict:
        return {
            "version": STORE_VERSION,
            "pending": {},
            "records": {},
            "active": {},
            "groups": {},
            "operations": {},
        }

    def _load(self) -> dict:
        if not self.index_path.is_file():
            return self._empty()
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SnapshotGoneError("change review metadata is unreadable") from exc
        if not isinstance(data, dict) or int(data.get("version") or 0) != STORE_VERSION:
            raise SnapshotGoneError("change review metadata has an unsupported version")
        for key in ("pending", "records", "active", "groups", "operations"):
            data.setdefault(key, {})
        return data

    def _save(self, data: dict) -> None:
        _atomic_json(self.index_path, data)

    def _put_blob(self, content: bytes) -> str:
        digest = _sha256(content)
        target = self.blob_dir / f"{digest}.gz"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=digest + ".", suffix=".tmp", dir=str(target.parent))
            try:
                with os.fdopen(fd, "wb") as raw:
                    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
                        stream.write(content)
                    raw.flush()
                    os.fsync(raw.fileno())
                os.replace(tmp_name, target)
            finally:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
        return digest

    def _get_blob(self, digest: Optional[str]) -> bytes:
        if not digest:
            raise SnapshotGoneError("snapshot content is missing")
        path = self.blob_dir / f"{digest}.gz"
        try:
            with gzip.open(path, "rb") as stream:
                data = stream.read()
        except Exception as exc:
            raise SnapshotGoneError("snapshot content is missing or damaged") from exc
        if _sha256(data) != digest:
            raise SnapshotGoneError("snapshot checksum mismatch")
        return data

    @staticmethod
    def _display_path(path: Path, work_root: Path) -> str:
        try:
            return path.resolve().relative_to(work_root.resolve()).as_posix()
        except ValueError:
            return str(path.resolve())

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(os.path.normpath(str(path.resolve())))

    @staticmethod
    def _operation(before: dict, after: dict) -> str:
        if not before.get("exists") and after.get("exists"):
            return "create"
        if before.get("exists") and not after.get("exists"):
            return "delete"
        return "modify"

    @staticmethod
    def _target_specs(tool_name: str, args: dict) -> tuple[List[dict], Optional[dict]]:
        from agent_tools import AGENT_DEFAULT_WRITE_FILENAME, _parse_apply_patch, safe_work_path

        name = str(tool_name or "")
        args = args if isinstance(args, dict) else {}
        if name == "apply_patch":
            operations = _parse_apply_patch(str(args.get("patch") or ""))
            return [
                {"path": safe_work_path(str(item["path"])), "requested_operation": item["kind"]}
                for item in operations
            ], None
        raw = args.get("path") or args.get("target_directory") or args.get("file_path")
        if name == "write_file" and not raw:
            raw = AGENT_DEFAULT_WRITE_FILENAME
        if not raw:
            return [], None
        path = safe_work_path(str(raw))
        if name == "delete_file" and path.is_dir() and not path.is_symlink():
            files: List[dict] = []
            dirs: List[str] = []
            for child in sorted(path.rglob("*"), key=lambda item: str(item).lower()):
                if child.is_symlink():
                    continue
                if child.is_dir():
                    dirs.append(str(child.resolve()))
                elif child.is_file():
                    files.append({"path": child.resolve(), "requested_operation": "delete"})
            manifest = {"root": str(path.resolve()), "directories": [str(path.resolve()), *dirs]}
            if not files:
                files.append({"path": path.resolve(), "requested_operation": "delete", "is_directory": True})
            return files, manifest
        requested = "delete" if name == "delete_file" else ("create" if name == "write_file" else "modify")
        return [{"path": path.resolve(), "requested_operation": requested}], None

    def begin_capture(
        self,
        tool_name: str,
        args: dict,
        *,
        run_id: str,
        tool_call_id: str,
        work_root: str | Path,
    ) -> Optional[Capture]:
        """Persist pre-tool bytes. Call while holding the workspace write lock."""
        if tool_name not in {"write_file", "edit_file", "apply_patch", "delete_file"}:
            return None
        # Temporary files are turn-scoped implementation details and are deleted
        # outside the tool call, so exposing them would immediately create a stale
        # undo target.
        if tool_name == "write_file" and bool((args or {}).get("temporary")):
            return None
        try:
            specs, manifest = self._target_specs(tool_name, args)
        except Exception:
            # Invalid arguments will be reported by the actual tool unchanged.
            return None
        if not specs and not manifest:
            return None
        capture_id = uuid.uuid4().hex
        root = Path(work_root).resolve()
        entries: List[dict] = []
        with self.lock:
            index = self._load()
            group_id = uuid.uuid4().hex if manifest is not None else None
            for spec in specs:
                path = Path(spec["path"]).resolve()
                before_data = _read_regular_file(path)
                before = _state(path, before_data)
                entries.append(
                    {
                        "path_abs": str(path),
                        "path": self._display_path(path, root),
                        "path_key": self._key(path),
                        "requested_operation": spec.get("requested_operation") or "modify",
                        "before": before,
                        "before_blob": self._put_blob(before_data) if before_data is not None else None,
                        "group_id": group_id,
                        "is_directory": bool(spec.get("is_directory")),
                    }
                )
            if group_id and manifest:
                index["groups"][group_id] = {
                    "root": manifest["root"],
                    "directories": manifest["directories"],
                    "snapshot_ids": [],
                }
            index["pending"][capture_id] = {
                "tool": tool_name,
                "run_id": str(run_id or ""),
                "tool_call_id": str(tool_call_id or ""),
                "entries": entries,
                "group_id": group_id,
            }
            self._save(index)
        return Capture(capture_id)

    def _diff(self, path: str, before_data: bytes, after_data: bytes) -> dict:
        before_text, before_lines_count, before_reason = _text_info(before_data)
        after_text, after_lines_count, after_reason = _text_info(after_data)
        reason = before_reason or after_reason
        if reason is None and (len(before_data) > MAX_DIFF_BYTES or len(after_data) > MAX_DIFF_BYTES):
            reason = "too_large_bytes"
        if reason is None and (before_lines_count > MAX_DIFF_LINES or after_lines_count > MAX_DIFF_LINES):
            reason = "too_many_lines"
        if reason:
            return {"diff": None, "added": None, "removed": None, "diff_omitted_reason": reason}
        before_lines = (before_text or "").splitlines(keepends=True)
        after_lines = (after_text or "").splitlines(keepends=True)
        matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
        added = removed = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in {"replace", "delete"}:
                removed += i2 - i1
            if tag in {"replace", "insert"}:
                added += j2 - j1
        unified = "".join(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="\n",
            )
        )
        return {"diff": unified, "added": added, "removed": removed, "diff_omitted_reason": None}

    def finish_capture(self, capture: Optional[Capture], *, successful: bool = True) -> List[dict]:
        """Promote changed paths and return cumulative UI records.

        A native tool exception is not a completed file change.  Drop its
        pending pre-tool capture without exposing a review row, even if the
        implementation happened to leave a partial write behind.
        """
        if capture is None:
            return []
        with self.lock:
            index = self._load()
            pending = index["pending"].pop(capture.capture_id, None)
            if not isinstance(pending, dict):
                return []
            if not successful:
                self._save(index)
                self._gc_blobs(index)
                return []
            output: List[dict] = []
            run_id = str(pending.get("run_id") or "")
            for entry in pending.get("entries") or []:
                path = Path(entry["path_abs"])
                entry_before_data = (
                    self._get_blob(entry.get("before_blob"))
                    if (entry.get("before") or {}).get("exists")
                    and (entry.get("before") or {}).get("kind") == "file"
                    else None
                )
                after_data = _read_regular_file(path)
                immediate_after = _state(path, after_data)
                if _review_same(entry["before"], entry_before_data, immediate_after, after_data):
                    continue
                active_key = run_id + "\0" + str(entry["path_key"])
                snapshot_id = index["active"].get(active_key)
                record = index["records"].get(snapshot_id) if snapshot_id else None
                if not isinstance(record, dict) or record.get("reverted"):
                    snapshot_id = uuid.uuid4().hex
                    record = {
                        "snapshot_id": snapshot_id,
                        "run_id": run_id,
                        "tool_call_id": str(pending.get("tool_call_id") or ""),
                        "path": entry["path"],
                        "path_abs": entry["path_abs"],
                        "path_key": entry["path_key"],
                        "before": entry["before"],
                        "before_blob": entry.get("before_blob"),
                        "revision": 0,
                        "reverted": False,
                        "group_id": entry.get("group_id"),
                    }
                    index["records"][snapshot_id] = record
                    index["active"][active_key] = snapshot_id
                    group_id = entry.get("group_id")
                    if group_id and group_id in index["groups"]:
                        index["groups"][group_id]["snapshot_ids"].append(snapshot_id)
                record["after"] = immediate_after
                record["after_blob"] = self._put_blob(after_data) if after_data is not None else None
                record["operation"] = self._operation(record["before"], immediate_after)
                record["revision"] = int(record.get("revision") or 0) + 1
                record_before_data = None
                if str((record.get("before") or {}).get("kind") or "") == "directory":
                    record.update({"diff": None, "added": 0, "removed": 0, "diff_omitted_reason": "directory"})
                else:
                    record_before_data = (
                        self._get_blob(record.get("before_blob"))
                        if record["before"].get("exists") else b""
                    )
                    cumulative_after = after_data or b""
                    record.update(self._diff(record["path"], record_before_data, cumulative_after))
                record["effective"] = not _review_same(
                    record["before"], record_before_data, immediate_after, after_data
                )
                # A later edit in the same execution process may bring the file
                # back to its original bytes. Keep a revision tombstone so the
                # just-emitted UI metadata can hide the earlier row, but no
                # longer expose an undo capability for a net-zero change.
                if not record["effective"]:
                    record["neutralized"] = True
                    index["active"].pop(active_key, None)
                output.append(self.public_record(record))
            self._save(index)
            self._gc_blobs(index)
            return output

    @staticmethod
    def public_record(record: dict) -> dict:
        return {
            "path": record.get("path") or "",
            "operation": record.get("operation") or "modify",
            "snapshot_id": record.get("snapshot_id") or "",
            "revision": int(record.get("revision") or 0),
            "diff": record.get("diff"),
            "added": record.get("added"),
            "removed": record.get("removed"),
            "before": dict(record.get("before") or {}),
            "after": dict(record.get("after") or {}),
            "diff_omitted_reason": record.get("diff_omitted_reason"),
            "effective": bool(record.get("effective", True)),
            "reverted": bool(record.get("reverted")),
        }

    def _restore_record(self, record: dict, side: str) -> None:
        path = Path(record["path_abs"])
        state = record.get(side) or {}
        if state.get("exists"):
            if str(state.get("kind") or "file") == "directory":
                path.mkdir(parents=True, exist_ok=True)
            else:
                _atomic_bytes(path, self._get_blob(record.get(f"{side}_blob")))
        else:
            if path.exists():
                if not path.is_file() or path.is_symlink():
                    raise ChangeConflictError("path is no longer a regular file", paths=[record.get("path") or str(path)])
                path.unlink()

    def undo(self, snapshot_ids: Iterable[str], operation_id: str) -> dict:
        ids = [str(item or "").strip() for item in snapshot_ids]
        if not ids or any(not item for item in ids) or len(set(ids)) != len(ids):
            raise InvalidSnapshotError("snapshot_ids must be a non-empty array of unique ids")
        operation_id = str(operation_id or "").strip()
        if not operation_id or len(operation_id) > 200:
            raise InvalidSnapshotError("operation_id is required")
        with self.lock:
            index = self._load()
            cached = index["operations"].get(operation_id)
            if isinstance(cached, dict):
                if cached.get("snapshot_ids") != ids:
                    raise InvalidSnapshotError("operation_id was already used for another request")
                replay = dict(cached.get("result") or {})
                replay["idempotent_replay"] = True
                return replay
            records: List[dict] = []
            for snapshot_id in ids:
                record = index["records"].get(snapshot_id)
                if not isinstance(record, dict):
                    raise InvalidSnapshotError(f"unknown snapshot_id: {snapshot_id}")
                if record.get("reverted") or record.get("neutralized"):
                    raise SnapshotGoneError(f"snapshot is no longer active: {snapshot_id}")
                records.append(record)
            conflicts: List[str] = []
            for record in records:
                current = _state(Path(record["path_abs"]))
                if not _same_state(current, record.get("after") or {}):
                    conflicts.append(str(record.get("path") or record["path_abs"]))
            if conflicts:
                raise ChangeConflictError("one or more files were modified again", paths=conflicts)
            # Ensure every required blob is valid before touching the filesystem.
            for record in records:
                if (record.get("before") or {}).get("exists") and str((record.get("before") or {}).get("kind") or "file") == "file":
                    self._get_blob(record.get("before_blob"))
                if (record.get("after") or {}).get("exists") and str((record.get("after") or {}).get("kind") or "file") == "file":
                    self._get_blob(record.get("after_blob"))
            restored: List[dict] = []
            try:
                for record in records:
                    self._restore_record(record, "before")
                    restored.append(record)
                selected = set(ids)
                for group in index["groups"].values():
                    group_ids = set(group.get("snapshot_ids") or [])
                    if group_ids and group_ids.issubset(selected):
                        for raw_dir in sorted(group.get("directories") or [], key=len):
                            Path(raw_dir).mkdir(parents=True, exist_ok=True)
            except Exception:
                for record in reversed(restored):
                    try:
                        self._restore_record(record, "after")
                    except Exception:
                        pass
                raise
            public: List[dict] = []
            for record in records:
                record["reverted"] = True
                record["effective"] = False
                index["active"].pop(str(record.get("run_id") or "") + "\0" + str(record.get("path_key") or ""), None)
                public.append(self.public_record(record))
            result = {"ok": True, "operation_id": operation_id, "snapshot_ids": ids, "changes": public}
            index["operations"][operation_id] = {
                "snapshot_ids": ids,
                "result": result,
                "phase": "restored",
            }
            self._save(index)
            return result

    def commit_undo(self, operation_id: str) -> None:
        """Mark the surrounding history/event transaction durable, then GC bytes."""
        with self.lock:
            index = self._load()
            operation = index["operations"].get(str(operation_id or ""))
            if not isinstance(operation, dict):
                raise InvalidSnapshotError("unknown undo operation")
            operation["phase"] = "committed"
            self._save(index)
            self._gc_blobs(index)

    def rollback_undo(self, operation_id: str) -> None:
        """Restore post-tool bytes if persisting the undo event/notice fails."""
        with self.lock:
            index = self._load()
            operation = index["operations"].get(str(operation_id or ""))
            if not isinstance(operation, dict) or operation.get("phase") != "restored":
                return
            records = [
                index["records"].get(snapshot_id)
                for snapshot_id in operation.get("snapshot_ids") or []
            ]
            records = [record for record in records if isinstance(record, dict)]
            restored: List[dict] = []
            try:
                for record in records:
                    self._restore_record(record, "after")
                    restored.append(record)
            except Exception:
                for record in reversed(restored):
                    try:
                        self._restore_record(record, "before")
                    except Exception:
                        pass
                raise
            for record in records:
                record["reverted"] = False
                record["effective"] = True
                active_key = str(record.get("run_id") or "") + "\0" + str(record.get("path_key") or "")
                index["active"][active_key] = record["snapshot_id"]
            index["operations"].pop(str(operation_id or ""), None)
            self._save(index)

    def prune_unreferenced(self, referenced_snapshot_ids: Iterable[str]) -> None:
        """Drop active records hidden by a history truncation, then GC bytes."""
        keep = {str(item or "") for item in referenced_snapshot_ids if str(item or "")}
        with self.lock:
            index = self._load()
            removed = {
                snapshot_id
                for snapshot_id, record in index["records"].items()
                if snapshot_id not in keep and not record.get("reverted")
            }
            if not removed:
                return
            for snapshot_id in removed:
                record = index["records"][snapshot_id]
                record["reverted"] = True
                record["effective"] = False
            index["active"] = {
                key: value for key, value in index["active"].items() if value not in removed
            }
            self._save(index)
            self._gc_blobs(index)

    def copy_referenced_to(
        self,
        target_session_dir: str | Path,
        referenced_snapshot_ids: Iterable[str],
    ) -> None:
        """Copy snapshot metadata/content retained by a new history branch."""
        wanted = {str(item or "") for item in referenced_snapshot_ids if str(item or "")}
        if not wanted:
            return
        target = FileChangeReviewStore(target_session_dir)
        with self.lock, target.lock:
            source_index = self._load()
            target_index = target._empty()
            for snapshot_id in wanted:
                record = source_index["records"].get(snapshot_id)
                if not isinstance(record, dict):
                    continue
                cloned = json.loads(json.dumps(record, ensure_ascii=False))
                target_index["records"][snapshot_id] = cloned
                if not cloned.get("reverted"):
                    active_key = str(cloned.get("run_id") or "") + "\0" + str(cloned.get("path_key") or "")
                    target_index["active"][active_key] = snapshot_id
                for blob_key in ("before_blob", "after_blob"):
                    digest = cloned.get(blob_key)
                    if digest:
                        target._put_blob(self._get_blob(digest))
            for group_id, group in source_index["groups"].items():
                retained = [item for item in group.get("snapshot_ids") or [] if item in target_index["records"]]
                if retained:
                    cloned_group = json.loads(json.dumps(group, ensure_ascii=False))
                    cloned_group["snapshot_ids"] = retained
                    target_index["groups"][group_id] = cloned_group
            if target_index["records"]:
                target._save(target_index)

    def _gc_blobs(self, index: Optional[dict] = None) -> None:
        if not self.blob_dir.is_dir():
            return
        index = index or self._load()
        keep: set[str] = set()
        for pending in index.get("pending", {}).values():
            for entry in pending.get("entries") or []:
                if entry.get("before_blob"):
                    keep.add(entry["before_blob"])
        for record in index.get("records", {}).values():
            if record.get("reverted") or record.get("neutralized"):
                continue
            for key in ("before_blob", "after_blob"):
                if record.get(key):
                    keep.add(record[key])
        for path in self.blob_dir.glob("*.gz"):
            if path.stem not in keep:
                try:
                    path.unlink()
                except OSError:
                    pass

__all__ = [
    "ChangeConflictError",
    "ChangeReviewError",
    "FileChangeReviewStore",
    "InvalidSnapshotError",
    "MAX_DIFF_BYTES",
    "MAX_DIFF_LINES",
    "SnapshotGoneError",
]
