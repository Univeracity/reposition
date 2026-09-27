"""Reposition: local repository search and traceable evidence."""

from .cache_index import CacheIndex
from .cache_source import TriageCache
from .importers import load_snapshot
from .index import Index
from .models import Hit, Record, SearchResult, Snapshot
from .projections import ProjectionPolicy
from .render import Evidence, render

__version__ = "0.2.0.dev0"
__all__ = [
    "CacheIndex",
    "Evidence",
    "Hit",
    "Index",
    "ProjectionPolicy",
    "Record",
    "SearchResult",
    "Snapshot",
    "TriageCache",
    "load_snapshot",
    "render",
]
