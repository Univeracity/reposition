"""Render source-bound evidence; measure the entire formatted text."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .models import SearchResult, canonical, sha256
from .query import expression


def _match_span(text: str, query: str) -> tuple[int, int] | None:
    """Find a literal query span without changing the source's code-point offsets."""
    spans = []
    for literal in re.findall(r'"([^\"]+)"', expression(query)):
        words = literal.split()
        pattern = r"(?<![^\W_])" + r"[\W_]+".join(map(re.escape, words)) + r"(?![^\W_])"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            spans.append(match.span())
        elif len(words) > 1:
            # TF-IDF flattens phrases; retain a matching term when no phrase is present.
            for word in words:
                match = re.search(
                    r"(?<![^\W_])" + re.escape(word) + r"(?![^\W_])", text, re.IGNORECASE
                )
                if match:
                    spans.append(match.span())
    return min(spans) if spans else None


def _window(text: str, size: int, span: tuple[int, int] | None) -> tuple[int, str]:
    before = min(100, (size - (span[1] - span[0])) // 2) if span else 0
    start = max(0, span[0] - before) if span else 0
    return start, text[start : start + size]


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
        span = _match_span(record.text, result.query)
        minimum = span[1] - span[0] if span else min(1, len(record.text))
        size = min(excerpt_chars, len(record.text))
        prefix = (
            f"\n[{result.repository}#{record.item}/{record.component}] {record.title}\n"
            f"{record.uri}\nRevision: {record.source_revision}; coverage: {canonical(record.coverage)}; "
            f"partial: {canonical(record.partial)}\n"
        )

        def block(value: str, source_offset: int, citation: str = prefix) -> str:
            return (
                citation + f"Span: {source_offset}:{source_offset + len(value)} "
                f"codepoints; excerpt sha256: {sha256(value)}\n" + value + "\n"
            )

        omitted = len(result.hits) - len(excerpts) - 1
        fitted = None
        while size >= minimum:
            # Recenter every smaller window so budgeting cannot remove the matched span.
            start, text = _window(record.text, size, span)
            current_truncated = start > 0 or start + len(text) < len(record.text)
            candidate_block = block(text, record.source_start + start)
            candidate = output + candidate_block + footer(omitted, truncated or current_truncated)
            if count(candidate) <= budget:
                fitted = candidate_block
                break
            if size == minimum:
                break
            size = max(minimum, size - max(1, size // 8))
        if fitted is None:
            # A later hit may have shorter citation metadata and still fit.
            continue
        output += fitted
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
