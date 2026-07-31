"""Bounded parsers for Git's stable NUL-delimited porcelain formats."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from revanent.ports.git import MalformedGitOutputError, RepositoryStatus, WorktreeSnapshot


@dataclass(frozen=True, slots=True)
class ParsedStatus:
    branch: str | None
    detached: bool
    head_commit: str
    upstream: str | None
    status: RepositoryStatus


def _records(text: str, *, label: str) -> list[str]:
    if "\ufffd" in text:
        raise MalformedGitOutputError(f"{label} contains undecodable path data")
    if not text.endswith("\x00"):
        raise MalformedGitOutputError(f"{label} is incomplete")
    return text[:-1].split("\x00")


def parse_status_porcelain_v2(text: str) -> ParsedStatus:
    """Parse `status --porcelain=v2 -z --branch` without path quoting."""
    records = _records(text, label="Git status output")
    staged: set[str] = set()
    unstaged: set[str] = set()
    untracked: set[str] = set()
    conflicted: set[str] = set()
    ignored: set[str] = set()
    branch: str | None = None
    detached = False
    head_commit: str | None = None
    upstream: str | None = None
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            raise MalformedGitOutputError("Git status output contains an empty record")
        if record.startswith("# branch.oid "):
            value = record.removeprefix("# branch.oid ")
            if value == "(initial)":
                raise MalformedGitOutputError("unborn repositories are not supported")
            head_commit = value
            continue
        if record.startswith("# branch.head "):
            value = record.removeprefix("# branch.head ")
            if value == "(detached)":
                detached = True
                branch = None
            elif not value or value.startswith("("):
                raise MalformedGitOutputError("Git status reported an unsupported HEAD state")
            else:
                branch = value
            continue
        if record.startswith("# branch.upstream "):
            value = record.removeprefix("# branch.upstream ")
            upstream = value or None
            continue
        if record.startswith("# "):
            continue
        if record.startswith("1 "):
            fields = record.split(" ", 8)
            if len(fields) != 9:
                raise MalformedGitOutputError("ordinary Git status record is malformed")
            _record_xy(fields[1], fields[8], staged=staged, unstaged=unstaged)
            continue
        if record.startswith("2 "):
            fields = record.split(" ", 9)
            if len(fields) != 10 or index >= len(records):
                raise MalformedGitOutputError("renamed Git status record is malformed")
            original_path = records[index]
            index += 1
            if not original_path:
                raise MalformedGitOutputError("renamed Git status record lacks its source path")
            _record_xy(fields[1], fields[9], staged=staged, unstaged=unstaged)
            continue
        if record.startswith("u "):
            fields = record.split(" ", 10)
            if len(fields) != 11 or not fields[10]:
                raise MalformedGitOutputError("unmerged Git status record is malformed")
            conflicted.add(fields[10])
            continue
        if record.startswith("? "):
            path = record[2:]
            if not path:
                raise MalformedGitOutputError("untracked Git status path is empty")
            untracked.add(path)
            continue
        if record.startswith("! "):
            path = record[2:]
            if not path:
                raise MalformedGitOutputError("ignored Git status path is empty")
            ignored.add(path)
            continue
        raise MalformedGitOutputError("Git status output contains an unknown record type")
    if head_commit is None or (branch is None) == (not detached):
        raise MalformedGitOutputError("Git status output lacks a consistent branch header")
    return ParsedStatus(
        branch=branch,
        detached=detached,
        head_commit=head_commit,
        upstream=upstream,
        status=RepositoryStatus(
            staged_paths=tuple(sorted(staged)),
            unstaged_paths=tuple(sorted(unstaged)),
            untracked_paths=tuple(sorted(untracked)),
            conflicted_paths=tuple(sorted(conflicted)),
            ignored_paths=tuple(sorted(ignored)),
        ),
    )


def _record_xy(xy: str, path: str, *, staged: set[str], unstaged: set[str]) -> None:
    if len(xy) != 2 or not path:
        raise MalformedGitOutputError("Git status change record is malformed")
    if xy[0] != ".":
        staged.add(path)
    if xy[1] != ".":
        unstaged.add(path)


def parse_worktree_porcelain(text: str) -> tuple[WorktreeSnapshot, ...]:
    """Parse `worktree list --porcelain -z`, retaining literal special characters."""
    records = _records(text, label="Git worktree output")
    worktrees: list[WorktreeSnapshot] = []
    current: dict[str, str | bool | None] | None = None
    for record in records:
        if not record:
            if current is not None:
                worktrees.append(_finish_worktree(current))
                current = None
            continue
        key, separator, value = record.partition(" ")
        if key == "worktree":
            if not separator or not value or current is not None:
                raise MalformedGitOutputError("Git worktree entry boundary is malformed")
            current = {"path": value}
            continue
        if current is None:
            raise MalformedGitOutputError("Git worktree field appears outside an entry")
        if key in current:
            raise MalformedGitOutputError("Git worktree entry repeats a field")
        if key in {"bare", "detached"}:
            if separator:
                raise MalformedGitOutputError("Git worktree boolean field is malformed")
            current[key] = True
        elif key in {"HEAD", "branch"}:
            if not separator or not value:
                raise MalformedGitOutputError("Git worktree required field is malformed")
            current[key] = value
        elif key in {"locked", "prunable"}:
            current[key] = value if separator and value else "registered without a reason"
        else:
            raise MalformedGitOutputError("Git worktree output contains an unknown field")
    if current is not None:
        worktrees.append(_finish_worktree(current))
    if not worktrees:
        raise MalformedGitOutputError("Git worktree output contains no entries")
    paths = [worktree.path for worktree in worktrees]
    if len(paths) != len(set(paths)):
        raise MalformedGitOutputError("Git worktree output repeats a canonical path")
    return tuple(sorted(worktrees, key=lambda item: str(item.path)))


def _finish_worktree(values: dict[str, str | bool | None]) -> WorktreeSnapshot:
    raw_path = values.get("path")
    raw_head = values.get("HEAD")
    if not isinstance(raw_path, str) or not isinstance(raw_head, str):
        raise MalformedGitOutputError("Git worktree entry lacks path or HEAD")
    try:
        path = Path(raw_path).resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise MalformedGitOutputError("Git worktree path cannot be resolved") from error
    raw_branch = values.get("branch")
    detached = bool(values.get("detached", False))
    bare = bool(values.get("bare", False))
    branch: str | None = None
    if isinstance(raw_branch, str):
        if not raw_branch.startswith("refs/heads/"):
            raise MalformedGitOutputError("Git worktree branch is not a local head")
        branch = raw_branch.removeprefix("refs/heads/")
    if bare:
        raise MalformedGitOutputError("bare worktree entries are unsupported")
    if detached == (branch is not None):
        raise MalformedGitOutputError("Git worktree branch state is inconsistent")
    locked = values.get("locked")
    prunable = values.get("prunable")
    return WorktreeSnapshot(
        path=path,
        head_commit=raw_head,
        branch=branch,
        detached=detached,
        bare=bare,
        locked_reason=locked if isinstance(locked, str) else None,
        prunable_reason=prunable if isinstance(prunable, str) else None,
    )
