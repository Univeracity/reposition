"""Self-contained, offline examples of the existing retrieval APIs."""

from __future__ import annotations

import json
import tempfile
from importlib.resources import files
from pathlib import Path

from ._demo_cache import make_cache
from .cache_index import CacheIndex
from .cache_source import TriageCache
from .importers import github
from .index import Index
from .render import render


def run_demo(query: str | None = None, *, immutable_cache: bool = False) -> str:
    """Use isolated sample data; never open or replace a user's database."""
    if immutable_cache:
        return _cache_demo(query or "café")
    value = json.loads(files("reposition").joinpath("data/github-cache.json").read_text("utf-8"))
    snapshot = github(value, "example/packages")
    query = query or "same filename"
    with Index() as index:
        index.import_snapshot(snapshot)
        result = index.search(query, limit=3)
        evidence = render(result, budget=5000)
        relationships = index.related("40")
    output = [
        "Reposition demo — synthetic repository data; no network requests.\n\n",
        evidence.text,
        "\nA reference worth reviewing:\n",
    ]
    for edge in relationships["edges"]:
        citation = edge["evidence"]
        output.append(
            f"#{edge['source_item']} → #{edge['target_item']}: {citation['text']}\n"
            f"{citation['uri']}\n"
            "This is a source claim; whether the fix works requires review.\n"
        )
    output.append("\nDemo finished; no database was saved.\n")
    return "".join(output)


def _cache_fragment(item: dict, fragment: dict) -> str:
    identity, excerpt = item["identity"], fragment["excerpt"]
    problems = ", ".join(fragment["problems"]) or "none recorded"
    return (
        f"\n[{identity['kind']} #{identity['number']}/{fragment['component']}]\n"
        f"{fragment['uri']}\n"
        f"Verified source; observation: {fragment['observed_at']}; problems: {problems}.\n"
        f"Span: {excerpt['start']}:{excerpt['end']} {excerpt['offset_unit']}; "
        f"excerpt sha256: {excerpt['sha256']}\n"
        f"{excerpt['text']}\n"
        f"Excerpt truncated: {str(excerpt['truncated']).lower()}.\n"
    )


def _cache_demo(query: str) -> str:
    with tempfile.TemporaryDirectory(prefix="reposition-demo-") as folder:
        root = Path(folder)
        _, corpus = make_cache(root / "cache", repeat=12)
        source = TriageCache(root / "cache")
        database = source.index_path(corpus=corpus)
        built = CacheIndex.build(source, database, corpus=corpus)
        with CacheIndex(database) as index:
            result = index.query(
                source, query, corpus=corpus, limit=2, max_snippets_per_item=1, fragment_bytes=96
            )
            output = [
                "Reposition immutable-cache demo — synthetic data; no network requests.\n",
                f"Indexed {built['coverage']['selected_members']} items across nine components.\n",
                f"Corpus: {corpus}\n",
                f"Search: {query}\n",
                "Recorded observations are not a live freshness check.\n",
            ]
            for item in result["items"]:
                output.append(_cache_fragment(item, item["fragments"][0]))
            if result["items"]:
                selected = result["items"][0]["fragments"][0]
                retrieved = index.retrieve(
                    source,
                    (selected["unit_id"],),
                    corpus=corpus,
                    checkpoint=result["checkpoint"],
                    fragment_bytes=1024,
                )
                output.append("\nRead the first result with a larger source window:\n")
                for item in retrieved["items"]:
                    output.append(_cache_fragment(item, item["fragments"][0]))
            else:
                output.append("\nNo matching fragments in this synthetic sample.\n")
            coverage, diagnostics = result["coverage"], result["diagnostics"]
            output.extend(
                (
                    f"\nCoverage: {coverage['selected_members']} selected items; "
                    f"{coverage['missing_members']} missing. Applies only to this synthetic corpus.\n",
                    f"Search limits: {diagnostics['returned_items']} items returned; "
                    f"{diagnostics['groups_omitted_after']} unread groups; "
                    f"{diagnostics['fragments_omitted_per_item']} extra fragments omitted per item.\n",
                    f"Candidate cap reached: {str(diagnostics['candidates_truncated']).lower()}; "
                    f"groups omitted by output budget: {diagnostics['groups_omitted_by_budget']}.\n",
                    f"Discovery JSON: {result['budget']['used_bytes']}/"
                    f"{result['budget']['max_bytes']} UTF-8 bytes.\n",
                    "Rankings and links do not establish duplicate fixes.\n",
                    "Demo finished; the temporary cache and index were removed.\n",
                )
            )
    return "".join(output)
