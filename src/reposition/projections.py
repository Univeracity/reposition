"""Versioned component projectors and reproducible UTF-8 source locators."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any

from .cache_source import parse_json

PROJECTION_VERSION = 1


@dataclass(frozen=True)
class ProjectionPolicy:
    max_bytes: int = 4096
    overlap_bytes: int = 128
    max_lines: int = 200

    def __post_init__(self):
        if type(self.max_bytes) is not int or not 256 <= self.max_bytes <= 65536:
            raise ValueError("projection max_bytes must be between 256 and 65536")
        if (
            type(self.overlap_bytes) is not int
            or not 0 <= self.overlap_bytes <= self.max_bytes // 4
        ):
            raise ValueError("projection overlap must be between zero and one quarter of max_bytes")
        if type(self.max_lines) is not int or not 1 <= self.max_lines <= 10000:
            raise ValueError("projection max_lines must be between 1 and 10000")

    def to_dict(self):
        return {"version": PROJECTION_VERSION, **asdict(self)}


def pointer_get(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("invalid structural locator; rebuild the index")
    for part in pointer[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise ValueError("invalid structural locator; rebuild the index")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError("invalid structural locator; rebuild the index")
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError("invalid structural locator; rebuild the index")
    return value


def _boundary(raw: bytes, end: int) -> int:
    end = min(end, len(raw))
    while end > 0 and end < len(raw) and raw[end] & 0xC0 == 0x80:
        end -= 1
    return end


def _chunks(text: str, policy: ProjectionPolicy):
    raw = text.encode("utf-8")
    start = previous = line = 0
    while start < len(raw):
        line += raw[previous:start].count(b"\n")
        end = _boundary(raw, start + policy.max_bytes)
        # Prefer a complete source line when a byte ceiling divides a long block.
        last_line = raw.rfind(b"\n", start, end)
        if end < len(raw) and last_line >= start + policy.max_bytes // 2:
            end = last_line + 1
        search = start
        for _ in range(policy.max_lines):
            newline = raw.find(b"\n", search, end)
            if newline < 0:
                break
            search = newline + 1
        else:
            end = search
        fragment = raw[start:end].decode("utf-8")
        yield (
            start,
            end,
            line + 1,
            line + 1 + raw[start:end].count(b"\n") + int(not raw[start:end].endswith(b"\n")),
            len(raw),
            fragment,
        )
        if end == len(raw):
            break
        previous = start
        start = max(start + 1, _boundary(raw, end - min(policy.overlap_bytes, (end - start) // 4)))
        while start < len(raw) and raw[start] & 0xC0 == 0x80:
            start += 1


def _fields(component: str, row: dict[str, Any], prefix: str):
    paths = {
        "summary": (
            ("title", "title"),
            ("body", "body"),
            ("user/login", "author"),
            ("author/login", "author"),
        ),
        "comments": (("body", "body"),),
        "files": (
            ("filename", "path"),
            ("path", "path"),
            ("previous_filename", "path"),
            ("patch", "patch"),
        ),
        "reviews": (("body", "body"), ("state", "state")),
        "review_comments": (("body", "body"), ("path", "path"), ("diff_hunk", "hunk")),
        "checks": (
            ("name", "name"),
            ("status", "status"),
            ("conclusion", "conclusion"),
            ("output/title", "title"),
            ("output/summary", "body"),
            ("output/text", "body"),
            ("kind", "event"),
            ("data/name", "name"),
            ("data/context", "name"),
            ("data/description", "body"),
            ("data/status", "status"),
            ("data/conclusion", "conclusion"),
            ("data/app/name", "name"),
            ("data/output/title", "title"),
            ("data/output/summary", "body"),
            ("data/output/text", "body"),
        ),
        "closing_issues": (
            ("title", "title"),
            ("url", "relationship"),
            ("html_url", "relationship"),
        ),
        "timeline": (
            ("event", "event"),
            ("body", "body"),
            ("source/issue/title", "title"),
            ("source/issue/html_url", "relationship"),
        ),
    }[component]
    for path, field in paths:
        try:
            value = pointer_get(row, "/" + path)
        except (KeyError, IndexError, ValueError):
            continue
        if isinstance(value, str) and value:
            yield prefix + "/" + path, field, value
    if component == "summary" and isinstance(row.get("labels"), list):
        for i, label in enumerate(row["labels"]):
            if isinstance(label, str) and label:
                yield f"/labels/{i}", "label", label
            elif isinstance(label, dict) and isinstance(label.get("name"), str) and label["name"]:
                yield f"/labels/{i}/name", "label", label["name"]


def _diff_regions(text: str):
    previous = byte_start = line_start = 0
    filename = None
    field = "file_header"
    for match in re.finditer(r"(?m)^(?:diff --git |@@)", text):
        start = match.start()
        if start > previous:
            region = text[previous:start]
            yield byte_start, line_start, filename, field, region
            byte_start += len(region.encode("utf-8"))
            line_start += region.count("\n")
        if match[0].startswith("diff"):
            newline = text.find("\n", start)
            header = text[start : newline if newline >= 0 else len(text)]
            path = re.search(r" b/(.+)$", header)
            filename = path[1] if path else None
            field = "file_header"
        else:
            field = "hunk"
        previous = start
    if previous < len(text):
        yield byte_start, line_start, filename, field, text[previous:]


def project(
    component: str, payload: str, policy: ProjectionPolicy
) -> Iterator[tuple[dict[str, Any], str]]:
    """Each text fragment is an original string slice, never serialized JSON."""
    if component == "diff":
        source_bytes = len(payload.encode("utf-8"))
        for byte_start, line_start, filename, field, region in _diff_regions(payload):
            for start, end, first, last, total, text in _chunks(region, policy):
                yield (
                    {
                        "pointer": None,
                        "field": field,
                        "record_pointer": None,
                        "record_id": None,
                        "filename": filename,
                        "coordinate_space": "object-utf8",
                        "start": byte_start + start,
                        "end": byte_start + end,
                        "line_start": line_start + first,
                        "line_end": line_start + last,
                        "source_bytes": source_bytes,
                        "boundary_start": byte_start,
                        "boundary_end": byte_start + total,
                    },
                    text,
                )
        return
    value = parse_json(payload)
    rows = (
        [("", value)] if component == "summary" else [(f"/{i}", row) for i, row in enumerate(value)]
    )
    for prefix, row in rows:
        if not isinstance(row, dict):
            raise ValueError("unsupported component record shape; check the projection version")
        source_row = (
            row.get("data") if component == "checks" and isinstance(row.get("data"), dict) else row
        )
        record_id = source_row.get("node_id", source_row.get("id"))
        if not isinstance(record_id, str | int) or isinstance(record_id, bool):
            record_id = None
        filename = row.get("filename", row.get("path"))
        if not isinstance(filename, str):
            filename = None
        for pointer, field, text in _fields(component, row, prefix):
            for start, end, first, last, total, fragment in _chunks(text, policy):
                yield (
                    {
                        "pointer": pointer,
                        "field": field,
                        "record_pointer": prefix,
                        "record_id": record_id,
                        "filename": filename,
                        "coordinate_space": "decoded-json-string-utf8",
                        "start": start,
                        "end": end,
                        "line_start": first,
                        "line_end": last,
                        "source_bytes": total,
                        "boundary_start": 0,
                        "boundary_end": total,
                    },
                    fragment,
                )


def reproduce(payload: str, locator: dict[str, Any], *, value: Any = None) -> str:
    if locator["coordinate_space"] == "object-utf8":
        text = payload
        if locator["pointer"] is not None:
            raise ValueError("invalid object locator; rebuild the index")
    elif locator["coordinate_space"] == "decoded-json-string-utf8":
        text = pointer_get(parse_json(payload) if value is None else value, locator["pointer"])
    else:
        raise ValueError("unsupported locator coordinate space; rebuild the index")
    start, end = locator["start"], locator["end"]
    if not isinstance(text, str) or type(start) is not int or type(end) is not int:
        raise ValueError("invalid source locator; rebuild the index")
    raw = text.encode("utf-8")
    if not 0 <= start < end <= len(raw):
        raise ValueError("source locator outside original text; rebuild the index")
    try:
        return raw[start:end].decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("source locator divides UTF-8; rebuild the index") from exc
