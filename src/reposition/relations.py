"""Bounded literal references, with source evidence; no inferred equivalence."""

from __future__ import annotations

import re
from typing import Any

from .models import Snapshot, sha256

REFERENCE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(?:issues|pull)/(\d+)"
    r"|([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)"
)
CLAIM_VERB = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*$", re.IGNORECASE)
UNCERTAIN = re.compile(
    r"\b(?:no|not|never|neither|nor|without|cannot|unable|fail(?:s|ed)?|"
    r"if|unless|whether|should|would|could|may|might|can|will|must|please|"
    r"perhaps|maybe|possibly|unsure|doubt|hope|plan|propos\w*|try|attempt)\b"
    r"|\b\w+n['’]t\b",
    re.IGNORECASE,
)
CLAUSE_BREAK = re.compile(r"[!?;]|(?<!\w)\.|\.(?!\w)|\n[ \t]*\n")


def _context(text: str, start: int, end: int) -> tuple[str, int, int]:
    """Cite the local clause; only label unambiguous literal closing statements."""
    lower = max(0, start - 240)
    preceding = text[lower:start]
    breaks = list(CLAUSE_BREAK.finditer(preceding))
    begin = lower + breaks[-1].end() if breaks else lower
    while begin < start and text[begin].isspace():
        begin += 1
    following = text[end : end + 240]
    ending = CLAUSE_BREAK.search(following)
    finish = end + ending.end() if ending else min(len(text), end + 240)
    context = text[begin:finish]
    complete_prefix = lower == 0 or bool(breaks)
    complete_suffix = ending is not None or end + 240 >= len(text)
    claim = (
        complete_prefix
        and complete_suffix
        and CLAIM_VERB.search(text[begin:start]) is not None
        and not UNCERTAIN.search(context)
        and "?" not in context
        and not any(quote in context for quote in ('"', "'", "`", "“", "”", "‘", "’"))
    )
    return "claims_fixes" if claim else "references", begin, finish


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
            predicate, context_start, context_end = _context(record.text, start, end)
            literal = record.text[start:end]
            context = record.text[context_start:context_end]
            edges.append(
                {
                    "source_item": record.item,
                    "target_repository": repository,
                    "target_item": target,
                    "target_present": same_repository and target in known,
                    "predicate": predicate,
                    "equivalence_established": False,
                    "reference": {
                        "text": literal,
                        "source_start": record.source_start + start,
                        "source_end": record.source_start + end,
                        "sha256": sha256(literal),
                    },
                    "evidence": {
                        "record_id": record.id,
                        "uri": record.uri,
                        "source_revision": record.source_revision,
                        "record_start": context_start,
                        "record_end": context_end,
                        "source_start": record.source_start + context_start,
                        "source_end": record.source_start + context_end,
                        "offset_unit": "unicode-codepoints",
                        "text": context,
                        "sha256": sha256(context),
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
