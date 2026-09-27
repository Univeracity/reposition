"""Explicit offline adapters. Import never fetches data or infers completeness."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .models import SCHEMA, Record, Snapshot, canonical, sha256, validate_repository


def _objects(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{name} must be an array of objects")
    return value


def _text(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def normalized(value: Any, repository: str | None = None) -> Snapshot:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError(f"normalized input needs schema {SCHEMA}")
    if repository and repository != value.get("repository"):
        raise ValueError("input repository differs from --repo")
    return Snapshot(
        value.get("repository"),
        tuple(Record.from_dict(row) for row in _objects(value.get("records"), "records")),
        value.get("coverage", {}),
    )


def github(value: Any, repository: str) -> Snapshot:
    """Accept gh/REST item lists, optionally wrapped in {items, coverage}."""
    validate_repository(repository)
    coverage = value.get("coverage", {}) if isinstance(value, dict) else {}
    rows = _objects(value.get("items") if isinstance(value, dict) else value, "GitHub items")
    records = []
    numbers = set()
    for row in rows:
        number = row.get("number")
        if type(number) is not int or number <= 0 or number in numbers:
            raise ValueError("GitHub item numbers must be distinct positive integers")
        numbers.add(number)
        title = _text(row.get("title"), "title")
        if not title:
            raise ValueError("each GitHub item needs a title")
        body = _text(row.get("body"), "body")
        is_pr = "pull_request" in row or "head" in row or "headRefOid" in row
        uri = (
            row.get("html_url")
            or row.get("url")
            or f"https://github.com/{repository}/{'pull' if is_pr else 'issues'}/{number}"
        )
        if not isinstance(uri, str):
            raise ValueError("GitHub item URL must be a string")
        parsed = urlparse(uri)
        if parsed.hostname == "github.com":
            parts = parsed.path.strip("/").split("/")
            if (
                len(parts) != 4
                or "/".join(parts[:2]).lower() != repository.lower()
                or parts[2] not in ("issues", "pull")
                or parts[3] != str(number)
            ):
                raise ValueError("GitHub item URL does not match its repository and number")
        elif parsed.hostname == "api.github.com":
            parts = parsed.path.strip("/").split("/")
            if (
                len(parts) != 5
                or parts[0] != "repos"
                or "/".join(parts[1:3]).lower() != repository.lower()
                or parts[3] not in ("issues", "pulls")
                or parts[4] != str(number)
            ):
                raise ValueError("GitHub API URL does not match its repository and number")
            uri = f"https://github.com/{repository}/{'pull' if is_pr or parts[3] == 'pulls' else 'issues'}/{number}"
        item_coverage = row.get("coverage", {})
        revision = "sha256:" + sha256(canonical(row))
        records.append(
            Record(
                f"{number}-summary",
                str(number),
                "summary",
                title,
                body,
                uri,
                revision,
                partial="body" not in row,
                coverage=item_coverage,
            )
        )
        comments = row.get("comments", [])
        if type(comments) is int and comments >= 0:
            comments = []
        for i, comment in enumerate(_objects(comments, "comments")):
            # REST 'comments' can be a total count, not inline evidence.
            text = _text(comment.get("body"), "comment body")
            comment_id = comment.get("id", i)
            comment_uri = (
                comment.get("html_url") or comment.get("url") or f"{uri}#comment-{comment_id}"
            )
            records.append(
                Record(
                    f"{number}-comment-{comment_id}",
                    str(number),
                    "comments",
                    title,
                    text,
                    comment_uri,
                    "sha256:" + sha256(canonical(comment)),
                    partial="body" not in comment,
                    coverage=item_coverage,
                )
            )
        for i, file in enumerate(_objects(row.get("files", []), "files")):
            filename = file.get("filename") or file.get("path")
            if not isinstance(filename, str) or not filename:
                raise ValueError("each changed file needs filename or path")
            patch = _text(file.get("patch"), "file patch")
            head_metadata = row.get("head") or {}
            if not isinstance(head_metadata, dict):
                raise ValueError("GitHub head metadata must be an object")
            head = head_metadata.get("sha") or row.get("headRefOid")
            file_uri = file.get("blob_url") or (
                f"https://github.com/{repository}/blob/{head}/{quote(filename, safe='/')}"
                if head
                else uri
            )
            # An included patch can still be truncated by the upstream API.
            records.append(
                Record(
                    f"{number}-file-{i}",
                    str(number),
                    "files",
                    title,
                    filename + ("\n" + patch if patch else ""),
                    file_uri,
                    "sha256:" + sha256(canonical(file)),
                    partial=None,
                    coverage=item_coverage,
                )
            )
    return Snapshot(repository, tuple(records), {"items": "unknown", **coverage})


def components(value: Any, repository: str, manifest: Any = None) -> Snapshot:
    """Import the original experiment's component cache without runtime dependencies."""
    validate_repository(repository)
    records = []
    manifest = manifest or {}
    for row in _objects(value, "components"):
        if row.get("repo") not in (repository, repository.split("/", 1)[1]):
            raise ValueError("component cache contains a different repository")
        span = row.get("span", {})
        text = _text(row.get("text"), "component text")
        if span.get("charEnd") != span.get("charStart", 0) + len(text):
            raise ValueError("component source span does not match its text")
        if row.get("excerptSha256") != sha256(text):
            raise ValueError("component text hash mismatch")
        coverage = manifest.get("componentCoverage", {}).get(str(row["item"]), {})
        records.append(
            Record(
                row["id"],
                str(row["item"]),
                row["component"],
                row["title"],
                text,
                row["uri"],
                row["sourceRevision"],
                span["charStart"],
                row.get("componentPossiblyPartial"),
                coverage,
            )
        )
    return Snapshot(
        repository, tuple(records), {"items": "bounded-sample", "details": "per-record"}
    )


def load_snapshot(
    path: str | Path,
    *,
    format: str = "records",
    repository: str | None = None,
    manifest_path: str | Path | None = None,
) -> Snapshot:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if format == "records":
        return normalized(value, repository)
    if repository is None:
        raise ValueError(f"--repo is required for {format} input")
    if format == "github":
        return github(value, repository)
    if format == "components":
        manifest = (
            json.loads(Path(manifest_path).read_text(encoding="utf-8")) if manifest_path else None
        )
        if manifest is not None:
            if not isinstance(manifest, dict):
                raise ValueError("component manifest must be an object")
            expected = manifest.get("documentsSha256")
            if expected and expected != hashlib.sha256(Path(path).read_bytes()).hexdigest():
                raise ValueError("component manifest does not match input bytes")
        return components(value, repository, manifest)
    raise ValueError(f"unsupported input format: {format}")
