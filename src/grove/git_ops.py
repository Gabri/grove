"""Thin git wrapper over subprocess (no GitPython, matching internxt-sync style)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import SyncStatus


class GitError(Exception):
    pass


def normalize_remote_url(url: str | None) -> str | None:
    """Canonicalise a git URL so https/ssh/token variants of the same repo match.

    Strips scheme, embedded credentials and a trailing '.git', converts scp-like
    'git@host:path' to 'host/path', and lowercases. Returns None for empty input.
    """
    if not url:
        return None
    u = url.strip()
    for pre in ("ssh://", "git+ssh://", "https://", "http://", "git://"):
        if u.startswith(pre):
            u = u[len(pre) :]
            break
    if "@" in u:  # drop userinfo (git@, oauth2:tok@, x-access-token:tok@, user:pw@)
        u = u.split("@", 1)[1]
    # scp-like separator: 'host:path' (no '/' before ':', not a :port) -> 'host/path'
    if ":" in u:
        host, _, rest = u.partition(":")
        if "/" not in host and not rest[:1].isdigit():
            u = f"{host}/{rest}"
    u = u.rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def _run(args: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def get_origin_url(repo_dir: Path) -> str | None:
    """Return the 'origin' remote URL of a local repo, or None."""
    try:
        return _run(["remote", "get-url", "origin"], cwd=repo_dir)
    except GitError:
        return None


def clone(url: str, dest: Path, timeout: int = 600) -> None:
    """Clone url into dest, creating intermediate dirs to mirror the hierarchy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["clone", url, str(dest)], timeout=timeout)


def fetch(repo_dir: Path, timeout: int = 180) -> None:
    _run(["fetch", "--all", "--prune"], cwd=repo_dir, timeout=timeout)


def update(repo_dir: Path, timeout: int = 180, fetch_url: str | None = None) -> str:
    """Fast-forward only pull.

    If fetch_url is given (HTTPS+token form from the remote provider), fetches
    from it directly — bypassing whatever SSH/HTTPS URL is configured on origin.
    Falls back to origin HEAD when no upstream tracking branch is configured.
    """
    if fetch_url:
        _run(["fetch", fetch_url], cwd=repo_dir, timeout=timeout)
        try:
            # If tracking branch exists, prefer it over FETCH_HEAD
            upstream = _run(
                ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                cwd=repo_dir,
            )
            return _run(["merge", "--ff-only", upstream], cwd=repo_dir, timeout=timeout)
        except GitError:
            return _run(["merge", "--ff-only", "FETCH_HEAD"], cwd=repo_dir, timeout=timeout)

    try:
        return _run(["pull", "--ff-only"], cwd=repo_dir, timeout=timeout)
    except GitError as exc:
        msg = str(exc).lower()
        if "no tracking information" not in msg and "no upstream" not in msg:
            raise
    # No upstream configured — fetch origin then merge its HEAD
    try:
        _run(["fetch", "origin"], cwd=repo_dir, timeout=timeout)
    except GitError:
        pass
    try:
        head_ref = _run(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_dir)
        return _run(["merge", "--ff-only", head_ref], cwd=repo_dir, timeout=timeout)
    except GitError:
        pass
    return _run(["merge", "--ff-only", "FETCH_HEAD"], cwd=repo_dir, timeout=timeout)


def sync_status(repo_dir: Path, do_fetch: bool = False) -> SyncStatus:
    """Inspect a local repo: branch, ahead/behind vs upstream, dirty tree."""
    status = SyncStatus()
    try:
        if do_fetch:
            try:
                fetch(repo_dir)
            except GitError:
                pass  # offline / no remote: still report local state

        # current branch (or detached)
        head = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
        if head == "HEAD":
            status.detached = True
            status.branch = None
        else:
            status.branch = head

        # dirty working tree?
        porcelain = _run(["status", "--porcelain"], cwd=repo_dir)
        status.dirty = bool(porcelain.strip())

        # upstream comparison
        if not status.detached:
            try:
                _run(
                    ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                    cwd=repo_dir,
                )
            except GitError:
                status.has_upstream = False
                return status

            counts = _run(
                ["rev-list", "--left-right", "--count", "@{u}...HEAD"],
                cwd=repo_dir,
            )
            behind_str, _, ahead_str = counts.partition("\t")
            status.behind = int(behind_str or 0)
            status.ahead = int((ahead_str or "0").strip() or 0)
    except GitError as e:
        status.error = str(e)
    except (subprocess.TimeoutExpired, ValueError) as e:
        status.error = str(e)
    return status
