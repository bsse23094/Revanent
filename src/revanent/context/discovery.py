"""Deterministic typed evidence discovery for context selection."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from revanent.context.models import (
    ContextAuthority,
    ContextCandidate,
    ContextDiscoveryInput,
    ContextImportance,
    ContextLimits,
    ContextSource,
    ContextTrust,
    GoverningContext,
    InclusionReason,
    InlineContextEvidence,
)
from revanent.context.reader import ContextFileReaderPort, ContextReadStatus
from revanent.ports.agents import AgentRole, RepositoryPath
from revanent.ports.validation import ValidationCommandClass, ValidationStatus


@dataclass(frozen=True, slots=True)
class DiscoveredContext:
    candidates: tuple[ContextCandidate, ...]
    inline_evidence: tuple[InlineContextEvidence, ...]


class ContextDiscoverer:
    """Expand only explicit typed evidence with bounded Python/test rules."""

    def __init__(self, reader: ContextFileReaderPort) -> None:
        self._reader = reader

    def discover(
        self,
        *,
        root: Path,
        role: AgentRole,
        explicit: tuple[ContextCandidate, ...],
        evidence: ContextDiscoveryInput,
        limits: ContextLimits,
    ) -> DiscoveredContext:
        candidates = list(explicit)
        candidates.extend(self._path_candidates(evidence, role))
        candidates.extend(self._governing_candidates(evidence.governing, role))
        inline = list(evidence.inline_evidence)
        inline.extend(self._validation_evidence(evidence, role))
        inline.extend(self._review_evidence(evidence, role))
        inline.extend(self._attempt_evidence(evidence, role))
        merged = _merge_candidates(candidates)
        expanded = self._expand_python_dependencies(root, merged, role, limits)
        merged = _merge_candidates([*merged, *expanded])
        tests = self._discover_tests(root, merged, role, limits)
        merged = _merge_candidates([*merged, *tests])
        return DiscoveredContext(
            candidates=tuple(merged),
            inline_evidence=tuple(sorted(inline, key=_inline_key)),
        )

    @staticmethod
    def _path_candidates(
        evidence: ContextDiscoveryInput, role: AgentRole
    ) -> tuple[ContextCandidate, ...]:
        values: list[ContextCandidate] = []
        specs = (
            (
                evidence.explicit_paths,
                ContextSource.TASK_PATH,
                ContextImportance.REQUIRED,
                InclusionReason.EXPLICIT_TASK_PATH,
                50,
            ),
            (
                evidence.changed_paths,
                ContextSource.CHANGED_PATH,
                ContextImportance.PREFERRED,
                InclusionReason.CHANGED_PATH,
                60,
            ),
            (
                evidence.diff_paths,
                ContextSource.DIFF,
                ContextImportance.PREFERRED,
                InclusionReason.DIFF_PATH,
                70,
            ),
        )
        for paths, source, importance, reason, priority in specs:
            for path in paths:
                values.append(
                    _file_candidate(
                        path,
                        source=source,
                        importance=importance,
                        reason=reason,
                        priority=_role_priority(priority, role, source),
                        requires_complete=importance is ContextImportance.REQUIRED,
                    )
                )
        for validation in evidence.validation:
            for path in validation.affected_paths:
                values.append(
                    _file_candidate(
                        path,
                        source=ContextSource.VALIDATION,
                        importance=ContextImportance.REQUIRED,
                        reason=InclusionReason.VALIDATION_FAILURE,
                        priority=_role_priority(30, role, ContextSource.VALIDATION),
                        correlations=(validation.attempt_id,),
                        requires_complete=True,
                    )
                )
        for finding in evidence.review:
            if finding.path is None or not finding.unresolved:
                continue
            required = finding.severity.value in {"HIGH", "CRITICAL"}
            values.append(
                _file_candidate(
                    finding.path,
                    source=ContextSource.REVIEW,
                    importance=(
                        ContextImportance.REQUIRED if required else ContextImportance.PREFERRED
                    ),
                    reason=InclusionReason.REVIEW_FINDING,
                    priority=_role_priority(35 if required else 75, role, ContextSource.REVIEW),
                    correlations=(finding.finding_id, *finding.correlation_ids),
                    requires_complete=required,
                )
            )
        for attempt in evidence.prior_attempts:
            if attempt.path is not None and attempt.unresolved:
                values.append(
                    _file_candidate(
                        attempt.path,
                        source=ContextSource.PRIOR_ATTEMPT,
                        importance=ContextImportance.PREFERRED,
                        reason=InclusionReason.PRIOR_ATTEMPT,
                        priority=_role_priority(110, role, ContextSource.PRIOR_ATTEMPT),
                        correlations=(attempt.attempt_id, *attempt.correlation_ids),
                    )
                )
        return tuple(values)

    @staticmethod
    def _governing_candidates(
        governing: GoverningContext | None, role: AgentRole
    ) -> tuple[ContextCandidate, ...]:
        if governing is None:
            return ()
        paths: list[tuple[RepositoryPath, ContextImportance, int]] = [
            (governing.active_work_package, ContextImportance.REQUIRED, 10)
        ]
        if governing.include_agents:
            paths.append((RepositoryPath("AGENTS.md"), ContextImportance.REQUIRED, 5))
        if governing.include_architecture:
            paths.append((RepositoryPath("docs/ARCHITECTURE.md"), ContextImportance.PREFERRED, 90))
        if governing.include_requirements:
            paths.append((RepositoryPath("docs/REQUIREMENTS.md"), ContextImportance.PREFERRED, 91))
        if governing.include_security:
            paths.append(
                (
                    RepositoryPath("docs/SECURITY_AND_THREAT_MODEL.md"),
                    ContextImportance.PREFERRED,
                    92,
                )
            )
        if governing.include_workflow:
            paths.append(
                (
                    RepositoryPath("docs/WORKFLOW_STATE_MACHINE.md"),
                    ContextImportance.PREFERRED,
                    93,
                )
            )
        paths.extend((path, ContextImportance.PREFERRED, 94) for path in governing.adrs)
        return tuple(
            _file_candidate(
                path,
                source=ContextSource.GOVERNING,
                importance=importance,
                reason=InclusionReason.GOVERNING_INSTRUCTION,
                priority=_role_priority(priority, role, ContextSource.GOVERNING),
                authority=ContextAuthority.REPOSITORY_GOVERNANCE,
                trust=ContextTrust.REPOSITORY_GOVERNANCE,
                requires_complete=importance is ContextImportance.REQUIRED,
            )
            for path, importance, priority in paths
        )

    @staticmethod
    def _validation_evidence(
        evidence: ContextDiscoveryInput, role: AgentRole
    ) -> tuple[InlineContextEvidence, ...]:
        values: list[InlineContextEvidence] = []
        for wrapped in evidence.validation:
            for command in wrapped.result.commands:
                if command.status in {
                    ValidationStatus.PASSED,
                    ValidationStatus.PASSED_WITH_ADVISORIES,
                    ValidationStatus.NOT_RUN,
                }:
                    continue
                required = command.classification is ValidationCommandClass.REQUIRED
                diagnostic = "\n".join(
                    part
                    for part in (
                        f"command={command.command_id.root}",
                        f"status={command.status.value}",
                        f"correlation={command.correlation_id}",
                        (f"failure={command.failure.code}" if command.failure is not None else ""),
                        command.stderr.text,
                        command.stdout.text,
                    )
                    if part
                )
                values.append(
                    InlineContextEvidence(
                        evidence_id=f"validation.{wrapped.attempt_id}.{command.command_id.root}",
                        source=ContextSource.VALIDATION,
                        importance=(
                            ContextImportance.REQUIRED if required else ContextImportance.PREFERRED
                        ),
                        authority=ContextAuthority.LOCAL_DETERMINISTIC_EVIDENCE,
                        trust=ContextTrust.UNTRUSTED_DIAGNOSTIC,
                        reasons=(InclusionReason.VALIDATION_FAILURE,),
                        priority=_role_priority(25, role, ContextSource.VALIDATION),
                        content=diagnostic,
                        roles=(role,),
                        correlation_ids=tuple(sorted({wrapped.attempt_id, command.correlation_id})),
                        requires_complete=required,
                    )
                )
        return tuple(values)

    @staticmethod
    def _review_evidence(
        evidence: ContextDiscoveryInput, role: AgentRole
    ) -> tuple[InlineContextEvidence, ...]:
        values: list[InlineContextEvidence] = []
        for finding in evidence.review:
            if not finding.unresolved:
                continue
            required = finding.severity.value in {"HIGH", "CRITICAL"}
            values.append(
                InlineContextEvidence(
                    evidence_id=f"review.{finding.finding_id}",
                    source=ContextSource.REVIEW,
                    importance=(
                        ContextImportance.REQUIRED if required else ContextImportance.PREFERRED
                    ),
                    authority=ContextAuthority.PROVIDER_CLAIM,
                    trust=ContextTrust.UNTRUSTED_PROVIDER,
                    reasons=(InclusionReason.REVIEW_FINDING,),
                    priority=_role_priority(30 if required else 80, role, ContextSource.REVIEW),
                    content=(
                        f"finding={finding.finding_id}\n"
                        f"severity={finding.severity.value}\n"
                        f"summary={finding.summary}\n"
                        f"required_change={finding.required_change}"
                    ),
                    path=finding.path,
                    roles=(role,),
                    correlation_ids=tuple(sorted({finding.finding_id, *finding.correlation_ids})),
                    requires_complete=required,
                )
            )
        return tuple(values)

    @staticmethod
    def _attempt_evidence(
        evidence: ContextDiscoveryInput, role: AgentRole
    ) -> tuple[InlineContextEvidence, ...]:
        values: list[InlineContextEvidence] = []
        for attempt in evidence.prior_attempts:
            if attempt.unresolved:
                values.append(
                    InlineContextEvidence(
                        evidence_id=f"attempt.{attempt.attempt_id}",
                        source=ContextSource.PRIOR_ATTEMPT,
                        importance=ContextImportance.PREFERRED,
                        authority=ContextAuthority.LOCAL_DETERMINISTIC_EVIDENCE,
                        trust=ContextTrust.TRUSTED_LOCAL_EVIDENCE,
                        reasons=(InclusionReason.PRIOR_ATTEMPT,),
                        priority=_role_priority(110, role, ContextSource.PRIOR_ATTEMPT),
                        content=attempt.summary,
                        path=attempt.path,
                        roles=(role,),
                        correlation_ids=tuple(
                            sorted({attempt.attempt_id, *attempt.correlation_ids})
                        ),
                    )
                )
        for decision in evidence.repair_decisions:
            if decision.unresolved:
                values.append(
                    InlineContextEvidence(
                        evidence_id=f"decision.{decision.decision_id}",
                        source=ContextSource.REPAIR_DECISION,
                        importance=ContextImportance.REQUIRED,
                        authority=ContextAuthority.LOCAL_DETERMINISTIC_EVIDENCE,
                        trust=ContextTrust.TRUSTED_LOCAL_EVIDENCE,
                        reasons=(InclusionReason.UNRESOLVED_DECISION,),
                        priority=_role_priority(40, role, ContextSource.REPAIR_DECISION),
                        content=decision.summary,
                        roles=(role,),
                        correlation_ids=tuple(
                            sorted({decision.decision_id, *decision.correlation_ids})
                        ),
                        requires_complete=True,
                    )
                )
        return tuple(values)

    def _expand_python_dependencies(
        self,
        root: Path,
        candidates: list[ContextCandidate],
        role: AgentRole,
        limits: ContextLimits,
    ) -> tuple[ContextCandidate, ...]:
        if limits.max_dependency_depth == 0 or limits.max_dependencies == 0:
            return ()
        initial = tuple(
            item
            for item in candidates
            if item.path.root.endswith(".py")
            and item.source
            in {ContextSource.TASK_PATH, ContextSource.CHANGED_PATH, ContextSource.DIFF}
        )
        queue = [(item.path, 0) for item in initial]
        visited = {item.path.root for item in initial}
        discovered: list[ContextCandidate] = []
        while queue and len(discovered) < limits.max_dependencies:
            parent, depth = queue.pop(0)
            if depth >= limits.max_dependency_depth:
                continue
            result = self._reader.read(root=root, path=parent, max_bytes=limits.max_source_bytes)
            if (
                result.status is not ContextReadStatus.COMPLETE
                or len(result.content) > limits.max_source_bytes
            ):
                continue
            try:
                tree = ast.parse(result.content.decode("utf-8"), filename=parent.root)
            except (SyntaxError, UnicodeDecodeError, ValueError):
                continue
            for path in _import_paths(parent, tree):
                if path.root in visited:
                    continue
                exists = self._reader.read(root=root, path=path, max_bytes=1)
                if exists.status is not ContextReadStatus.COMPLETE:
                    continue
                visited.add(path.root)
                candidate = _file_candidate(
                    path,
                    source=ContextSource.DEPENDENCY,
                    importance=ContextImportance.OPTIONAL,
                    reason=InclusionReason.DIRECT_DEPENDENCY,
                    priority=_role_priority(120 + depth, role, ContextSource.DEPENDENCY),
                    parent=parent,
                )
                discovered.append(candidate)
                queue.append((path, depth + 1))
                if len(discovered) >= limits.max_dependencies:
                    break
        return tuple(discovered)

    def _discover_tests(
        self,
        root: Path,
        candidates: list[ContextCandidate],
        role: AgentRole,
        limits: ContextLimits,
    ) -> tuple[ContextCandidate, ...]:
        stems = sorted(
            {
                PurePosixPath(item.path.root).stem
                for item in candidates
                if item.path.root.endswith(".py")
                and not PurePosixPath(item.path.root).name.startswith("test_")
            }
        )
        names = tuple(f"test_{stem}.py" for stem in stems[: limits.max_tests])
        if not names or limits.max_tests == 0:
            return ()
        matches = self._reader.find_named_files(
            root=root,
            search_roots=(RepositoryPath("tests"),),
            names=names,
            max_entries=limits.max_test_scan_entries,
        )
        return tuple(
            _file_candidate(
                path,
                source=ContextSource.TEST,
                importance=ContextImportance.PREFERRED,
                reason=InclusionReason.CORRESPONDING_TEST,
                priority=_role_priority(85, role, ContextSource.TEST),
                trust=ContextTrust.UNTRUSTED_TEST,
            )
            for path in matches[: limits.max_tests]
        )


def _file_candidate(
    path: RepositoryPath,
    *,
    source: ContextSource,
    importance: ContextImportance,
    reason: InclusionReason,
    priority: int,
    authority: ContextAuthority = ContextAuthority.REPOSITORY_CONTENT,
    trust: ContextTrust = ContextTrust.UNTRUSTED_REPOSITORY,
    correlations: tuple[str, ...] = (),
    parent: RepositoryPath | None = None,
    requires_complete: bool = False,
) -> ContextCandidate:
    return ContextCandidate(
        path=path,
        source=source,
        importance=importance,
        authority=authority,
        trust=trust,
        reasons=(reason,),
        priority=priority,
        correlation_ids=tuple(sorted(set(correlations))),
        parent_path=parent,
        requires_complete=requires_complete,
    )


def _merge_candidates(values: list[ContextCandidate]) -> list[ContextCandidate]:
    merged: dict[str, ContextCandidate] = {}
    for candidate in sorted(values, key=_candidate_key):
        existing = merged.get(candidate.path.root)
        if existing is None:
            merged[candidate.path.root] = candidate
            continue
        winner = min((existing, candidate), key=_candidate_key)
        merged[candidate.path.root] = ContextCandidate(
            path=winner.path,
            source=winner.source,
            importance=min(
                (existing.importance, candidate.importance),
                key=_importance_rank,
            ),
            authority=winner.authority,
            trust=winner.trust,
            reasons=tuple(
                sorted(set(existing.reasons + candidate.reasons), key=lambda item: item.value)
            ),
            priority=min(existing.priority, candidate.priority),
            roles=tuple(sorted(set(existing.roles + candidate.roles), key=lambda item: item.value)),
            correlation_ids=tuple(
                sorted(set(existing.correlation_ids + candidate.correlation_ids))
            ),
            parent_path=winner.parent_path,
            requires_complete=existing.requires_complete or candidate.requires_complete,
        )
    return sorted(merged.values(), key=_candidate_key)


def _candidate_key(item: ContextCandidate) -> tuple[int, int, str, str, str]:
    return (
        _importance_rank(item.importance),
        item.priority,
        item.source.value,
        item.path.root.casefold(),
        item.path.root,
    )


def _inline_key(item: InlineContextEvidence) -> tuple[int, int, str, str]:
    return (
        _importance_rank(item.importance),
        item.priority,
        item.source.value,
        item.evidence_id,
    )


def _importance_rank(value: ContextImportance) -> int:
    return {
        ContextImportance.REQUIRED: 0,
        ContextImportance.PREFERRED: 1,
        ContextImportance.OPTIONAL: 2,
    }[value]


def _role_priority(base: int, role: AgentRole, source: ContextSource) -> int:
    if role is AgentRole.REVIEWER and source in {
        ContextSource.CHANGED_PATH,
        ContextSource.DIFF,
        ContextSource.VALIDATION,
    }:
        return max(0, base - 10)
    if role is AgentRole.REPAIRER and source in {
        ContextSource.REVIEW,
        ContextSource.VALIDATION,
        ContextSource.REPAIR_DECISION,
    }:
        return max(0, base - 10)
    return base


def _import_paths(parent: RepositoryPath, tree: ast.AST) -> tuple[RepositoryPath, ...]:
    modules: set[str] = set()
    parent_parts = list(PurePosixPath(parent.root).with_suffix("").parts)
    if parent_parts and parent_parts[0] == "src":
        parent_parts = parent_parts[1:]
    package = parent_parts[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative_prefix = package[: max(0, len(package) - node.level + 1)]
                base = [
                    *relative_prefix,
                    *(node.module.split(".") if node.module else []),
                ]
                modules.add(".".join(base))
                modules.update(".".join([*base, alias.name]) for alias in node.names)
            elif node.module:
                modules.add(node.module)
                modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    paths: set[RepositoryPath] = set()
    for module in sorted(modules):
        module_parts = tuple(part for part in module.split(".") if part)
        if not module_parts:
            continue
        for path_prefix in ((), ("src",)):
            for suffix in (
                (f"{module_parts[-1]}.py",),
                (*module_parts[-1:], "__init__.py"),
            ):
                candidate_parts = (*path_prefix, *module_parts[:-1], *suffix)
                try:
                    paths.add(RepositoryPath("/".join(candidate_parts)))
                except ValueError:
                    continue
    return tuple(sorted(paths, key=lambda item: item.root))
