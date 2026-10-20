"""Offline native CI log adapter; no acquisition or install-code imports.

Validation helpers adapted from triage-o-mator bin/_ci_logs.py, _checks.py and
_acquire.py, Copyright (c) 2026 EFrMG, MIT; see NOTICE and retained license.
The adapter preserves raw retained bytes and binds separately selected snapshots.
"""

from __future__ import annotations

import base64
import hashlib
import re

from . import _triage_contract as contract
from .cache_source import parse_json
from .models import canonical, sha256
from .projections import _chunks, pointer_get

fields = contract.fields
timestamp = contract.timestamp
same_item = contract.same_item
same_repository = contract.same_repository
MAX_RETAINED = 65536
REDIRECT_RESERVATION = 11
FAILURES = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}


def repository(full_name, host, database_id=None, node_id=None):
    result = dict(host=host, full_name=full_name, database_id=database_id, node_id=node_id)
    contract.validate_repository(result)
    return result


def source_lines(data):
    parts = data.split(b"\n")
    return [part + b"\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def summary_identity(raw, number, repo):
    identity = dict(
        kind="pr", number=raw.get("number"), database_id=raw.get("id"), node_id=raw.get("node_id")
    )
    contract.validate_item(identity)
    same_item(dict(kind="pr", number=number, database_id=None, node_id=None), identity)
    if identity["database_id"] is None or identity["node_id"] is None:
        raise ValueError("CI summary lacks stable PR identity")

    if (
        raw.get("html_url") != f"https://{repo['host']}/{repo['full_name']}/pull/{number}"
        or raw.get("state") not in ("open", "closed")
        or "body" not in raw
        or raw["body"] is not None
        and not isinstance(raw["body"], str)
        or type(raw.get("merged")) is not bool
    ):
        raise ValueError("CI summary lacks observed PR scope/state")

    base, head = raw.get("base") or {}, raw.get("head") or {}
    base_repo = base.get("repo") or {}
    same_repository(
        repo,
        repository(
            base_repo.get("full_name"), repo["host"], base_repo.get("id"), base_repo.get("node_id")
        ),
    )
    revision = dict(
        updated_at=raw.get("updated_at"), base_sha=base.get("sha"), head_sha=head.get("sha")
    )
    contract.validate_revision(revision)
    if any(value is None for value in revision.values()):
        raise ValueError("CI summary lacks required revisions")

    return identity, revision


class Reads:
    """Deduplicate and bound all referenced object bytes, including dependencies."""

    def __init__(self, maximum):
        self.maximum = maximum
        self.objects = {}
        self.payloads = {}

    def reserve(self, ref):
        contract.validate_ref(ref)
        key = (ref["sha256"], ref["format"])
        if key in self.objects and self.objects[key] != ref["bytes"]:
            raise ValueError("conflicting source object lengths")

        if key not in self.objects:
            if sum(self.objects.values()) + ref["bytes"] > self.maximum:
                raise ValueError(
                    "selected source objects exceed the verification byte ceiling; raise --max-verify-bytes"
                )
            self.objects[key] = ref["bytes"]

    def object(self, source, name, descriptor, kind):
        ref = descriptor["object"]
        self.reserve(ref)
        key = (ref["sha256"], ref["format"])
        if key not in self.payloads:
            self.payloads[key] = source.object(name, descriptor, kind)
        else:
            contract.validate_payload(name, descriptor, kind, self.payloads[key])
        return self.payloads[key]


def evidence(source, number, snapshot, reads):
    manifest = source.manifest(snapshot)
    record = next(
        (
            r
            for r in manifest["items"]
            if r["identity"]["kind"] == "pr" and r["identity"]["number"] == number
        ),
        None,
    )
    if record is None:
        raise ValueError("CI capture source PR is missing from snapshot")

    data = {}
    for name in ("summary", "checks"):
        descriptor = record["components"].get(name)
        if not descriptor or not descriptor["object"]:
            raise ValueError("CI source summary/checks unavailable")
        data[name] = parse_json(reads.object(source, name, descriptor, "pr"))

    return dict(repository=manifest["repository"], **record, data=data)


def selected(source, number, snapshot, check_id, source_repo, reads):
    value = evidence(source, number, snapshot, reads)
    entries, _ = verify(value)
    entry = next(
        (
            e
            for e in entries
            if e["kind"] == "check_run"
            and e["data"]["id"] == check_id
            and e["repository"]["full_name"] == source_repo
        ),
        None,
    )
    if (
        entry is None
        or entry["data"].get("status") != "completed"
        or entry["data"].get("conclusion") not in FAILURES
        or entry["head_sha"] != value["revision"]["head_sha"]
    ):
        raise ValueError("CI capture must bind a selected completed failed check at the PR head")

    return value, entry


def api_url(host, resource):
    return (
        f"https://api.github.com/{resource}"
        if host == "github.com"
        else f"https://{host}/api/v3/{resource}"
    )


def bounded(value, maximum=256):
    if not isinstance(value, str):
        return None

    return "".join(char if char.isprintable() else " " for char in value)[:maximum]


def observed_time(value):
    try:
        return timestamp(value)
    except (ValueError, TypeError):
        return None


def object_field(data, name):
    value = data.get(name)
    if value is None:
        return {}

    if not isinstance(value, dict):
        raise ValueError(f"check source {name} must be an object")

    return value


def source_repository(raw, host):
    if not isinstance(raw, dict):
        raise ValueError("check source repository is unavailable")

    result = repository(raw.get("full_name"), host, raw.get("id"), raw.get("node_id"))
    if result["database_id"] is None or result["node_id"] is None:
        raise ValueError("check source repository lacks stable identity")

    return result


def verify(evidence):
    """Content hashes do not prove typed source scope: verify the raw envelopes before interpreting them."""
    raw = evidence["data"].get("summary")
    entries = evidence["data"].get("checks", [])
    if raw is None:
        if entries:
            raise ValueError("checks cannot be interpreted without their selected PR summary")

        return [], []

    for side in ("base", "head"):
        object_field(object_field(raw, side), "repo")

    normalized = dict(raw, state="closed" if raw.get("state") == "merged" else raw.get("state"))
    identity, revision = summary_identity(
        normalized, evidence["identity"]["number"], evidence["repository"]
    )
    same_item(evidence["identity"], identity)
    if revision != evidence["revision"]:
        raise ValueError("selected PR summary revision differs from its manifest")

    allowed = [evidence["repository"]]
    raw_head = object_field(raw, "head").get("repo")
    if raw_head:
        head = source_repository(raw_head, evidence["repository"]["host"])
        if head["full_name"] == allowed[0]["full_name"]:
            same_repository(allowed[0], head)
        else:
            allowed.append(head)

    descriptor = evidence["components"].get("checks")
    sha = descriptor["revision"]["head_sha"] if descriptor else None
    suites, seen = {}, set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or entry.get("kind") not in ("check_suite", "check_run", "status")
            or not isinstance(entry.get("data"), dict)
        ):
            raise ValueError("invalid typed checks envelope")

        source = entry.get("repository")
        expected = next(
            (
                repo
                for repo in allowed
                if isinstance(source, dict) and repo["full_name"] == source.get("full_name")
            ),
            None,
        )
        if expected is None:
            raise ValueError("checks envelope belongs to a foreign repository")

        same_repository(expected, source)
        fetched = observed_time(entry.get("fetched_at"))
        if (
            entry.get("head_sha") != sha
            or fetched is None
            or fetched > timestamp(descriptor["fetched_at"])
        ):
            raise ValueError("checks envelope has a mismatched head or invalid observation time")

        data, kind = entry["data"], entry["kind"]
        identifier = data.get("id")
        key = (source["full_name"], kind, identifier)
        if type(identifier) is not int or identifier <= 0 or key in seen:
            raise ValueError("checks envelope has an invalid or repeated source identity")

        seen.add(key)
        prefix = f"repos/{source['full_name']}"
        if kind == "status":
            resource = f"{prefix}/commits/{sha}/statuses"
        elif kind == "check_suite":
            resource = f"{prefix}/commits/{sha}/check-suites"
            suites[(source["full_name"], identifier)] = data
        else:
            suite_id = object_field(data, "check_suite").get("id")
            if type(suite_id) is not int or suite_id <= 0:
                raise ValueError("check run lacks its suite identity")

            resource = f"{prefix}/check-suites/{suite_id}/check-runs?filter=all"

        if entry.get("resource") != resource or (kind != "status" and data.get("head_sha") != sha):
            raise ValueError("checks source resource or raw head differs from its envelope")

        if isinstance(data.get("repository"), dict):
            same_repository(expected, source_repository(data["repository"], expected["host"]))

    for entry in entries:
        if entry["kind"] != "check_run":
            continue

        data, source = entry["data"], entry["repository"]["full_name"]
        suite = suites.get((source, data["check_suite"]["id"]))
        app = object_field(data, "app")
        if suite is None or app.get("id") != object_field(suite, "app").get("id"):
            raise ValueError("check run has an absent suite or mismatched app")

        if data["check_suite"].get("head_sha", sha) != sha:
            raise ValueError("check run suite has a foreign head")

    for index, entry in enumerate(entries):
        entry["source_index"] = index

    return entries, allowed


def equivalent_source(old_evidence, old_entry, evidence, entry):
    """Reuse binds the full retained check and suite projections, excluding observation timestamps."""
    same_repository(old_evidence["repository"], evidence["repository"])
    same_repository(old_entry["repository"], entry["repository"])
    if (
        old_evidence["identity"] != evidence["identity"]
        or old_entry["head_sha"] != entry["head_sha"]
        or old_entry["data"] != entry["data"]
    ):
        return False

    def suite(parent, child):
        return next(
            value["data"]
            for value in parent["data"]["checks"]
            if value["kind"] == "check_suite"
            and value["repository"] == child["repository"]
            and value["data"]["id"] == child["data"]["check_suite"]["id"]
        )

    return suite(old_evidence, old_entry) == suite(evidence, entry)


def validate_metadata(run, job, source, entry, run_id, attempt, job_id):
    host, repo = source["host"], source["full_name"]
    prefix = f"repos/{repo}"
    if not isinstance(run, dict) or not isinstance(job, dict):
        raise ValueError("CI run/job metadata is malformed")

    raw_repo = run.get("repository") or {}
    if not isinstance(raw_repo, dict):
        raise ValueError("CI run repository metadata is malformed")
    same_repository(
        source,
        repository(raw_repo.get("full_name"), host, raw_repo.get("id"), raw_repo.get("node_id")),
    )
    if (
        any(type(run.get(key)) is not int for key in ("id", "run_attempt", "check_suite_id"))
        or run.get("id") != run_id
        or run.get("run_attempt") != attempt
        or run.get("head_sha") != entry["head_sha"]
        or run.get("check_suite_id") != entry["data"]["check_suite"]["id"]
        or run.get("url") != api_url(host, f"{prefix}/actions/runs/{run_id}")
    ):
        raise ValueError("CI run attempt differs from the selected check repository, suite or head")

    if (
        any(type(job.get(key)) is not int for key in ("id", "run_id"))
        or job.get("id") != job_id
        or job.get("run_id") != run_id
        or job.get("head_sha") != entry["head_sha"]
        or job.get("url") != api_url(host, f"{prefix}/actions/jobs/{job_id}")
        or job.get("check_run_url") != api_url(host, f"{prefix}/check-runs/{entry['data']['id']}")
        or "run_attempt" in job
        and (type(job["run_attempt"]) is not int or job["run_attempt"] != attempt)
    ):
        raise ValueError("CI job differs from the selected check, run attempt or head")
    if job.get("status") != "completed" or job.get("conclusion") not in FAILURES:
        raise ValueError("CI logs require a completed failed job")


def packet(source, snapshot, number, ref, reads):
    contract.validate_ref(ref)
    if ref["format"] != "json" or not 1 <= ref["bytes"] <= min(2000000, source.max_object_bytes):
        raise ValueError("CI log packet exceeds its bounded typed object limit")
    reads.reserve(ref)
    key = (ref["sha256"], ref["format"])
    if key not in reads.payloads:
        raw = source._bytes(
            ("objects", contract.object_name(ref)), min(2000000, source.max_object_bytes)
        )
        if len(raw) != ref["bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError("CI log packet checksum or size mismatch")
        reads.payloads[key] = raw.decode("utf-8")
    value = parse_json(reads.payloads[key])
    try:
        return validate_packet(source, snapshot, number, value, reads)
    except (TypeError, KeyError, IndexError, UnicodeError) as exc:
        raise ValueError("malformed native CI packet or source binding") from exc


def validate_packet(source, snapshot, number, packet, reads):
    required = (
        "schema_version",
        "artifact",
        "repository",
        "number",
        "snapshot_id",
        "source_repository",
        "head_sha",
        "check_id",
        "check_name",
        "run_id",
        "attempt",
        "job_id",
        "check_source",
        "run",
        "job",
        "membership",
        "membership_resource",
        "log_resource",
        "fetched_at",
        "download_partial",
        "binding_revalidated",
        "observed_bytes",
        "observed_lines",
        "observed_sha256",
        "selection_basis",
        "segments",
        "omissions",
        "request_budget",
        "max_log_bytes",
        "requests",
        "reserved_requests",
    )
    fields(packet, required)
    if (
        type(packet["schema_version"]) is not int
        or packet["schema_version"] != 1
        or packet["artifact"] != "ci-failure-log"
        or packet["number"] != number
    ):
        raise ValueError("CI log packet differs from the selected PR/snapshot")
    if (
        any(
            type(packet[key]) is not int or packet[key] < 1
            for key in ("number", "check_id", "run_id", "attempt", "job_id")
        )
        or type(packet["download_partial"]) is not bool
        or not isinstance(packet["segments"], list)
    ):
        raise ValueError("CI log packet has malformed identities or coverage")
    if (
        type(packet["binding_revalidated"]) is not bool
        or not packet["binding_revalidated"]
        and not packet["download_partial"]
    ):
        raise ValueError("CI log packet has malformed binding revalidation")
    timestamp(packet["fetched_at"])
    if (
        not isinstance(packet["selection_basis"], str)
        or len(packet["selection_basis"]) > 500
        or not isinstance(packet["omissions"], list)
        or len(packet["omissions"]) > 10
        or any(not isinstance(value, str) or len(value) > 1000 for value in packet["omissions"])
    ):
        raise ValueError("CI log packet presentation metadata is malformed")
    evidence, entry = selected(
        source,
        number,
        packet["snapshot_id"],
        packet["check_id"],
        packet["source_repository"]["full_name"],
        reads,
    )
    if packet["snapshot_id"] != snapshot:
        current_evidence, current_entry = selected(
            source,
            number,
            snapshot,
            packet["check_id"],
            packet["source_repository"]["full_name"],
            reads,
        )
        if not packet["binding_revalidated"] or not equivalent_source(
            evidence, entry, current_evidence, current_entry
        ):
            raise ValueError("CI log reuse differs from its exact verified source evidence")

    same_repository(evidence["repository"], packet["repository"])
    same_repository(entry["repository"], packet["source_repository"])
    fields(packet["check_source"], ("component", "object", "index"))
    if (
        packet["check_source"]["component"] != "checks"
        or type(packet["check_source"]["index"]) is not int
        or packet["check_source"]["index"] < 0
    ):
        raise ValueError("CI check source index/component is malformed")
    contract.validate_ref(packet["check_source"]["object"])
    expected_source = dict(
        component="checks",
        object=evidence["components"]["checks"]["object"],
        index=entry["source_index"],
    )
    if (
        packet["head_sha"] != entry["head_sha"]
        or packet["check_source"] != expected_source
        or packet["check_name"] != bounded(entry["data"].get("name"))
    ):
        raise ValueError("CI log packet differs from its selected source evidence")

    validate_metadata(
        packet["run"],
        packet["job"],
        entry["repository"],
        entry,
        packet["run_id"],
        packet["attempt"],
        packet["job_id"],
    )
    validate_metadata(
        packet["run"],
        packet["membership"],
        entry["repository"],
        entry,
        packet["run_id"],
        packet["attempt"],
        packet["job_id"],
    )
    prefix = f"repos/{entry['repository']['full_name']}/actions"
    membership_prefix = (
        f"{prefix}/runs/{packet['run_id']}/attempts/{packet['attempt']}/jobs?per_page=100&page="
    )
    if (
        packet["log_resource"] != f"{prefix}/jobs/{packet['job_id']}/logs"
        or not isinstance(packet["membership_resource"], str)
        or not re.fullmatch(
            re.escape(membership_prefix) + r"[1-9][0-9]*", packet["membership_resource"]
        )
    ):
        raise ValueError("CI log source resources differ from the selected identities")
    if (
        any(
            type(packet[key]) is not int or packet[key] < 0
            for key in ("observed_bytes", "observed_lines", "requests", "reserved_requests")
        )
        or packet["observed_bytes"] > 4194304
        or not isinstance(packet["observed_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", packet["observed_sha256"])
    ):
        raise ValueError("CI log observation bounds are malformed")
    if (
        type(packet["request_budget"]) is not int
        or not 1 <= packet["request_budget"] <= 100
        or packet["requests"] + packet["reserved_requests"] > packet["request_budget"]
        or packet["reserved_requests"] not in (0, REDIRECT_RESERVATION)
    ):
        raise ValueError("CI log request bounds are malformed")
    if (
        type(packet["max_log_bytes"]) is not int
        or not 1 <= packet["max_log_bytes"] <= 4194304
        or packet["observed_bytes"] > packet["max_log_bytes"]
    ):
        raise ValueError("CI log transfer bounds are malformed")
    total = previous_byte = previous_line = 0
    for segment in packet["segments"]:
        fields(
            segment,
            (
                "start_line",
                "end_line",
                "start_byte",
                "end_byte",
                "last_line_complete",
                "content_base64",
            ),
        )
        if (
            any(
                type(segment[key]) is not int
                for key in ("start_line", "end_line", "start_byte", "end_byte")
            )
            or not previous_line
            < segment["start_line"]
            <= segment["end_line"]
            <= packet["observed_lines"]
            or not previous_byte
            <= segment["start_byte"]
            < segment["end_byte"]
            <= packet["observed_bytes"]
            or type(segment["last_line_complete"]) is not bool
        ):
            raise ValueError("CI log segment bounds are malformed")
        raw = base64.b64decode(segment["content_base64"], validate=True)
        total += len(raw)
        lines = source_lines(raw)
        if (
            len(raw) != segment["end_byte"] - segment["start_byte"]
            or len(lines) != segment["end_line"] - segment["start_line"] + 1
            or total > MAX_RETAINED
        ):
            raise ValueError("CI log segment source ranges are malformed")
        if segment["last_line_complete"] != raw.endswith(b"\n"):
            raise ValueError("CI log segment line completeness is malformed")
        previous_byte, previous_line = segment["end_byte"], segment["end_line"]

    return packet


class Selection:
    """Freeze exact native associations before indexing; pointers are build inputs only."""

    def __init__(self, source, selection, maximum):
        self.pointers = {}
        self.captures = {}
        self.coverage = dict(
            captures=0,
            inspected_members=0,
            lookups=0,
            missing=0,
            capped_members=0,
            capped_checks=0,
            unavailable=0,
            partial=0,
            partial_source_members=0,
            unsupported_providers=0,
            non_utf8_segments=0,
            omission_count=0,
            basis="separately captured logs; not snapshot completeness",
        )
        reads = Reads(128 * 1024 * 1024)
        for member in selection.members:
            record = member["record"]
            if record is None or record["identity"]["kind"] != "pr":
                continue
            if self.coverage["lookups"] >= maximum or self.coverage["inspected_members"] >= maximum:
                self.coverage["capped_members"] += 1
                continue
            self.coverage["inspected_members"] += 1
            checks = record["components"].get("checks")
            summary = record["components"].get("summary")
            if not checks or not checks["object"] or not summary or not summary["object"]:
                self.coverage["unavailable"] += 1
                continue

            if (
                any(
                    descriptor["status"] != "complete" or descriptor["truncated"]
                    for descriptor in (checks, summary)
                )
                or checks["pagination_complete"] is not True
            ):
                self.coverage["partial_source_members"] += 1

            number, snapshot = record["identity"]["number"], member["snapshot_id"]
            value = evidence(source, number, snapshot, reads)
            try:
                entries, _ = verify(value)
            except (ValueError, TypeError, KeyError):
                self.coverage["unavailable"] += 1
                continue

            for entry in entries:
                if (
                    entry["kind"] != "check_run"
                    or entry["data"].get("status") != "completed"
                    or entry["data"].get("conclusion") not in FAILURES
                ):
                    continue
                if self.coverage["lookups"] >= maximum:
                    self.coverage["capped_checks"] += 1
                    continue
                self.coverage["lookups"] += 1
                if (entry["data"].get("app") or {}).get("slug") != "github-actions":
                    self.coverage["unsupported_providers"] += 1
                    continue

                scope = dict(
                    repository=selection.repository,
                    number=number,
                    snapshot_id=snapshot,
                    source_repository=entry["repository"]["full_name"],
                    check_id=entry["data"]["id"],
                )
                name = sha256(canonical(scope)) + ".json"
                path = source.path("ci-logs", name)
                if not path.exists():
                    self.pointers[name] = None
                    self.coverage["missing"] += 1
                    continue
                raw = source._bytes(("ci-logs", name), min(4096, source.max_metadata_bytes))
                pointer = parse_json(raw)
                fields(pointer, ("schema_version", "artifact", "scope", "log_object"))
                if (
                    type(pointer["schema_version"]) is not int
                    or pointer["schema_version"] != 1
                    or pointer["artifact"] != "ci-failure-log-pointer"
                    or canonical(pointer["scope"]) != canonical(scope)
                ):
                    raise ValueError("CI association differs from the exact selected scope")
                self.pointers[name] = raw
                captured = packet(source, snapshot, number, pointer["log_object"], reads)
                if (
                    captured["check_id"] != scope["check_id"]
                    or captured["source_repository"]["full_name"] != scope["source_repository"]
                ):
                    raise ValueError("CI association selected a foreign check")
                self.captures.setdefault((snapshot, number), []).append(
                    (pointer["log_object"], captured)
                )
                self.coverage["captures"] += 1
                self.coverage["partial"] += int(
                    captured["download_partial"] or not captured["binding_revalidated"]
                )
                self.coverage["unavailable"] += int(not captured["segments"])
                self.coverage["omission_count"] += len(captured["omissions"])
                for segment in captured["segments"]:
                    try:
                        base64.b64decode(segment["content_base64"], validate=True).decode("utf-8")
                    except UnicodeError:
                        self.coverage["non_utf8_segments"] += 1

        self.digest = sha256(
            canonical(
                {
                    name: hashlib.sha256(raw).hexdigest() if raw is not None else None
                    for name, raw in sorted(self.pointers.items())
                }
            )
        )
        self.coverage.update(selection_digest=self.digest, maximum_captures=maximum)

    def recheck(self, source):
        for name, expected in self.pointers.items():
            path = source.path("ci-logs", name)
            raw = (
                source._bytes(("ci-logs", name), min(4096, source.max_metadata_bytes))
                if path.exists()
                else None
            )
            if raw != expected:
                raise ValueError("CI association changed during build; restart cache-index")


def context(packet, snapshot):
    return dict(
        selected_snapshot_id=snapshot,
        capture_snapshot_id=packet["snapshot_id"],
        source_repository=packet["source_repository"],
        provider="github-actions",
        status="retained" if packet["segments"] else "unavailable",
        check_status="completed",
        job_status=packet["job"]["status"],
        job_conclusion=packet["job"]["conclusion"],
        run_status=packet["run"].get("status"),
        run_conclusion=packet["run"].get("conclusion"),
        check_id=packet["check_id"],
        check_name=packet["check_name"],
        workflow_name=bounded(packet["run"].get("name")),
        job_name=bounded(packet["job"].get("name")),
        run_id=packet["run_id"],
        attempt=packet["attempt"],
        job_id=packet["job_id"],
        head_sha=packet["head_sha"],
        download_partial=packet["download_partial"],
        binding_revalidated=packet["binding_revalidated"],
        omissions=packet["omissions"],
        check_source=packet["check_source"],
        selection_basis=packet["selection_basis"],
    )


def original(packet, locator):
    if locator["coordinate_space"] == "ci-segment-utf8":
        return base64.b64decode(
            packet["segments"][locator["segment_index"]]["content_base64"], validate=True
        ).decode("utf-8")
    return pointer_get(packet, locator["pointer"])


def project(packet, policy):
    sources = []
    for index, segment in enumerate(packet["segments"]):
        try:
            text = base64.b64decode(segment["content_base64"], validate=True).decode("utf-8")
        except UnicodeError:
            continue
        sources.append(
            (
                text,
                dict(
                    pointer=f"/segments/{index}/content_base64",
                    field="log",
                    segment_index=index,
                    coordinate_space="ci-segment-utf8",
                    original_start_byte=segment["start_byte"],
                    original_end_byte=segment["end_byte"],
                    original_start_line=segment["start_line"],
                    original_end_line=segment["end_line"],
                    last_line_complete=segment["last_line_complete"],
                ),
            )
        )
    for pointer in ("/check_name", "/run/name", "/job/name"):
        try:
            text = pointer_get(packet, pointer)
        except KeyError:
            continue
        if isinstance(text, str) and text:
            sources.append(
                (
                    text,
                    dict(
                        pointer=pointer, field="name", coordinate_space="decoded-json-string-utf8"
                    ),
                )
            )
    for text, origin in sources:
        for start, end, first, last, total, fragment in _chunks(text, policy):
            yield (
                dict(
                    **origin,
                    start=start,
                    end=end,
                    line_start=first,
                    line_end=last,
                    source_bytes=total,
                    boundary_start=0,
                    boundary_end=total,
                    record_pointer=None,
                    record_id=packet["check_id"],
                    filename=None,
                ),
                fragment,
            )


def uri(ref, locator):
    suffix = (
        f"segment={locator['segment_index']}"
        if "segment_index" in locator
        else f"pointer={locator['pointer']}"
    )
    return f"ci-failure-log:sha256:{ref['sha256']}#{suffix}"


def records(selection, member, policy):
    for ref, packet in selection.captures.get(
        (member["snapshot_id"], member["identity"]["number"]), ()
    ):
        descriptor = describe(packet, ref, member["record"]["revision"])
        yield descriptor, packet, context(packet, member["snapshot_id"]), project(packet, policy)


def describe(packet, ref, revision):
    return dict(
        object=ref,
        revision=revision,
        fetched_at=packet["fetched_at"],
        status="partial"
        if packet["download_partial"] or not packet["binding_revalidated"] or packet["omissions"]
        else "retained",
        truncated=packet["download_partial"],
        pagination_complete=None,
        expected_count=None,
        received_count=len(packet["segments"]),
        error=None,
    )


def coverage(descriptor, outcome):
    return {
        key: descriptor[key]
        for key in (
            "status",
            "truncated",
            "pagination_complete",
            "expected_count",
            "received_count",
        )
    } | dict(error_present=descriptor["error"] is not None, outcome=outcome)


ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b\r\n]*(?:\x07|\x1b\\))")


def search_text(component, locator, text):
    """Mask CI escapes without moving code points; citable projections retain raw bytes."""
    return (
        ANSI.sub(lambda match: " " * len(match.group()), text)
        if component == "ci_logs" and "segment_index" in locator
        else text
    )
