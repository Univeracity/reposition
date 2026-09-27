"""Literal any-term query policy adapted from Vyral's lexical implementation.

Copyright 2026 Jeremy Dixon. Portions licensed under Apache-2.0; see NOTICE
and licenses/Vyral-Apache-2.0.txt. Modified for Reposition: standalone parser,
fixed any-term behavior, no runtime models/options, bounded query length.
"""

import re

STOP_WORDS = frozenset(
    "a an and are as at be by for from in is it of on or that the to with".split()
)


def terms(text: str) -> tuple[str, ...]:
    # [^\W_] includes Unicode alphanumerics but excludes the underscore.
    return tuple(re.findall(r"[^\W_]+", text.lower(), flags=re.UNICODE))


def expression(query: str) -> str:
    """Quote user input as literal FTS terms; preserve balanced quoted phrases."""
    if not isinstance(query, str) or len(query) > 4096:
        raise ValueError("query must be a string of at most 4096 characters")
    balanced = query.count('"') > 0 and query.count('"') % 2 == 0
    phrases = re.findall(r'"([^\"]*)"', query) if balanced else []
    quoted = ['"' + " ".join(terms(phrase)) + '"' for phrase in phrases if terms(phrase)]
    residual = re.sub(r'"[^\"]*"', " ", query) if balanced else query
    tokens = terms(residual)
    if not tokens and not quoted:
        tokens = terms(query)
    filtered = [t for t in tokens if len(t) > 1 and t not in STOP_WORDS]
    literals = ['"' + t + '"' for t in (filtered or tokens)]
    return " OR ".join(dict.fromkeys([*quoted, *literals]))
