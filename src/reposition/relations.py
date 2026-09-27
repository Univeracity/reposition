"""Bounded literal references, with source evidence; no inferred equivalence."""

from __future__ import annotations

import re
from typing import Any

from .models import Snapshot, sha256

REFERENCE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(?:issues|pull)/(\d+)"
    r"|([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)"
)


def related(snapshot: Snapshot, item: str, *, limit: int = 20) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("relationship limit must be between 1 and 1000")
    known = {r.item for r in snapshot.records}
    if item not in known:
        raise ValueError(f"item #{item} is not in this snapshot")
    edges = []
    for record in sorted(snapshot.records, key=lambda r: r.id):
        if record.component == "files":
            continue
        references = []
        spans = []
        for match in REFERENCE.finditer(record.text):
            spans.append(match.span())
            target_repository = match[1] or match[3]
            references.append((target_repository, match[2] or match[4], *match.span()))
        for match in re.finditer(r"(?<![\w/#])#(\d+)\b", record.text):
            if not any(a <= match.start() < b for a, b in spans):
                references.append((snapshot.repository, match[1], *match.span()))
        for repository, target, start, end in sorted(references, key=lambda row: row[2]):
            same_repository = repository.lower() == snapshot.repository.lower()
            if record.item != item and not (same_repository and target == item):
                continue
            if same_repository and target == record.item:
                continue
            preceding = record.text[max(0, start - 25) : start].lower()
            predicate = (
                "claims_fixes"
                if re.search(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*$", preceding)
                else "references"
            )
            literal = record.text[start:end]
            edges.append(
                {
                    "source_item": record.item,
                    "target_repository": repository,
                    "target_item": target,
                    "target_present": same_repository and target in known,
                    "predicate": predicate,
                    "equivalence_established": False,
                    "evidence": {
                        "record_id": record.id,
                        "uri": record.uri,
                        "source_revision": record.source_revision,
                        "source_start": record.source_start + start,
                        "source_end": record.source_start + end,
                        "offset_unit": "unicode-codepoints",
                        "text": literal,
                        "sha256": sha256(literal),
                    },
                }
            )
    return {
        "schema": "reposition.relations.v1",
        "repository": snapshot.repository,
        "snapshot_digest": snapshot.digest,
        "item": item,
        "coverage": snapshot.coverage,
        "edges": edges[:limit],
        "omitted_edges": max(0, len(edges) - limit),
        "policy": "literal incoming/outgoing references; one hop; missing targets remain unknown",
    }
