"""Read-only adapter for triage-o-mator's immutable evidence format v1."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import _triage_contract as contract
from .models import canonical, sha256

PROFILES = {
    "backlog": {"summary", "comments", "files", "diff", "closing_issues"},
    "closure-watch": {"summary", "comments", "timeline"},
    "discussion": {"summary", "comments"},
    "pr-context": {"summary", "comments", "files", "reviews", "review_comments"},
    "pr-code": {"summary", "comments", "files", "diff"},
    "pr-comparison": contract.COMPONENTS - {"timeline"},
}
SCOPES = {"open-prs": {"pr"}, "open-issues": {"issue"}, "open-items": {"pr", "issue"}}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def parse_json(raw: bytes | str) -> Any:
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=contract.reject_constant)


@dataclass(frozen=True)
class CacheSelection:
    repository: dict[str, Any]
    scope: dict[str, Any]
    checkpoint: str
    members: tuple[dict[str, Any], ...]
    coverage: dict[str, Any]


class TriageCache:
    """Only reads cache.json, selected manifests, corpus records, and objects.

    No current-item pointer, install code, acquisition, ledger, or live fallback.
    Limits apply before reading a component, not to the complete cache size.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        max_object_bytes: int = 64 * 1024 * 1024,
        max_metadata_bytes: int = 16 * 1024 * 1024,
    ):
        for value in (max_object_bytes, max_metadata_bytes):
            if type(value) is not int or value < 1:
                raise ValueError("source read limits must be positive integers")
        self.root = Path(root).absolute()
        self.max_object_bytes = max_object_bytes
        self.max_metadata_bytes = max_metadata_bytes
        self.repository = self._metadata()
        self._verified_manifests = OrderedDict()

    def path(self, *parts: str) -> Path:
        path = self.root
        if path.is_symlink():
            raise ValueError("cache root must not be a symlink")
        for part in parts:
            if not part or Path(part).name != part or part in (".", ".."):
                raise ValueError("invalid cache artifact path")
            path /= part
            if path.is_symlink():
                raise ValueError("cache artifacts must not be symlinks")
        return path

    def _bytes(self, parts: tuple[str, ...], limit: int) -> bytes:
        path = self.path(*parts)
        if path.stat().st_size > limit:
            raise ValueError(
                "source artifact exceeds its read limit; raise the explicit source limit"
            )
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("source artifact exceeds its read limit")
        return raw

    def _json(self, *parts: str) -> dict[str, Any]:
        try:
            value = parse_json(self._bytes(parts, self.max_metadata_bytes))
            if not isinstance(value, dict):
                raise ValueError("not an object")
            return value
        except (UnicodeError, ValueError) as exc:
            raise ValueError("invalid source metadata; preserve it and check the format") from exc

    def _metadata(self) -> dict[str, Any]:
        value = self._json("cache.json")
        try:
            contract.fields(value, ("schema_version", "artifact", "repository"))
            contract.version(value, "evidence-cache")
            contract.validate_repository(value["repository"])
            if all(value["repository"][key] is None for key in ("database_id", "node_id")):
                raise ValueError("unbound repository")
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(
                "unsupported or unbound evidence-cache metadata; bind stable repository IDs first"
            ) from exc
        return value["repository"]

    def manifest(self, identifier: str) -> dict[str, Any]:
        self._digest(identifier)
        raw = self._bytes(("snapshots", identifier + ".json"), self.max_metadata_bytes)
        fingerprint = (identifier, hashlib.sha256(raw).hexdigest())
        try:
            value = parse_json(raw)
            if fingerprint not in self._verified_manifests:
                contract.validate_snapshot(value)
                self._verified_manifests[fingerprint] = True
                if len(self._verified_manifests) > 128:
                    self._verified_manifests.popitem(last=False)
            contract.same_repository(self.repository, value["repository"])
            if value["snapshot_id"] != identifier:
                raise ValueError("snapshot filename mismatch")
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("invalid immutable snapshot identity, checksum, or format") from exc
        return value

    @staticmethod
    def _digest(identifier: str) -> None:
        if not isinstance(identifier, str) or not contract.DIGEST.fullmatch(identifier):
            raise ValueError("source identifiers must be SHA-256 digests")

    def _sealed(self, identifier: str, suffix: str, artifact: str) -> dict[str, Any]:
        self._digest(identifier)
        value = self._json("corpora", identifier + suffix)
        try:
            contract.version(value, artifact)
            expected = sha256(canonical({k: v for k, v in value.items() if k != "checksum"}))
            if value.get("checksum") != expected:
                raise ValueError("record checksum mismatch")
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid corpus record checksum or version") from exc
        return value

    def _scope(
        self,
        snapshot: str | None,
        corpus: str | None,
    ) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any] | None]:
        if bool(snapshot) == bool(corpus):
            raise ValueError("select exactly one immutable snapshot or corpus")
        current = self._metadata()
        contract.same_repository(self.repository, current)
        contract.same_repository(current, self.repository)
        if snapshot:
            manifest = self.manifest(snapshot)
            scope = {"snapshot_id": snapshot, "corpus_id": None, "corpus_checkpoint": None}
            return scope, sha256(canonical([current, scope])), manifest, None
        plan = self._sealed(corpus, ".plan.json", "evidence-corpus-plan")
        try:
            contract.fields(
                plan,
                (
                    "schema_version",
                    "artifact",
                    "checksum",
                    "repository",
                    "inventory_snapshot",
                    "scope",
                    "profile",
                    "max_age",
                    "members",
                ),
                ("reuse_snapshots",),
            )
            contract.same_repository(current, plan["repository"])
            if (
                plan["checksum"] != corpus
                or plan["scope"] not in SCOPES
                or plan["profile"] not in PROFILES
            ):
                raise ValueError("invalid corpus selection")
            contract.natural(plan["max_age"], "maximum age")
            inventory = self.manifest(plan["inventory_snapshot"])
            members = sorted(
                (
                    r["identity"]
                    for r in inventory["items"]
                    if r["identity"]["kind"] in SCOPES[plan["scope"]]
                ),
                key=lambda r: (r["kind"], r["number"]),
            )
            if plan["members"] != members:
                raise ValueError("corpus membership differs from frozen inventory")
            state_path = self.path("corpora", corpus + ".state.json")
            if state_path.exists():
                state = self._sealed(corpus, ".state.json", "evidence-corpus-state")
                contract.fields(
                    state,
                    (
                        "schema_version",
                        "artifact",
                        "checksum",
                        "plan_id",
                        "updated_at",
                        "status",
                        "items",
                        "last_run",
                    ),
                )
                contract.timestamp(state["updated_at"])
            else:
                state = {
                    "plan_id": corpus,
                    "updated_at": None,
                    "status": "pending",
                    "items": {
                        f"{m['kind']}:{m['number']}": {
                            "attempts": 0,
                            "snapshot_id": None,
                            "outcome": "pending",
                            "error": None,
                        }
                        for m in members
                    },
                    "last_run": None,
                }
            if state["plan_id"] != corpus or state["status"] not in {
                "pending",
                "running",
                "stopped",
                "interrupted",
                "finished",
            }:
                raise ValueError("invalid corpus progress")
            if not isinstance(state["items"], dict) or set(state["items"]) != {
                f"{m['kind']}:{m['number']}" for m in members
            }:
                raise ValueError("invalid corpus progress membership")
            for entry in state["items"].values():
                contract.fields(entry, ("attempts", "snapshot_id", "outcome", "error"))
                contract.natural(entry["attempts"], "attempts")
                if entry["outcome"] not in {"pending", "running", "complete", "gaps", "error"}:
                    raise ValueError("invalid corpus outcome")
                if entry["error"] is not None and not isinstance(entry["error"], str):
                    raise ValueError("invalid corpus error")
                if entry["snapshot_id"] is not None:
                    self._digest(entry["snapshot_id"])
                if entry["outcome"] == "pending" and (
                    entry["attempts"] or entry["snapshot_id"] or entry["error"]
                ):
                    raise ValueError("pending member has results")
                if entry["outcome"] != "pending" and entry["attempts"] == 0:
                    raise ValueError("attempted member has no attempts")
                if entry["outcome"] in {"complete", "gaps"} and entry["snapshot_id"] is None:
                    raise ValueError("evidence outcome has no snapshot")
            if state["status"] == "finished" and any(
                e["outcome"] not in {"complete", "gaps"} for e in state["items"].values()
            ):
                raise ValueError("finished corpus has unfinished members")
            if state["last_run"] is not None:
                run = state["last_run"]
                contract.fields(run, ("started_at", "request_budget", "requests", "reason"))
                contract.timestamp(run["started_at"])
                contract.natural(run["request_budget"], "request budget", 1)
                contract.natural(run["requests"], "requests")
                if run["requests"] > run["request_budget"] or (
                    run["reason"] is not None and not isinstance(run["reason"], str)
                ):
                    raise ValueError("invalid corpus run")
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(
                "invalid corpus selection or progress; preserve source records"
            ) from exc
        state_token = sha256(canonical(state))
        scope = {"snapshot_id": None, "corpus_id": corpus, "corpus_checkpoint": state_token}
        return scope, sha256(canonical([current, scope])), plan, state

    def checkpoint(self, *, snapshot: str | None = None, corpus: str | None = None) -> str:
        if bool(snapshot) == bool(corpus):
            raise ValueError("select exactly one immutable snapshot or corpus")
        current = self._metadata()
        contract.same_repository(self.repository, current)
        contract.same_repository(current, self.repository)
        if snapshot:
            self._digest(snapshot)
            stamps = [
                hashlib.sha256(
                    self._bytes(("snapshots", snapshot + ".json"), self.max_metadata_bytes)
                ).hexdigest()
            ]
        else:
            plan = self._sealed(corpus, ".plan.json", "evidence-corpus-plan")
            contract.same_repository(current, plan["repository"])
            if plan["checksum"] != corpus:
                raise ValueError("corpus plan filename mismatch")
            stamps = [
                hashlib.sha256(
                    self._bytes(
                        ("snapshots", plan["inventory_snapshot"] + ".json"), self.max_metadata_bytes
                    )
                ).hexdigest()
            ]
            state = self.path("corpora", corpus + ".state.json")
            stamps.append(
                hashlib.sha256(
                    self._bytes(("corpora", corpus + ".state.json"), self.max_metadata_bytes)
                ).hexdigest()
                if state.exists()
                else "unstarted"
            )
        return sha256(canonical([current, snapshot, corpus, stamps]))

    def index_path(self, *, snapshot: str | None = None, corpus: str | None = None) -> Path:
        if bool(snapshot) == bool(corpus):
            raise ValueError("name a snapshot/corpus or supply an explicit index database")
        self._digest(snapshot or corpus)
        return (
            self.root.parent
            / "reposition"
            / (("snapshot-" if snapshot else "corpus-") + (snapshot or corpus) + ".sqlite")
        )

    def select(self, *, snapshot: str | None = None, corpus: str | None = None) -> CacheSelection:
        checkpoint = self.checkpoint(snapshot=snapshot, corpus=corpus)
        scope, _, selected, state = self._scope(snapshot, corpus)
        members = []
        if snapshot:
            members = [
                {
                    "snapshot_id": snapshot,
                    "outcome": "selected",
                    "record": r,
                    "identity": r["identity"],
                }
                for r in selected["items"]
            ]
        else:
            stable = {}
            manifests = OrderedDict()
            for identity in selected["members"]:
                entry = state["items"][f"{identity['kind']}:{identity['number']}"]
                record = None
                if entry["snapshot_id"]:
                    identifier = entry["snapshot_id"]
                    if identifier not in manifests:
                        manifests[identifier] = self.manifest(identifier)
                        if len(manifests) > 4:
                            manifests.popitem(last=False)
                    manifests.move_to_end(identifier)
                    manifest = manifests[identifier]
                    record = next(
                        (
                            r
                            for r in manifest["items"]
                            if (r["identity"]["kind"], r["identity"]["number"])
                            == (identity["kind"], identity["number"])
                        ),
                        None,
                    )
                    if record is None:
                        raise ValueError("corpus member missing from its pinned snapshot")
                    contract.same_item(identity, record["identity"])
                    if entry["outcome"] == "complete":
                        for name in PROFILES[selected["profile"]]:
                            descriptor = record["components"].get(name)
                            if descriptor is None or any(
                                p != "observation outside freshness window"
                                for p in contract.component_problems(
                                    name,
                                    descriptor,
                                    record["revision"],
                                    descriptor["fetched_at"] or manifest["completed_at"],
                                    selected["max_age"],
                                )
                            ):
                                raise ValueError(
                                    "complete corpus member references incomplete evidence"
                                )
                observed = record["identity"] if record else identity
                for key in ("database_id", "node_id"):
                    value = observed[key]
                    stable_key = (key, observed["kind"] if key == "database_id" else None, value)
                    if value is not None and stable_key in stable:
                        raise ValueError("stable item ID appears under multiple corpus members")
                    stable[stable_key] = True
                members.append(
                    {
                        "snapshot_id": entry["snapshot_id"],
                        "outcome": entry["outcome"],
                        "record": record,
                        "identity": identity,
                    }
                )
        outcomes = {}
        for member in members:
            outcomes[member["outcome"]] = outcomes.get(member["outcome"], 0) + 1
        coverage = {
            "selected_members": len(members),
            "outcomes": outcomes,
            "missing_members": sum(m["record"] is None for m in members),
            "basis": "explicit frozen selection; absence is not a negative verdict",
        }
        return CacheSelection(self.repository, scope, checkpoint, tuple(members), coverage)

    def object(self, name: str, descriptor: dict[str, Any], kind: str) -> str:
        try:
            reference = descriptor["object"]
            contract.validate_ref(reference)
            if reference["bytes"] > self.max_object_bytes:
                raise ValueError("source object exceeds max_object_bytes")
            raw = self._bytes(("objects", contract.object_name(reference)), self.max_object_bytes)
            if (
                len(raw) != reference["bytes"]
                or hashlib.sha256(raw).hexdigest() != reference["sha256"]
            ):
                raise ValueError("checksum mismatch")
            text = raw.decode("utf-8")
            contract.validate_payload(name, descriptor, kind, text)
            if reference["format"] == "json":
                parse_json(text)
            return text
        except (KeyError, TypeError, UnicodeError, ValueError) as exc:
            raise ValueError(
                "selected evidence object failed verification or exceeded its read limit"
            ) from exc

    def history_stamp(self) -> dict[str, int]:
        stat = self.path("snapshots").stat()
        return {
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
        }
