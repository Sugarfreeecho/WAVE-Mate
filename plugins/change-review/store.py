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
import subprocess
import tempfile
import threading
import uuid
import zipfile
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
            "baselines": {},
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
        for key in ("pending", "records", "active", "groups", "operations", "baselines"):
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
            relative = os.path.relpath(os.path.abspath(path), os.path.abspath(work_root))
            if relative == os.pardir or relative.startswith(os.pardir + os.sep):
                return os.path.abspath(path)
            return Path(relative).as_posix()
        except (OSError, ValueError):
            return os.path.abspath(path)

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

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

    @staticmethod
    def _git_inventory(work_root: Path) -> Optional[List[Path]]:
        """Return Git's review surface: tracked plus non-ignored untracked files.

        This deliberately follows repository policy instead of maintaining a
        list of tool-specific scratch directories.  Missing tracked paths stay
        in the inventory so deletions can be observed.
        """
        try:
            root_result = subprocess.run(
                ["git", "-C", str(work_root), "rev-parse", "--show-toplevel"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            repo_root = Path(
                root_result.stdout.decode("utf-8", errors="surrogateescape").strip()
            ).resolve()
            relative_root = work_root.resolve().relative_to(repo_root)
            pathspec = relative_root.as_posix() if relative_root.parts else "."
            files_result = subprocess.run(
                [
                    "git", "-C", str(repo_root), "ls-files", "--full-name",
                    "-co", "--exclude-standard", "-z", "--", pathspec,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

        paths: List[Path] = []
        seen: set[str] = set()
        for raw in files_result.stdout.split(b"\0"):
            if not raw:
                continue
            relative = raw.decode("utf-8", errors="surrogateescape")
            path = repo_root / relative
            try:
                path.relative_to(work_root)
            except ValueError:
                continue
            key = FileChangeReviewStore._key(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
        return paths

    def _baseline_archive_path(self, baseline_id: str) -> Path:
        return self.root / "baselines" / f"{baseline_id}.zip"

    def _remove_baseline(self, baseline: dict) -> None:
        baseline_id = str(baseline.get("baseline_id") or "")
        if not baseline_id:
            return
        try:
            self._baseline_archive_path(baseline_id).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _ensure_workspace_baseline(
        self,
        index: dict,
        *,
        run_id: str,
        work_root: Path,
    ) -> Optional[dict]:
        baseline_key = str(run_id or "") + "\0" + self._key(work_root)
        existing = index["baselines"].get(baseline_key)
        if isinstance(existing, dict):
            return existing

        inventory = self._git_inventory(work_root)
        if inventory is None:
            return None

        # A session executes one top-level run at a time.  Once a new run gets
        # its baseline, older full-workspace archives are no longer needed;
        # changed files have already been promoted into durable content blobs.
        for key, baseline in list(index["baselines"].items()):
            if key != baseline_key and isinstance(baseline, dict):
                self._remove_baseline(baseline)
                index["baselines"].pop(key, None)

        baseline_id = uuid.uuid4().hex
        target = self._baseline_archive_path(baseline_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=baseline_id + ".", suffix=".tmp", dir=str(target.parent)
        )
        os.close(fd)
        entries: Dict[str, dict] = {}
        try:
            with zipfile.ZipFile(
                tmp_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1
            ) as archive:
                for ordinal, path in enumerate(inventory):
                    data = _read_regular_file(path)
                    state = _state(path, data)
                    path_key = self._key(path)
                    archive_name = None
                    if data is not None:
                        archive_name = f"{ordinal:08d}"
                        archive.writestr(archive_name, data)
                    entries[path_key] = {
                        "path_abs": str(path),
                        "path": self._display_path(path, work_root),
                        "path_key": path_key,
                        "before": state,
                        "archive_name": archive_name,
                    }
            os.replace(tmp_name, target)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

        baseline = {
            "baseline_id": baseline_id,
            "baseline_key": baseline_key,
            "run_id": str(run_id or ""),
            "work_root": str(work_root),
            "entries": entries,
        }
        index["baselines"][baseline_key] = baseline
        return baseline

    def _baseline_bytes(self, baseline: dict, entry: dict) -> Optional[bytes]:
        state = entry.get("before") or {}
        if not state.get("exists") or state.get("kind") != "file":
            return None
        archive_name = str(entry.get("archive_name") or "")
        if not archive_name:
            raise SnapshotGoneError("workspace baseline content is missing")
        try:
            with zipfile.ZipFile(
                self._baseline_archive_path(str(baseline.get("baseline_id") or "")), "r"
            ) as archive:
                data = archive.read(archive_name)
        except Exception as exc:
            raise SnapshotGoneError("workspace baseline is missing or damaged") from exc
        if _sha256(data) != str(state.get("sha256") or ""):
            raise SnapshotGoneError("workspace baseline checksum mismatch")
        return data

    def _workspace_entries(self, work_root: Path) -> Optional[tuple[Dict[str, dict], Dict[str, bytes]]]:
        inventory = self._git_inventory(work_root)
        if inventory is None:
            return None
        entries: Dict[str, dict] = {}
        contents: Dict[str, bytes] = {}
        for path in inventory:
            path_key = self._key(path)
            data = _read_regular_file(path)
            state = _state(path, data)
            entries[path_key] = {
                "path_abs": str(path),
                "path": self._display_path(path, work_root),
                "path_key": path_key,
                "before": state,
            }
            if data is not None:
                contents[path_key] = data
        return entries, contents

    def begin_capture(
        self,
        tool_name: str,
        args: dict,
        *,
        run_id: str,
        tool_call_id: str,
        work_root: str | Path,
    ) -> Optional[Capture]:
        """Persist declared paths and the execution process workspace baseline."""
        supported_file_tool = tool_name in {
            "write_file", "edit_file", "apply_patch", "delete_file"
        }
        specs: List[dict] = []
        manifest: Optional[dict] = None
        if supported_file_tool and not (
            tool_name == "write_file" and bool((args or {}).get("temporary"))
        ):
            try:
                specs, manifest = self._target_specs(tool_name, args)
            except Exception:
                # Invalid arguments will be reported by the actual tool unchanged.
                specs, manifest = [], None
        capture_id = uuid.uuid4().hex
        root = Path(work_root).resolve()
        entries: List[dict] = []
        with self.lock:
            index = self._load()
            baseline = self._ensure_workspace_baseline(
                index,
                run_id=str(run_id or ""),
                work_root=root,
            )
            if baseline is None and not specs and not manifest:
                return None
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
                "baseline_key": baseline.get("baseline_key") if baseline else None,
                "work_root": str(root),
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

    def _promote_entry(
        self,
        index: dict,
        pending: dict,
        entry: dict,
        after_data: Optional[bytes],
        immediate_after: dict,
        *,
        authoritative_baseline: bool = False,
    ) -> Optional[dict]:
        run_id = str(pending.get("run_id") or "")
        active_key = run_id + "\0" + str(entry["path_key"])
        snapshot_id = index["active"].get(active_key)
        record = index["records"].get(snapshot_id) if snapshot_id else None
        entry_before_data = (
            self._get_blob(entry.get("before_blob"))
            if (entry.get("before") or {}).get("exists")
            and (entry.get("before") or {}).get("kind") == "file"
            else None
        )
        if not isinstance(record, dict) or record.get("reverted"):
            if _review_same(entry["before"], entry_before_data, immediate_after, after_data):
                return None
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
        elif authoritative_baseline:
            # A declared file-tool snapshot may have been taken after an MCP,
            # plugin or shell tool changed the path.  The process baseline is
            # earlier and therefore owns the cumulative diff/undo origin.
            baseline_changed = not _same_state(record.get("before") or {}, entry["before"])
            if not baseline_changed and record.get("before_blob") and entry.get("before_blob"):
                baseline_changed = record.get("before_blob") != entry.get("before_blob")
            if baseline_changed:
                record["before"] = entry["before"]
                record["before_blob"] = entry.get("before_blob")
                record["path"] = entry["path"]
                record["path_abs"] = entry["path_abs"]

        previous_after = dict(record.get("after") or {})
        previous_before = dict(record.get("before") or {})
        record_before_data: Optional[bytes] = None
        if str((record.get("before") or {}).get("kind") or "") != "directory":
            record_before_data = (
                self._get_blob(record.get("before_blob"))
                if (record.get("before") or {}).get("exists") else b""
            )
        effective = not _review_same(
            record["before"], record_before_data, immediate_after, after_data
        )
        if (
            previous_after
            and _same_state(previous_after, immediate_after)
            and previous_before == record.get("before")
            and bool(record.get("effective", True)) == effective
        ):
            return None

        record["after"] = immediate_after
        record["after_blob"] = self._put_blob(after_data) if after_data is not None else None
        record["operation"] = self._operation(record["before"], immediate_after)
        record["revision"] = int(record.get("revision") or 0) + 1
        if str((record.get("before") or {}).get("kind") or "") == "directory":
            record.update({
                "diff": None,
                "added": 0,
                "removed": 0,
                "diff_omitted_reason": "directory",
            })
        else:
            record.update(self._diff(record["path"], record_before_data or b"", after_data or b""))
        record["effective"] = effective
        if not effective:
            record["neutralized"] = True
            index["active"].pop(active_key, None)
        else:
            record.pop("neutralized", None)
        return self.public_record(record)

    def finish_capture(self, capture: Optional[Capture], *, successful: bool = True) -> List[dict]:
        """Promote the execution process's cumulative, byte-accurate changes."""
        if capture is None:
            return []
        with self.lock:
            index = self._load()
            pending = index["pending"].pop(capture.capture_id, None)
            if not isinstance(pending, dict):
                return []
            output: List[dict] = []
            # Declared paths preserve support for non-Git workspaces, ignored
            # files explicitly edited by native tools, and empty directories.
            for entry in pending.get("entries") or []:
                path = Path(entry["path_abs"])
                after_data = _read_regular_file(path)
                immediate_after = _state(path, after_data)
                public = self._promote_entry(index, pending, entry, after_data, immediate_after)
                if public is not None:
                    output.append(public)

            baseline = index["baselines"].get(str(pending.get("baseline_key") or ""))
            work_root = Path(pending.get("work_root") or ".").resolve()
            current = (
                self._workspace_entries(work_root)
                if isinstance(baseline, dict) else None
            )
            if isinstance(baseline, dict) and current is not None:
                current_entries, current_contents = current
                baseline_entries = baseline.get("entries") or {}
                run_prefix = str(pending.get("run_id") or "") + "\0"
                active_entries: Dict[str, dict] = {}
                for active_key, snapshot_id in index["active"].items():
                    if not str(active_key).startswith(run_prefix):
                        continue
                    record = index["records"].get(snapshot_id)
                    if isinstance(record, dict):
                        active_entries[str(record.get("path_key") or "")] = record
                for path_key in sorted(
                    set(baseline_entries) | set(current_entries) | set(active_entries)
                ):
                    base_entry = baseline_entries.get(path_key)
                    current_entry = current_entries.get(path_key)
                    if base_entry is None and current_entry is not None:
                        base_entry = {
                            "path_abs": current_entry["path_abs"],
                            "path": current_entry["path"],
                            "path_key": path_key,
                            "before": {
                                "exists": False, "kind": "missing", "sha256": None,
                                "bytes": 0, "lines": 0,
                            },
                            "archive_name": None,
                        }
                    elif base_entry is None and path_key in active_entries:
                        active_record = active_entries[path_key]
                        base_entry = {
                            "path_abs": active_record["path_abs"],
                            "path": active_record["path"],
                            "path_key": path_key,
                            "before": {
                                "exists": False, "kind": "missing", "sha256": None,
                                "bytes": 0, "lines": 0,
                            },
                            "archive_name": None,
                        }
                    if base_entry is None:
                        continue
                    if current_entry is not None:
                        after_data = current_contents.get(path_key)
                        immediate_after = current_entry["before"]
                    else:
                        # The tool may have changed .gitignore during the run.
                        # A baseline path disappearing from `git ls-files -co`
                        # is not necessarily a filesystem deletion.
                        current_path = Path(base_entry["path_abs"])
                        after_data = _read_regular_file(current_path)
                        immediate_after = _state(current_path, after_data)
                    active_key = str(pending.get("run_id") or "") + "\0" + path_key
                    if (
                        _same_state(base_entry.get("before") or {}, immediate_after)
                        and active_key not in index["active"]
                    ):
                        continue
                    active_record = index["records"].get(index["active"].get(active_key))
                    if (
                        isinstance(active_record, dict)
                        and _same_state(active_record.get("before") or {}, base_entry.get("before") or {})
                        and _same_state(active_record.get("after") or {}, immediate_after)
                        and bool(active_record.get("effective", True))
                    ):
                        continue
                    workspace_entry = dict(base_entry)
                    baseline_data = self._baseline_bytes(baseline, base_entry)
                    workspace_entry["before_blob"] = (
                        self._put_blob(baseline_data) if baseline_data is not None else None
                    )
                    if after_data is None and immediate_after.get("exists"):
                        after_data = _read_regular_file(Path(base_entry["path_abs"]))
                    public = self._promote_entry(
                        index,
                        pending,
                        workspace_entry,
                        after_data,
                        immediate_after,
                        authoritative_baseline=True,
                    )
                    if public is not None:
                        output.append(public)

            # Only the latest revision for each path matters inside one tool
            # result.  This also prevents the declared-path fallback from
            # briefly flashing a misleading intermediate row.
            latest: Dict[str, dict] = {}
            order: List[str] = []
            for row in output:
                key = str(row.get("snapshot_id") or row.get("path") or "")
                if key not in latest:
                    order.append(key)
                latest[key] = row
            self._save(index)
            self._gc_blobs(index)
            return [latest[key] for key in order]

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

    def finish_run(self, run_id: str) -> None:
        """Discard the full workspace checkpoint after records are durable."""
        wanted = str(run_id or "")
        if not wanted:
            return
        with self.lock:
            index = self._load()
            changed = False
            for key, baseline in list(index["baselines"].items()):
                if isinstance(baseline, dict) and str(baseline.get("run_id") or "") == wanted:
                    self._remove_baseline(baseline)
                    index["baselines"].pop(key, None)
                    changed = True
            if changed:
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
