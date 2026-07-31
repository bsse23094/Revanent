"""Fail-closed branch, revision, and Git command-surface policies."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from revanent.ports.git import InvalidGitReferenceError, ProtectedBranchError

_REF_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FULL_COMMIT = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_SHORT_COMMIT = re.compile(r"^[0-9a-fA-F]{7,39}$")
_FORBIDDEN_BRANCH_PARTS = {".", "..", "@"}


def validate_branch_name(branch: str, *, required_namespace: str = "revanent/") -> str:
    """Validate a literal dedicated local branch before Git sees it."""
    if (
        not isinstance(branch, str)
        or not branch
        or len(branch) > 255
        or branch.startswith(("-", "/"))
        or branch.endswith(("/", ".", ".lock"))
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or "\\" in branch
        or any(ord(character) < 32 or ord(character) == 127 for character in branch)
    ):
        raise InvalidGitReferenceError("branch name is not a safe literal reference")
    components = branch.split("/")
    if any(
        component in _FORBIDDEN_BRANCH_PARTS or _REF_COMPONENT.fullmatch(component) is None
        for component in components
    ):
        raise InvalidGitReferenceError("branch name is not a safe literal reference")
    if required_namespace and not branch.startswith(required_namespace):
        raise InvalidGitReferenceError("branch is outside the Revanent-owned namespace")
    return branch


def validate_revision(revision: str) -> str:
    """Accept only unambiguous, option-safe commit/ref spellings."""
    if not isinstance(revision, str) or not revision or len(revision) > 1_024:
        raise InvalidGitReferenceError("base revision is invalid")
    if revision == "HEAD" or _FULL_COMMIT.fullmatch(revision) or _SHORT_COMMIT.fullmatch(revision):
        return revision
    if revision.startswith("refs/heads/") or revision.startswith("refs/tags/"):
        suffix = revision.split("/", 2)[2]
        validate_branch_name(suffix, required_namespace="")
        return revision
    raise InvalidGitReferenceError("base revision must be HEAD, a commit ID, or a full local ref")


@dataclass(frozen=True, slots=True)
class ProtectedBranchPolicy:
    """Configurable exact/pattern rules plus a locally discovered default branch."""

    exact_names: frozenset[str] = frozenset({"main", "master"})
    namespace_patterns: tuple[str, ...] = ("release/*", "protected/*")
    owned_namespace: str = "revanent/"

    def __post_init__(self) -> None:
        if not self.owned_namespace or not self.owned_namespace.endswith("/"):
            raise ValueError("owned branch namespace must be a non-empty prefix ending in '/'")
        for name in self.exact_names:
            validate_branch_name(name, required_namespace="")
        for pattern in self.namespace_patterns:
            if (
                not pattern
                or pattern.startswith(("-", "/"))
                or "\\" in pattern
                or ".." in pattern
                or "@{" in pattern
                or "[" in pattern
                or "]" in pattern
                or "?" in pattern
            ):
                raise ValueError("protected branch patterns may use only literal text and '*'")
        if any(self.is_protected(self.owned_namespace + "probe") for _ in (0,)):
            raise ValueError("owned branch namespace cannot overlap protected-branch rules")

    def is_protected(self, branch: str, *, default_branch: str | None = None) -> bool:
        if branch in self.exact_names or branch == default_branch:
            return True
        return any(fnmatch.fnmatchcase(branch, pattern) for pattern in self.namespace_patterns)

    def require_mutable_owned_branch(
        self,
        branch: str,
        *,
        default_branch: str | None,
    ) -> str:
        if self.is_protected(branch, default_branch=default_branch):
            raise ProtectedBranchError("protected branches cannot be used for task mutation")
        validated = validate_branch_name(branch, required_namespace=self.owned_namespace)
        return validated
