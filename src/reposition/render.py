"""Render source-bound evidence; measure the entire formatted text."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .models import SearchResult, canonical, sha256


@dataclass(frozen=True)
class Evidence:
    text: str
    count: int
    budget: int
    unit: str
    encoding: str | None
    sha256: str
    excerpts: tuple[dict[str, Any], ...]
    omitted_hits: int

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "reposition.evidence.v1", **asdict(self)}


def render(
    result: SearchResult,
    *,
    budget: int = 8000,
    unit: str = "characters",
    encoding: str = "o200k_base",
    excerpt_chars: int = 600,
) -> Evidence:
    """Budget includes headers, citations, coverage and footer; excludes JSON wrapping.

    Token budgets require the 'tokens' extra and an explicit consuming encoding.
    No character/token approximation is used.
    """
    if (
        type(budget) is not int
        or budget <= 0
        or type(excerpt_chars) is not int
        or excerpt_chars <= 0
    ):
        raise ValueError("budget and excerpt_chars must be positive integers")
    if unit == "characters":
        count = len
        used_encoding = None
    elif unit == "tokens":
        try:
            import tiktoken
        except ImportError as exc:
            raise ValueError("token budgets require: pip install 'reposition[tokens]'") from exc
        tokenizer = tiktoken.get_encoding(encoding)
        count = lambda text: len(tokenizer.encode(text, disallowed_special=()))  # noqa: E731
        used_encoding = encoding
    else:
        raise ValueError("unit must be characters or tokens")
    header = (
        f"{result.repository}; query: {result.query}\n"
        f"Snapshot: sha256:{result.snapshot_digest}\n"
        f"Coverage: {canonical(result.coverage)}; missing evidence is unknown.\n"
        f"Method: {result.method}; candidate cap: {result.diagnostics['candidate_limit']}; "
        f"capped: {str(result.diagnostics['candidates_truncated']).lower()}. "
        "Rankings are not duplicate decisions.\n"
    )

    def footer(omitted: int, truncated: bool) -> str:
        return f"\nOmitted hits: {omitted}; excerpt truncation: {str(truncated).lower()}; budget: {budget} {unit}.\n"

    if count(header + footer(len(result.hits), False)) > budget:
        raise ValueError("budget is too small for the query, snapshot metadata and footer")
    output = header
    excerpts = []
    truncated = False
    for hit in result.hits:
        record = hit.record
        # Query-centered source slice. Positions remain code-point offsets in original text.
        positions = [
            match.start()
            for term in set(re.findall(r"[a-z0-9]+", result.query.lower()))
            if len(term) >= 4
            for match in [re.search(re.escape(term), record.text, re.IGNORECASE)]
            if match
        ]
        start = max(0, min(positions) - 100) if positions else 0
        text = record.text[start : start + excerpt_chars]
        prefix = (
            f"\n[{result.repository}#{record.item}/{record.component}] {record.title}\n"
            f"{record.uri}\nRevision: {record.source_revision}; coverage: {canonical(record.coverage)}; "
            f"partial: {canonical(record.partial)}\n"
        )

        def block(
            value: str, citation: str = prefix, source_offset: int = record.source_start + start
        ) -> str:
            return (
                citation + f"Span: {source_offset}:{source_offset + len(value)} "
                f"codepoints; excerpt sha256: {sha256(value)}\n" + value + "\n"
            )

        omitted = len(result.hits) - len(excerpts) - 1
        while True:
            current_truncated = start > 0 or start + len(text) < len(record.text)
            candidate = output + block(text) + footer(omitted, truncated or current_truncated)
            if count(candidate) <= budget:
                break
            if not text:
                candidate = None
                break
            text = text[: -max(1, len(text) // 8)]
        if candidate is None:
            break
        output += block(text)
        truncated |= current_truncated
        excerpts.append(
            {
                "record_id": record.id,
                "item": record.item,
                "component": record.component,
                "uri": record.uri,
                "source_revision": record.source_revision,
                "record_start": start,
                "record_end": start + len(text),
                "source_start": record.source_start + start,
                "source_end": record.source_start + start + len(text),
                "offset_unit": "unicode-codepoints",
                "text": text,
                "sha256": sha256(text),
                "truncated": current_truncated,
                "partial": record.partial,
                "coverage": record.coverage,
            }
        )
    omitted = len(result.hits) - len(excerpts)
    output += footer(omitted, truncated)
    measured = count(output)
    if measured > budget:
        raise ValueError("final evidence exceeds budget")
    return Evidence(
        output, measured, budget, unit, used_encoding, sha256(output), tuple(excerpts), omitted
    )
