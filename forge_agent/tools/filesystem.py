"""Workspace-confined file tools."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from .core import ToolResult, ToolSpec


PROTECTED_DIRS = {".git", ".forge", ".venv", "__pycache__", "node_modules"}
IGNORED_DIRS = PROTECTED_DIRS


def _is_protected(relative: Path) -> bool:
    if any(part.casefold() in PROTECTED_DIRS for part in relative.parts):
        return True
    name = relative.name.casefold()
    return name == ".env" or name.startswith(".env.")


class Workspace:
    """Resolve paths once and enforce a hard workspace boundary."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"Workspace is not a directory: {self.root}")

    def resolve(self, user_path: str = ".", *, must_exist: bool = False) -> Path:
        candidate = Path(user_path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {user_path}") from exc
        if relative.parts and _is_protected(relative):
            raise PermissionError(f"Protected workspace path is not accessible: {user_path}")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"Path does not exist: {user_path}")
        return resolved

    def display(self, path: Path) -> str:
        relative = path.resolve(strict=False).relative_to(self.root)
        return relative.as_posix() or "."

    def files(self, start: Path) -> Iterable[Path]:
        if start.is_file():
            yield start
            return
        for current, directories, filenames in os.walk(start):
            directories[:] = sorted(d for d in directories if d not in IGNORED_DIRS)
            for filename in sorted(filenames):
                path = Path(current) / filename
                if not _is_protected(path.relative_to(self.root)):
                    yield path


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("Binary file is not supported")
    return data.decode("utf-8-sig", errors="replace")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(content)
            temporary_name = stream.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def build_filesystem_tools(workspace: Workspace) -> list[ToolSpec]:
    def list_files(args: dict[str, Any]) -> ToolResult:
        start = workspace.resolve(args.get("path", "."), must_exist=True)
        max_depth = args.get("max_depth", 3)
        if not 0 <= max_depth <= 8:
            return ToolResult.failure("max_depth must be between 0 and 8")
        if start.is_file():
            return ToolResult.success(workspace.display(start))

        rows: list[str] = []
        truncated = False
        for path in workspace.files(start):
            depth = len(path.relative_to(start).parts) - 1
            if depth > max_depth:
                continue
            rows.append(workspace.display(path))
            if len(rows) >= 500:
                truncated = True
                break
        message = "\n".join(rows) if rows else "(no files)"
        if truncated:
            message += "\n... [file list limited to 500 entries]"
        return ToolResult.success(message, count=len(rows), truncated=truncated)

    def read_file(args: dict[str, Any]) -> ToolResult:
        path = workspace.resolve(args["path"], must_exist=True)
        if not path.is_file():
            return ToolResult.failure(f"Not a file: {args['path']}")
        text = _read_text(path)
        lines = text.splitlines()
        start = args.get("start_line", 1)
        end = args.get("end_line", min(len(lines), start + 399))
        if start < 1 or end < start:
            return ToolResult.failure("Require 1 <= start_line <= end_line")
        end = min(end, len(lines), start + 399)
        body = "\n".join(
            f"{number:>5} | {lines[number - 1]}"
            for number in range(start, end + 1)
        )
        return ToolResult.success(
            body or "(empty file)",
            path=workspace.display(path),
            total_lines=len(lines),
            returned_lines=[start, end] if lines else [],
            sha256=_sha256(path),
        )

    def read_files(args: dict[str, Any]) -> ToolResult:
        requested = args["paths"]
        if not 1 <= len(requested) <= 20:
            return ToolResult.failure("paths must contain between 1 and 20 files")
        if not all(isinstance(item, str) and item for item in requested):
            return ToolResult.failure("Every paths item must be a non-empty string")
        max_lines = args.get("max_lines_per_file", 200)
        if not 1 <= max_lines <= 400:
            return ToolResult.failure("max_lines_per_file must be between 1 and 400")

        sections: list[str] = []
        returned: list[str] = []
        for user_path in requested:
            path = workspace.resolve(user_path, must_exist=True)
            if not path.is_file():
                return ToolResult.failure(f"Not a file: {user_path}")
            lines = _read_text(path).splitlines()
            body = "\n".join(
                f"{number:>5} | {lines[number - 1]}"
                for number in range(1, min(len(lines), max_lines) + 1)
            )
            display = workspace.display(path)
            suffix = "\n... [file truncated]" if len(lines) > max_lines else ""
            sections.append(f"### {display}\n{body or '(empty file)'}{suffix}")
            returned.append(display)
        return ToolResult.success(
            "\n\n".join(sections),
            paths=returned,
            count=len(returned),
            max_lines_per_file=max_lines,
        )

    def file_info(args: dict[str, Any]) -> ToolResult:
        path = workspace.resolve(args["path"], must_exist=True)
        info: dict[str, Any] = {
            "path": workspace.display(path),
            "type": "directory" if path.is_dir() else "file",
            "bytes": path.stat().st_size,
            "modified_ns": path.stat().st_mtime_ns,
        }
        if path.is_file():
            info["sha256"] = _sha256(path)
        else:
            visible_entries = 0
            truncated = False
            for visible_entries, _ in enumerate(workspace.files(path), start=1):
                if visible_entries >= 10_000:
                    truncated = True
                    break
            info["visible_entries"] = visible_entries
            info["entry_count_truncated"] = truncated
        return ToolResult.success(json.dumps(info, ensure_ascii=False), **info)

    def search_text(args: dict[str, Any]) -> ToolResult:
        start = workspace.resolve(args.get("path", "."), must_exist=True)
        query = args["query"]
        pattern = args.get("glob", "*")
        case_sensitive = args.get("case_sensitive", False)
        needle = query if case_sensitive else query.casefold()
        matches: list[str] = []
        skipped = 0
        for path in workspace.files(start):
            relative = workspace.display(path)
            if not (fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern)):
                continue
            try:
                lines = _read_text(path).splitlines()
            except (OSError, ValueError):
                skipped += 1
                continue
            for number, line in enumerate(lines, 1):
                haystack = line if case_sensitive else line.casefold()
                if needle in haystack:
                    matches.append(f"{relative}:{number}: {line[:500]}")
                    if len(matches) >= 200:
                        return ToolResult.success(
                            "\n".join(matches) + "\n... [limited to 200 matches]",
                            count=len(matches),
                            truncated=True,
                            skipped_binary_or_unreadable=skipped,
                        )
        return ToolResult.success(
            "\n".join(matches) if matches else "(no matches)",
            count=len(matches),
            truncated=False,
            skipped_binary_or_unreadable=skipped,
        )

    def write_file(args: dict[str, Any]) -> ToolResult:
        path = workspace.resolve(args["path"])
        existed = path.exists()
        if existed and not path.is_file():
            return ToolResult.failure(f"Not a regular file: {args['path']}")
        if existed and not args.get("overwrite", False):
            return ToolResult.failure(
                "File exists. Read it first, then call with overwrite=true if replacement is intended."
            )
        before = _sha256(path)
        _atomic_write(path, args["content"])
        after = _sha256(path)
        return ToolResult.success(
            f"{'Updated' if existed else 'Created'} {workspace.display(path)}",
            changed=before != after,
            path=workspace.display(path),
            bytes=path.stat().st_size,
            sha256_before=before,
            sha256_after=after,
        )

    def replace_in_file(args: dict[str, Any]) -> ToolResult:
        path = workspace.resolve(args["path"], must_exist=True)
        if not path.is_file():
            return ToolResult.failure(f"Not a file: {args['path']}")
        text = _read_text(path)
        expected = args.get("expected_count", 1)
        actual = text.count(args["old_text"])
        if actual != expected:
            return ToolResult.failure(
                f"Expected {expected} exact occurrence(s), found {actual}; file was not changed.",
                actual_count=actual,
            )
        before = _sha256(path)
        updated = text.replace(args["old_text"], args["new_text"])
        _atomic_write(path, updated)
        return ToolResult.success(
            f"Replaced {actual} occurrence(s) in {workspace.display(path)}",
            changed=text != updated,
            path=workspace.display(path),
            replacements=actual,
            sha256_before=before,
            sha256_after=_sha256(path),
        )

    def apply_edits(args: dict[str, Any]) -> ToolResult:
        path = workspace.resolve(args["path"], must_exist=True)
        if not path.is_file():
            return ToolResult.failure(f"Not a file: {args['path']}")
        edits = args["edits"]
        if not isinstance(edits, list) or not 1 <= len(edits) <= 20:
            return ToolResult.failure("edits must contain between 1 and 20 operations")

        original = _read_text(path)
        updated = original
        replacements = 0
        for index, edit in enumerate(edits, start=1):
            if not isinstance(edit, dict):
                return ToolResult.failure(f"Edit {index} must be an object")
            extras = set(edit) - {"old_text", "new_text", "expected_count"}
            if extras:
                return ToolResult.failure(
                    f"Edit {index} has unexpected field(s): {', '.join(sorted(extras))}"
                )
            old_text = edit.get("old_text")
            new_text = edit.get("new_text")
            expected = edit.get("expected_count", 1)
            if not isinstance(old_text, str) or not old_text:
                return ToolResult.failure(f"Edit {index} old_text must be a non-empty string")
            if not isinstance(new_text, str):
                return ToolResult.failure(f"Edit {index} new_text must be a string")
            if isinstance(expected, bool) or not isinstance(expected, int) or not 1 <= expected <= 1000:
                return ToolResult.failure(
                    f"Edit {index} expected_count must be an integer between 1 and 1000"
                )
            actual = updated.count(old_text)
            if actual != expected:
                return ToolResult.failure(
                    f"Edit {index} expected {expected} occurrence(s), found {actual}; "
                    "file was not changed.",
                    failed_edit=index,
                    actual_count=actual,
                )
            updated = updated.replace(old_text, new_text)
            replacements += actual

        before = _sha256(path)
        _atomic_write(path, updated)
        return ToolResult.success(
            f"Applied {len(edits)} edit(s) to {workspace.display(path)}",
            changed=updated != original,
            path=workspace.display(path),
            edit_count=len(edits),
            replacements=replacements,
            sha256_before=before,
            sha256_after=_sha256(path),
        )

    def make_directory(args: dict[str, Any]) -> ToolResult:
        path = workspace.resolve(args["path"])
        if path.exists() and not path.is_dir():
            return ToolResult.failure(f"Path exists and is not a directory: {args['path']}")
        existed = path.is_dir()
        path.mkdir(parents=True, exist_ok=True)
        return ToolResult.success(
            f"{'Directory already exists' if existed else 'Created directory'}: "
            f"{workspace.display(path)}",
            changed=not existed,
            path=workspace.display(path),
        )

    def move_file(args: dict[str, Any]) -> ToolResult:
        source = workspace.resolve(args["source"], must_exist=True)
        destination = workspace.resolve(args["destination"])
        if not source.is_file():
            return ToolResult.failure("source must be a regular file")
        if source == destination:
            return ToolResult.failure("source and destination are the same file")
        if destination.exists():
            return ToolResult.failure("destination already exists; move_file never overwrites")
        before = _sha256(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        return ToolResult.success(
            f"Moved {workspace.display(source)} to {workspace.display(destination)}",
            changed=True,
            source=workspace.display(source),
            path=workspace.display(destination),
            changed_paths=[workspace.display(source), workspace.display(destination)],
            sha256=before,
        )

    def delete_file(args: dict[str, Any]) -> ToolResult:
        path = workspace.resolve(args["path"], must_exist=True)
        if not path.is_file():
            return ToolResult.failure("delete_file only removes regular files")
        expected = args["expected_sha256"].casefold()
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
            return ToolResult.failure("expected_sha256 must be a 64-character hexadecimal digest")
        actual = _sha256(path)
        if actual != expected:
            return ToolResult.failure(
                "SHA-256 mismatch; file was not deleted. Read file_info again before retrying.",
                path=workspace.display(path),
                actual_sha256=actual,
            )
        display = workspace.display(path)
        path.unlink()
        return ToolResult.success(
            f"Deleted {display}",
            changed=True,
            path=display,
            changed_paths=[display],
            deleted_sha256=actual,
        )

    object_schema = {"type": "object", "additionalProperties": False}
    return [
        ToolSpec(
            "list_files",
            "List files beneath a workspace path. Repository metadata and dependency caches are skipped.",
            {
                **object_schema,
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path"},
                    "max_depth": {"type": "integer", "description": "0 to 8; default 3"},
                },
            },
            list_files,
        ),
        ToolSpec(
            "read_file",
            "Read a UTF-8 text file with line numbers, at most 400 lines per call.",
            {
                **object_schema,
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
            read_file,
        ),
        ToolSpec(
            "read_files",
            "Read up to 20 UTF-8 text files in one call, with line numbers and per-file limits.",
            {
                **object_schema,
                "properties": {
                    "paths": {"type": "array"},
                    "max_lines_per_file": {"type": "integer"},
                },
                "required": ["paths"],
            },
            read_files,
        ),
        ToolSpec(
            "file_info",
            "Return type, size, modification time, and SHA-256 for one workspace path.",
            {
                **object_schema,
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            file_info,
        ),
        ToolSpec(
            "search_text",
            "Search text files recursively and return path, line number, and matching line.",
            {
                **object_schema,
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string", "description": "Filename glob such as *.py"},
                    "case_sensitive": {"type": "boolean"},
                },
                "required": ["query"],
            },
            search_text,
        ),
        ToolSpec(
            "write_file",
            "Create a text file or atomically replace one. Existing files require overwrite=true.",
            {
                **object_schema,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path", "content"],
            },
            write_file,
        ),
        ToolSpec(
            "replace_in_file",
            "Atomically replace an exact text fragment. The expected count prevents ambiguous edits.",
            {
                **object_schema,
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "expected_count": {"type": "integer"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            replace_in_file,
        ),
        ToolSpec(
            "apply_edits",
            "Apply 1-20 exact replacements to one text file as a single transaction.",
            {
                **object_schema,
                "properties": {
                    "path": {"type": "string"},
                    "edits": {"type": "array"},
                },
                "required": ["path", "edits"],
            },
            apply_edits,
        ),
        ToolSpec(
            "make_directory",
            "Create a workspace directory and missing parents; existing directories are unchanged.",
            {
                **object_schema,
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            make_directory,
        ),
        ToolSpec(
            "move_file",
            "Move one workspace file without overwriting an existing destination.",
            {
                **object_schema,
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                },
                "required": ["source", "destination"],
            },
            move_file,
        ),
        ToolSpec(
            "delete_file",
            "Delete one regular file only when its current SHA-256 matches the supplied digest.",
            {
                **object_schema,
                "properties": {
                    "path": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                },
                "required": ["path", "expected_sha256"],
            },
            delete_file,
        ),
    ]
