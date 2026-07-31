"""Bounded deterministic multi-source context selection."""

from __future__ import annotations

import fnmatch
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from revanent.commands.redaction import Redactor
from revanent.context.discovery import ContextDiscoverer
from revanent.context.models import (
    ApprovedContextArtifact,
    BaselineKind,
    ContextAuthority,
    ContextCandidate,
    ContextCandidateKind,
    ContextContentState,
    ContextExclusion,
    ContextImportance,
    ContextItem,
    ContextManifest,
    ContextManifestItem,
    ContextPackage,
    ContextSelectionFailure,
    ContextSelectionRequest,
    ContextSelectionResult,
    ContextSelectionStatus,
    ContextSource,
    ContextTrust,
    ExclusionReason,
    InclusionReason,
    InlineContextEvidence,
    RedactionState,
    context_item_id,
    manifest_id,
)
from revanent.context.reader import (
    ContextFileReaderPort,
    ContextReadResult,
    ContextReadStatus,
    LocalContextFileReader,
)
from revanent.ports.agents import (
    MAX_AGENT_OUTPUT_BYTES,
    AgentArtifactKind,
    AgentArtifactStatus,
    RepositoryPath,
    ScopePath,
)

_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".revanent",
        ".venv",
        "venv",
        "node_modules",
        "build",
        "dist",
        ".pytest_cache",
        "__pycache__",
    }
)
_TRUNCATION_MARKER = "\n[CONTEXT TRUNCATED]\n"
_UNSAFE_SECRET_CONTENT = re.compile(
    r"(?im)^-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|"
    r"^-----BEGIN OPENSSH PRIVATE KEY-----|^ssh-(?:rsa|dss|ed25519)\s+[A-Za-z0-9+/]{80,}"
)
_SECRET_PATH_PARTS = frozenset({".aws", ".azure", ".gcloud"})
_SECRET_FILE_NAMES = frozenset(
    {
        "credentials",
        "application_default_credentials.json",
        "service-account.json",
        "service_account.json",
    }
)


@dataclass(frozen=True, slots=True)
class _Prepared:
    item: ContextItem
    observed_bytes: int


type _Entry = ContextCandidate | InlineContextEvidence | ApprovedContextArtifact


class ContextSelector:
    """Discover, authorize, read, redact, prioritize, and manifest context."""

    def __init__(
        self,
        *,
        redactor: Redactor | None = None,
        reader: ContextFileReaderPort | None = None,
    ) -> None:
        self._redactor = redactor or Redactor()
        self._reader = reader or LocalContextFileReader()
        self._discoverer = ContextDiscoverer(self._reader)

    def select(self, request: ContextSelectionRequest) -> ContextSelectionResult:
        try:
            root = request.root.resolve(strict=True)
        except OSError:
            return self._failure(
                "context_root_unavailable",
                "approved context root is unavailable",
                category=ExclusionReason.ROOT_MISMATCH,
                blocking=True,
            )
        if _is_unc(root):
            return self._failure(
                "context_root_unsupported",
                "UNC context roots are not authorized",
                category=ExclusionReason.ROOT_MISMATCH,
                blocking=True,
            )
        controls = tuple(self._redactor.redact(value) for value in request.trusted_controls)
        if any(_UNSAFE_SECRET_CONTENT.search(value) for value in controls):
            return self._failure(
                "unsafe_trusted_control",
                "trusted control contains unsafe credential material",
                category=ExclusionReason.SECRET,
            )
        control_bytes = len("\n\n".join(controls).encode("utf-8"))
        if control_bytes > MAX_AGENT_OUTPUT_BYTES:
            return self._failure(
                "trusted_control_limit",
                "trusted controls exceed the agent context byte limit",
                category=ExclusionReason.AGGREGATE_LIMIT,
            )
        agent_item_budget = min(
            request.limits.max_total_bytes,
            MAX_AGENT_OUTPUT_BYTES - control_bytes,
        )
        if (
            any(
                item.result.run_id != request.run_id
                or item.result.work_package_id != request.work_package_id
                for item in request.discovery.validation
            )
            or any(
                item.run_id != request.run_id or item.work_package_id != request.work_package_id
                for item in request.discovery.review
            )
            or any(
                item.run_id != request.run_id or item.work_package_id != request.work_package_id
                for item in request.discovery.prior_attempts
            )
            or any(
                item.run_id != request.run_id or item.work_package_id != request.work_package_id
                for item in request.discovery.repair_decisions
            )
        ):
            return self._failure(
                "evidence_correlation_mismatch",
                "context evidence does not belong to the requested run and work package",
                category=ExclusionReason.INVALID_ARTIFACT,
            )
        try:
            allowed = tuple(ScopePath(value) for value in request.task.allowed_paths)
            forbidden = tuple(ScopePath(value) for value in request.task.forbidden_paths)
        except ValidationError:
            return self._failure(
                "invalid_scope_policy",
                "task scope contains an invalid repository-relative pattern",
                category=ExclusionReason.UNSAFE_PATH,
            )
        discovered = self._discoverer.discover(
            root=root,
            role=request.role,
            explicit=request.candidates,
            evidence=request.discovery,
            limits=request.limits,
        )
        inline_evidence = _unique_inline(discovered.inline_evidence)
        artifacts = _unique_artifacts(request.discovery.artifacts)
        total_discovered = len(discovered.candidates) + len(inline_evidence) + len(artifacts)
        if total_discovered > request.limits.max_candidates:
            return self._failure(
                "candidate_limit",
                "deterministic discovery exceeded the candidate limit",
                category=ExclusionReason.DISCOVERY_LIMIT,
            )

        prepared: list[_Prepared] = []
        exclusions: list[ContextExclusion] = []
        total_source_considered = 0
        artifact_source_total = 0
        entries: list[tuple[tuple[int, int, str, str], _Entry]] = []
        entries.extend((_candidate_order(item), item) for item in discovered.candidates)
        entries.extend((_inline_order(item), item) for item in inline_evidence)
        entries.extend((_artifact_order(item), item) for item in artifacts)
        for _, entry in sorted(entries, key=lambda item: item[0]):
            if isinstance(entry, ContextCandidate):
                result = self._prepare_file(
                    root,
                    entry,
                    request,
                    allowed=allowed,
                    forbidden=forbidden,
                )
            elif isinstance(entry, InlineContextEvidence):
                result = self._prepare_inline(
                    entry,
                    request,
                    allowed=allowed,
                    forbidden=forbidden,
                )
            else:
                assert isinstance(entry, ApprovedContextArtifact)
                result = self._prepare_artifact(entry, request)
            if isinstance(result, ContextExclusion):
                if result.source_bytes is not None:
                    total_source_considered += result.source_bytes
                if _entry_importance(entry) is ContextImportance.REQUIRED:
                    return self._required_failure(result)
                exclusions.append(result)
                continue
            total_source_considered += result.observed_bytes
            if result.item.kind is ContextCandidateKind.ARTIFACT:
                artifact_source_total += result.observed_bytes
                if artifact_source_total > request.limits.max_artifact_total_bytes:
                    exclusion = _exclusion_from_item(
                        result.item,
                        ExclusionReason.AGGREGATE_LIMIT,
                        result.observed_bytes,
                    )
                    if result.item.importance is ContextImportance.REQUIRED:
                        return self._required_failure(exclusion)
                    exclusions.append(exclusion)
                    continue
            prepared.append(result)

        selected: list[ContextItem] = []
        retained_total = 0
        duplicate_bytes = 0
        representatives: dict[tuple[str, ContextAuthority, ContextTrust], ContextItem] = {}
        for prepared_item in sorted(prepared, key=lambda item: _item_order(item.item)):
            item = prepared_item.item
            duplicate = representatives.get((item.source_digest_sha256, item.authority, item.trust))
            if duplicate is not None:
                duplicate_bytes += item.retained_bytes
                item = ContextItem.model_validate(
                    {
                        **item.model_dump(mode="python"),
                        "retained_bytes": 0,
                        "truncated_bytes": 0,
                        "state": ContextContentState.REFERENCED,
                        "retained_digest_sha256": hashlib.sha256(b"").hexdigest(),
                        "duplicate_of": duplicate.id,
                        "content": "",
                    }
                )
            else:
                representatives[(item.source_digest_sha256, item.authority, item.trust)] = item
            exceeds_count = len(selected) >= min(request.limits.max_items, 63)
            exceeds_bytes = retained_total + item.retained_bytes > agent_item_budget
            if exceeds_count or exceeds_bytes:
                reason = (
                    ExclusionReason.ITEM_LIMIT if exceeds_count else ExclusionReason.AGGREGATE_LIMIT
                )
                exclusion = _exclusion_from_item(item, reason, prepared_item.observed_bytes)
                if item.importance is ContextImportance.REQUIRED:
                    return self._required_failure(exclusion)
                exclusions.append(exclusion)
                continue
            selected.append(item)
            retained_total += item.retained_bytes

        bounded_exclusions = tuple(
            sorted(exclusions, key=_exclusion_order)[: request.limits.max_exclusions]
        )
        exclusion_overflow = max(0, len(exclusions) - len(bounded_exclusions))
        manifest_items = tuple(
            ContextManifestItem(
                sequence=sequence,
                **item.model_dump(
                    mode="python",
                    exclude={"content", "run_id", "work_package_id"},
                ),
            )
            for sequence, item in enumerate(selected, 1)
        )
        excluded_bytes = sum(item.source_bytes or 0 for item in exclusions)
        baseline_kind = (
            BaselineKind.INJECTED_REPOSITORY
            if request.baseline_bytes is not None
            else BaselineKind.AUTHORIZED_CANDIDATES
        )
        baseline = (
            request.baseline_bytes
            if request.baseline_bytes is not None
            else max(total_source_considered, retained_total)
        )
        if baseline < retained_total:
            return self._failure(
                "invalid_baseline",
                "injected baseline is smaller than retained context",
                category=ExclusionReason.UNSAFE_PATH,
            )
        required_count = _importance_total(entries, ContextImportance.REQUIRED)
        preferred_count = _importance_total(entries, ContextImportance.PREFERRED)
        optional_count = _importance_total(entries, ContextImportance.OPTIONAL)
        warning_values = []
        if exclusion_overflow:
            warning_values.append(f"{exclusion_overflow} exclusions omitted by the bounded ledger")
        if any(item.state is ContextContentState.TRUNCATED for item in selected):
            warning_values.append("one or more context items were deterministically truncated")
        manifest_values: dict[str, object] = {
            "run_id": request.run_id,
            "work_package_id": request.work_package_id,
            "task_id": request.task.id.root,
            "role": request.role,
            "repository_reference": request.repository_reference,
            "worktree_reference": request.worktree_reference,
            "created_at": request.created_at,
            "limits": request.limits,
            "items": manifest_items,
            "exclusions": bounded_exclusions,
            "exclusion_overflow_count": exclusion_overflow,
            "candidate_count": total_discovered,
            "included_count": len(selected),
            "excluded_count": len(exclusions),
            "required_count": required_count,
            "preferred_count": preferred_count,
            "optional_count": optional_count,
            "total_source_bytes_considered": total_source_considered,
            "retained_bytes": retained_total,
            "excluded_bytes": excluded_bytes,
            "truncated_bytes": sum(item.truncated_bytes for item in selected),
            "duplicate_bytes_avoided": duplicate_bytes,
            "baseline_kind": baseline_kind,
            "baseline_bytes": baseline,
            "retained_to_baseline_ratio": 0.0 if baseline == 0 else retained_total / baseline,
            "required_evidence_complete": True,
            "warnings": tuple(sorted(warning_values)),
            "status": (
                ContextSelectionStatus.COMPLETE_WITH_EXCLUSIONS
                if exclusions
                else ContextSelectionStatus.COMPLETE
            ),
        }
        identifier_values = {
            key: value for key, value in manifest_values.items() if key not in {"created_at"}
        }
        manifest = ContextManifest.model_validate(
            {
                "manifest_id": manifest_id(_json_values(identifier_values)),
                **manifest_values,
            }
        )
        artifact_references = tuple(
            sorted(
                (item.artifact for item in selected if item.artifact is not None),
                key=lambda item: f"{item.root_id}:{item.relative_path.root}",
            )
        )
        return ContextSelectionResult(
            package=ContextPackage(
                manifest=manifest,
                trusted_controls=controls,
                untrusted_items=tuple(selected),
                artifact_references=artifact_references,
            )
        )

    def _prepare_file(
        self,
        root: Path,
        candidate: ContextCandidate,
        request: ContextSelectionRequest,
        *,
        allowed: tuple[ScopePath, ...],
        forbidden: tuple[ScopePath, ...],
    ) -> _Prepared | ContextExclusion:
        if request.role not in candidate.roles:
            return _candidate_exclusion(candidate, ExclusionReason.ROLE_MISMATCH)
        scope = _scope_reason(candidate.path, allowed=allowed, forbidden=forbidden)
        if scope is not None:
            return _candidate_exclusion(candidate, scope)
        if any(part.casefold() in _EXCLUDED_PARTS for part in candidate.path.root.split("/")):
            return _candidate_exclusion(candidate, ExclusionReason.EXCLUDED_DIRECTORY)
        if _secret_path(candidate.path):
            return _candidate_exclusion(candidate, ExclusionReason.SECRET)
        read = self._read_with_retry(
            root=root,
            path=candidate.path,
            max_bytes=request.limits.max_source_bytes,
            retries=request.limits.max_read_retries,
        )
        mapped = _read_exclusion(read)
        if mapped is not None:
            return _candidate_exclusion(candidate, mapped, read.observed_bytes)
        if read.observed_bytes is None:
            return _candidate_exclusion(candidate, ExclusionReason.UNSAFE_PATH)
        if (
            read.observed_bytes > request.limits.max_source_bytes
            or len(read.content) > request.limits.max_source_bytes
        ):
            return _candidate_exclusion(
                candidate,
                ExclusionReason.OVERSIZED,
                read.observed_bytes,
            )
        if b"\x00" in read.content:
            return _candidate_exclusion(candidate, ExclusionReason.BINARY, read.observed_bytes)
        try:
            text = read.content.decode("utf-8")
        except UnicodeDecodeError:
            return _candidate_exclusion(
                candidate,
                ExclusionReason.UNSUPPORTED_ENCODING,
                read.observed_bytes,
            )
        if _UNSAFE_SECRET_CONTENT.search(text):
            return _candidate_exclusion(candidate, ExclusionReason.SECRET, read.observed_bytes)
        return self._content_item(
            reference=candidate.path.root,
            path=candidate.path,
            kind=ContextCandidateKind.REPOSITORY_FILE,
            source=candidate.source,
            importance=candidate.importance,
            authority=candidate.authority,
            trust=candidate.trust,
            reasons=candidate.reasons,
            priority=candidate.priority,
            correlations=candidate.correlation_ids,
            requires_complete=candidate.requires_complete,
            text=text,
            observed_bytes=read.observed_bytes,
            request=request,
        )

    def _prepare_inline(
        self,
        evidence: InlineContextEvidence,
        request: ContextSelectionRequest,
        *,
        allowed: tuple[ScopePath, ...],
        forbidden: tuple[ScopePath, ...],
    ) -> _Prepared | ContextExclusion:
        if evidence.path is not None:
            scope = _scope_reason(evidence.path, allowed=allowed, forbidden=forbidden)
            if scope is not None:
                return ContextExclusion(
                    reference=evidence.evidence_id,
                    path=evidence.path,
                    reason=scope,
                    importance=evidence.importance,
                    source=evidence.source,
                    authority=evidence.authority,
                    trust=evidence.trust,
                    source_bytes=len(evidence.content.encode("utf-8")),
                )
        if _UNSAFE_SECRET_CONTENT.search(evidence.content):
            return ContextExclusion(
                reference=evidence.evidence_id,
                path=evidence.path,
                reason=ExclusionReason.SECRET,
                importance=evidence.importance,
                source=evidence.source,
                authority=evidence.authority,
                trust=evidence.trust,
                source_bytes=len(evidence.content.encode("utf-8")),
            )
        return self._content_item(
            reference=evidence.evidence_id,
            path=evidence.path,
            kind=ContextCandidateKind.LOCAL_EVIDENCE,
            source=evidence.source,
            importance=evidence.importance,
            authority=evidence.authority,
            trust=evidence.trust,
            reasons=evidence.reasons,
            priority=evidence.priority,
            correlations=evidence.correlation_ids,
            requires_complete=evidence.requires_complete,
            text=evidence.content,
            observed_bytes=len(evidence.content.encode("utf-8")),
            request=request,
        )

    def _prepare_artifact(
        self,
        artifact: ApprovedContextArtifact,
        request: ContextSelectionRequest,
    ) -> _Prepared | ContextExclusion:
        reference = artifact.reference
        exclusion = _artifact_exclusion_factory(artifact)
        if _is_unc(artifact.root):
            return exclusion(ExclusionReason.ROOT_MISMATCH, None)
        if artifact.run_id != request.run_id or artifact.work_package_id != request.work_package_id:
            return exclusion(ExclusionReason.INVALID_ARTIFACT, None)
        if reference.status is AgentArtifactStatus.TRUNCATED and artifact.requires_complete:
            return exclusion(ExclusionReason.INCOMPLETE, reference.stored_bytes)
        if reference.stored_bytes > request.limits.max_artifact_bytes:
            return exclusion(ExclusionReason.OVERSIZED, reference.stored_bytes)
        if reference.kind is AgentArtifactKind.RAW_OUTPUT and not reference.redacted:
            return exclusion(ExclusionReason.SECRET, reference.stored_bytes)
        read = self._read_with_retry(
            root=artifact.root,
            path=reference.relative_path,
            max_bytes=request.limits.max_artifact_bytes,
            retries=request.limits.max_read_retries,
        )
        mapped = _read_exclusion(read)
        if mapped is not None:
            return exclusion(mapped, read.observed_bytes)
        if (
            read.observed_bytes != reference.stored_bytes
            or len(read.content) != reference.stored_bytes
        ):
            return exclusion(ExclusionReason.FILE_CHANGED, read.observed_bytes)
        if (
            reference.sha256 is not None
            and hashlib.sha256(read.content).hexdigest() != reference.sha256
        ):
            return exclusion(ExclusionReason.FILE_CHANGED, read.observed_bytes)
        if b"\x00" in read.content:
            return exclusion(ExclusionReason.BINARY, read.observed_bytes)
        try:
            text = read.content.decode("utf-8")
        except UnicodeDecodeError:
            return exclusion(ExclusionReason.UNSUPPORTED_ENCODING, read.observed_bytes)
        if _UNSAFE_SECRET_CONTENT.search(text):
            return exclusion(ExclusionReason.SECRET, read.observed_bytes)
        trust = artifact.trust
        authority = artifact.authority
        if reference.kind in {
            AgentArtifactKind.RAW_OUTPUT,
            AgentArtifactKind.PUBLIC_OUTPUT,
            AgentArtifactKind.REVIEW,
        }:
            trust = ContextTrust.UNTRUSTED_PROVIDER
            authority = ContextAuthority.PROVIDER_CLAIM
        prepared = self._content_item(
            reference=f"artifact:{reference.root_id}/{reference.relative_path.root}",
            path=reference.relative_path,
            kind=ContextCandidateKind.ARTIFACT,
            source=ContextSource.ARTIFACT,
            importance=artifact.importance,
            authority=authority,
            trust=trust,
            reasons=(InclusionReason.APPROVED_ARTIFACT,),
            priority=100,
            correlations=(artifact.correlation_id,),
            requires_complete=artifact.requires_complete,
            text=text,
            observed_bytes=read.observed_bytes or 0,
            request=request,
        )
        if isinstance(prepared, _Prepared):
            return _Prepared(
                item=ContextItem.model_validate(
                    {**prepared.item.model_dump(mode="python"), "artifact": reference}
                ),
                observed_bytes=prepared.observed_bytes,
            )
        return prepared

    def _content_item(
        self,
        *,
        reference: str,
        path: RepositoryPath | None,
        kind: ContextCandidateKind,
        source: ContextSource,
        importance: ContextImportance,
        authority: ContextAuthority,
        trust: ContextTrust,
        reasons: tuple[InclusionReason, ...],
        priority: int,
        correlations: tuple[str, ...],
        requires_complete: bool,
        text: str,
        observed_bytes: int,
        request: ContextSelectionRequest,
    ) -> _Prepared | ContextExclusion:
        safe = self._redactor.redact(text)
        safe_bytes = safe.encode("utf-8")
        if len(safe_bytes) > request.limits.max_source_bytes:
            return ContextExclusion(
                reference=reference,
                path=path,
                reason=ExclusionReason.OVERSIZED,
                importance=importance,
                source=source,
                authority=authority,
                trust=trust,
                source_bytes=observed_bytes,
            )
        retained = safe
        state = ContextContentState.COMPLETE
        if len(safe_bytes) > request.limits.max_item_bytes:
            if requires_complete:
                return ContextExclusion(
                    reference=reference,
                    path=path,
                    reason=ExclusionReason.INCOMPLETE,
                    importance=importance,
                    source=source,
                    authority=authority,
                    trust=trust,
                    source_bytes=observed_bytes,
                )
            retained = _truncate_utf8(safe, request.limits.max_item_bytes)
            state = ContextContentState.TRUNCATED
        retained_bytes = retained.encode("utf-8")
        item = ContextItem(
            id=context_item_id(request.run_id, reference, authority, trust),
            run_id=request.run_id,
            work_package_id=request.work_package_id,
            path=path,
            reference=reference,
            kind=kind,
            source=source,
            importance=importance,
            authority=authority,
            trust=trust,
            role=request.role,
            reasons=reasons,
            priority=priority,
            correlation_ids=correlations,
            source_bytes=len(safe_bytes),
            retained_bytes=len(retained_bytes),
            truncated_bytes=len(safe_bytes) - len(retained_bytes),
            state=state,
            source_digest_sha256=hashlib.sha256(safe_bytes).hexdigest(),
            retained_digest_sha256=hashlib.sha256(retained_bytes).hexdigest(),
            redaction=(RedactionState.REDACTED if safe != text else RedactionState.NOT_NEEDED),
            content=retained,
        )
        return _Prepared(item=item, observed_bytes=observed_bytes)

    def _read_with_retry(
        self,
        *,
        root: Path,
        path: RepositoryPath,
        max_bytes: int,
        retries: int,
    ) -> ContextReadResult:
        result = self._reader.read(root=root, path=path, max_bytes=max_bytes)
        for _ in range(retries):
            if result.status is not ContextReadStatus.CHANGED:
                break
            result = self._reader.read(root=root, path=path, max_bytes=max_bytes)
        return result

    @staticmethod
    def _required_failure(exclusion: ContextExclusion) -> ContextSelectionResult:
        blocking = exclusion.reason in {
            ExclusionReason.MISSING,
            ExclusionReason.ROOT_MISMATCH,
            ExclusionReason.FILE_CHANGED,
            ExclusionReason.OVERSIZED,
            ExclusionReason.INCOMPLETE,
        }
        return ContextSelectionResult(
            failure=ContextSelectionFailure(
                code="required_context_unavailable",
                category=exclusion.reason,
                path=exclusion.path,
                message="required context is unavailable, unsafe, incomplete, or out of scope",
                blocking=blocking,
            )
        )

    @staticmethod
    def _failure(
        code: str,
        message: str,
        *,
        category: ExclusionReason | None = None,
        path: RepositoryPath | None = None,
        blocking: bool = False,
    ) -> ContextSelectionResult:
        return ContextSelectionResult(
            failure=ContextSelectionFailure(
                code=code,
                message=message,
                category=category,
                path=path,
                blocking=blocking,
            )
        )


def _truncate_utf8(value: str, maximum: int) -> str:
    marker = _TRUNCATION_MARKER.encode("utf-8")
    available = maximum - len(marker)
    head_size = available * 2 // 3
    tail_size = available - head_size
    encoded = value.encode("utf-8")
    head = encoded[:head_size].decode("utf-8", errors="ignore")
    tail = encoded[-tail_size:].decode("utf-8", errors="ignore") if tail_size else ""
    retained = f"{head}{_TRUNCATION_MARKER}{tail}"
    while len(retained.encode("utf-8")) > maximum:
        tail = tail[1:]
        retained = f"{head}{_TRUNCATION_MARKER}{tail}"
    return retained


def _scope_reason(
    path: RepositoryPath,
    *,
    allowed: tuple[ScopePath, ...],
    forbidden: tuple[ScopePath, ...],
) -> ExclusionReason | None:
    if any(_scope_match(path.root, pattern.root) for pattern in forbidden):
        return ExclusionReason.FORBIDDEN_SCOPE
    if not any(_scope_match(path.root, pattern.root) for pattern in allowed):
        return ExclusionReason.SCOPE_CONFLICT
    return None


def _scope_match(path: str, pattern: str) -> bool:
    path_parts = path.split("/")
    pattern_parts = pattern.split("/")
    insensitive = __import__("os").name == "nt"
    if insensitive:
        path_parts = [part.casefold() for part in path_parts]
        pattern_parts = [part.casefold() for part in pattern_parts]

    def match(path_index: int, pattern_index: int) -> bool:
        while pattern_index < len(pattern_parts):
            current = pattern_parts[pattern_index]
            if current == "**":
                if pattern_index + 1 == len(pattern_parts):
                    return True
                return any(
                    match(next_index, pattern_index + 1)
                    for next_index in range(path_index, len(path_parts) + 1)
                )
            if path_index >= len(path_parts) or not fnmatch.fnmatchcase(
                path_parts[path_index], current
            ):
                return False
            path_index += 1
            pattern_index += 1
        return path_index == len(path_parts)

    return match(0, 0)


def _secret_path(path: RepositoryPath) -> bool:
    parts = tuple(part.casefold() for part in path.root.split("/"))
    name = parts[-1]
    return (
        name == ".env"
        or name.startswith(".env.")
        or name in _SECRET_FILE_NAMES
        or any(part in _SECRET_PATH_PARTS for part in parts)
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
    )


def _read_exclusion(result: ContextReadResult) -> ExclusionReason | None:
    return {
        ContextReadStatus.COMPLETE: None,
        ContextReadStatus.MISSING: ExclusionReason.MISSING,
        ContextReadStatus.ESCAPE: ExclusionReason.SYMLINK_ESCAPE,
        ContextReadStatus.SPECIAL: ExclusionReason.SPECIAL_FILE,
        ContextReadStatus.CHANGED: ExclusionReason.FILE_CHANGED,
        ContextReadStatus.ERROR: ExclusionReason.UNSAFE_PATH,
    }[result.status]


def _candidate_exclusion(
    item: ContextCandidate,
    reason: ExclusionReason,
    source_bytes: int | None = None,
) -> ContextExclusion:
    return ContextExclusion(
        reference=item.path.root,
        path=item.path,
        reason=reason,
        importance=item.importance,
        source=item.source,
        authority=item.authority,
        trust=item.trust,
        source_bytes=source_bytes,
    )


def _exclusion_from_item(
    item: ContextItem, reason: ExclusionReason, observed_bytes: int
) -> ContextExclusion:
    return ContextExclusion(
        reference=item.reference,
        path=item.path,
        reason=reason,
        importance=item.importance,
        source=item.source,
        authority=item.authority,
        trust=item.trust,
        source_bytes=observed_bytes,
    )


def _artifact_exclusion_factory(
    artifact: ApprovedContextArtifact,
) -> Callable[[ExclusionReason, int | None], ContextExclusion]:
    def build(reason: ExclusionReason, source_bytes: int | None = None) -> ContextExclusion:
        return ContextExclusion(
            reference=(
                f"artifact:{artifact.reference.root_id}/{artifact.reference.relative_path.root}"
            ),
            path=artifact.reference.relative_path,
            reason=reason,
            importance=artifact.importance,
            source=ContextSource.ARTIFACT,
            authority=artifact.authority,
            trust=artifact.trust,
            source_bytes=source_bytes,
        )

    return build


def _entry_importance(item: _Entry) -> ContextImportance:
    return item.importance


def _importance_rank(value: ContextImportance) -> int:
    return {
        ContextImportance.REQUIRED: 0,
        ContextImportance.PREFERRED: 1,
        ContextImportance.OPTIONAL: 2,
    }[value]


def _candidate_order(item: ContextCandidate) -> tuple[int, int, str, str]:
    return (_importance_rank(item.importance), item.priority, item.source.value, item.path.root)


def _inline_order(item: InlineContextEvidence) -> tuple[int, int, str, str]:
    return (
        _importance_rank(item.importance),
        item.priority,
        item.source.value,
        item.evidence_id,
    )


def _artifact_order(item: ApprovedContextArtifact) -> tuple[int, int, str, str]:
    return (
        _importance_rank(item.importance),
        100,
        ContextSource.ARTIFACT.value,
        f"{item.reference.root_id}:{item.reference.relative_path.root}",
    )


def _item_order(item: ContextItem) -> tuple[int, int, str, str]:
    return (_importance_rank(item.importance), item.priority, item.source.value, item.reference)


def _exclusion_order(item: ContextExclusion) -> tuple[int, str, str]:
    return (_importance_rank(item.importance), item.reference, item.reason.value)


def _importance_total(
    entries: list[tuple[tuple[int, int, str, str], _Entry]],
    importance: ContextImportance,
) -> int:
    return sum(_entry_importance(item) is importance for _, item in entries)


def _is_unc(path: Path) -> bool:
    return str(path).startswith(("\\\\", "//"))


def _json_values(values: dict[str, object]) -> dict[str, object]:
    result = to_jsonable_python(values)
    assert isinstance(result, dict)
    return result


def _unique_inline(
    values: tuple[InlineContextEvidence, ...],
) -> tuple[InlineContextEvidence, ...]:
    unique: dict[str, InlineContextEvidence] = {}
    for item in sorted(values, key=_inline_order):
        existing = unique.get(item.evidence_id)
        if existing is None:
            unique[item.evidence_id] = item
        elif existing != item:
            raise ValueError("one inline evidence identity cannot describe different content")
    return tuple(sorted(unique.values(), key=_inline_order))


def _unique_artifacts(
    values: tuple[ApprovedContextArtifact, ...],
) -> tuple[ApprovedContextArtifact, ...]:
    unique: dict[str, ApprovedContextArtifact] = {}
    for item in sorted(values, key=_artifact_order):
        key = f"{item.reference.root_id}:{item.reference.relative_path.root}"
        existing = unique.get(key)
        if existing is None:
            unique[key] = item
        elif existing != item:
            raise ValueError("one artifact identity cannot describe different evidence")
    return tuple(sorted(unique.values(), key=_artifact_order))
