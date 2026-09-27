"""Source and result contracts shared by the CLI and Python integrations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA = "reposition.snapshot.v1"


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_repository(repository: str) -> None:
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise ValueError("repository must be an owner/name identifier")


@dataclass(frozen=True)
class Record:
    id: str
    item: str
    component: str
    title: str
    text: str
    uri: str
    source_revision: str
    source_start: int = 0
    partial: bool | None = None
    coverage: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "item", "component", "title", "text", "uri", "source_revision"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"record {name} must be a string")
            if name not in ("title", "text") and not value.strip():
                raise ValueError(f"record {name} must not be empty")
        if type(self.source_start) is not int or self.source_start < 0:
            raise ValueError("source_start must be a nonnegative code-point offset")
        if self.partial is not None and type(self.partial) is not bool:
            raise ValueError("partial must be true, false, or null (unknown)")
        if not isinstance(self.coverage, dict):
            raise ValueError("coverage must be a JSON object")
        canonical(self.coverage)

    @property
    def text_sha256(self) -> str:
        return sha256(self.text)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "text_sha256": self.text_sha256}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Record:
        if not isinstance(value, dict):
            raise ValueError("each record must be an object")
        allowed = set(cls.__dataclass_fields__) | {"text_sha256"}
        unknown = value.keys() - allowed
        if unknown:
            raise ValueError(f"unknown record fields: {', '.join(sorted(unknown))}")
        try:
            record = cls(**{k: v for k, v in value.items() if k != "text_sha256"})
        except TypeError as exc:
            raise ValueError(f"invalid record: {exc}") from exc
        if "text_sha256" in value and value["text_sha256"] != record.text_sha256:
            raise ValueError(f"text hash mismatch for record {record.id}")
        return record


@dataclass(frozen=True)
class Snapshot:
    repository: str
    records: tuple[Record, ...]
    coverage: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_repository(self.repository)
        if not isinstance(self.coverage, dict):
            raise ValueError("snapshot coverage must be a JSON object")
        canonical(self.coverage)
        if any(not isinstance(r, Record) for r in self.records):
            raise ValueError("snapshot records must be Record instances")
        if len({r.id for r in self.records}) != len(self.records):
            raise ValueError("snapshot contains duplicate record IDs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "repository": self.repository,
            "coverage": self.coverage,
            "records": [r.to_dict() for r in sorted(self.records, key=lambda r: r.id)],
        }

    @property
    def digest(self) -> str:
        """Actual normalized evidence, including coverage; independent of record order."""
        return sha256(canonical(self.to_dict()))


@dataclass(frozen=True)
class Hit:
    record: Record
    score: float | None
    reason: str = "lexical"

    def to_dict(self) -> dict[str, Any]:
        return {"record": self.record.to_dict(), "score": self.score, "reason": self.reason}


@dataclass(frozen=True)
class SearchResult:
    repository: str
    snapshot_digest: str
    query: str
    method: str
    hits: tuple[Hit, ...]
    diagnostics: dict[str, Any]
    coverage: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "reposition.search.v1",
            "repository": self.repository,
            "snapshot_digest": self.snapshot_digest,
            "query": self.query,
            "method": self.method,
            "hits": [hit.to_dict() for hit in self.hits],
            "diagnostics": self.diagnostics,
            "coverage": self.coverage,
        }
