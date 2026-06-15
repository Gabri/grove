"""Thin git wrapper over subprocess (no GitPython, matching internxt-sync style).

Authentication: tokens are NEVER embedded in URLs (they would be persisted in
.git/config and visible in `ps`). Instead a GIT_ASKPASS helper reads the
credentials from environment variables, which are private to the process.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from .models import SyncStatus


class GitError(Exception):
    pass


# (username, token) for HTTPS auth — e.g. ("oauth2", "glpat-…") for GitLab
GitAuth = tuple[str, str]

_ASKPASS_SRC = """#!/bin/sh
# grove git askpass helper: credentials come from the environment only.
case "$1" in
  [Uu]sername*) printf '%s\\n' "${GROVE_GIT_USER}" ;;
  *) printf '%s\\n' "${GROVE_GIT_TOKEN}" ;;
esac
"""

_askpass_path: Path | None = None


def _askpass_script() -> Path:
    """Write the askpass helper once per process (0700, owner-only)."""
    global _askpass_path
    if _askpass_path is not None and _askpass_path.exists():
        return _askpass_path
    fd, name = tempfile.mkstemp(prefix="grove-askpass-", suffix=".sh")
    with os.fdopen(fd, "w") as fh:
        fh.write(_ASKPASS_SRC)
    os.chmod(name, stat.S_IRWXU)
    _askpass_path = Path(name)
    return _askpass_path


def _auth_env(auth: GitAuth | None) -> dict | None:
    """Env for git subprocesses: askpass helper + creds, prompts disabled."""
    if auth is None:
        return None
    user, token = auth
    env = dict(os.environ)
    env.update(
        {
            "GIT_ASKPASS": str(_askpass_script()),
            "GROVE_GIT_USER": user,
            "GROVE_GIT_TOKEN": token,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


_SECRET_RE = re.compile(r"(://[^/:@\s]+):([^@\s]+)@")


def scrub_secrets(text: str) -> str:
    """Mask any userinfo password in URLs (defence in depth for logs)."""
    return _SECRET_RE.sub(r"\1:***@", text)


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


def _run(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = 120,
    env: dict | None = None,
) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if proc.returncode != 0:
        raise GitError(scrub_secrets((proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip()


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def get_origin_url(repo_dir: Path) -> str | None:
    """Return the 'origin' remote URL of a local repo, or None."""
    try:
        return _run(["remote", "get-url", "origin"], cwd=repo_dir)
    except GitError:
        return None


def list_branches(repo_dir: Path) -> list[str]:
    """Local branches first, then remote-only tracking branches (origin/ stripped)."""
    try:
        local_raw = _run(
            ["branch", "--format=%(refname:short)"], cwd=repo_dir
        )
        local = [b.strip() for b in local_raw.splitlines() if b.strip()]
    except GitError:
        local = []
    try:
        remote_raw = _run(
            ["branch", "-r", "--format=%(refname:short)"], cwd=repo_dir
        )
        remote_only = [
            b.strip().removeprefix("origin/")
            for b in remote_raw.splitlines()
            if b.strip() and "HEAD" not in b
            and b.strip().removeprefix("origin/") not in local
        ]
    except GitError:
        remote_only = []
    return local + remote_only


def checkout(repo_dir: Path, branch: str) -> None:
    """Checkout a branch. Creates a local tracking branch for remote-only ones."""
    _run(["checkout", branch], cwd=repo_dir)


def clone(
    url: str,
    dest: Path,
    timeout: int = 600,
    auth: GitAuth | None = None,
    depth: int | None = None,
) -> None:
    """Clone url into dest, creating intermediate dirs to mirror the hierarchy.

    The URL must be credential-free; auth (if any) goes through GIT_ASKPASS so
    nothing secret lands in .git/config or the process list.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone"]
    if depth:
        args += ["--depth", str(depth)]
    args += [url, str(dest)]
    _run(args, timeout=timeout, env=_auth_env(auth))


def fetch(
    repo_dir: Path,
    timeout: int = 180,
    url: str | None = None,
    auth: GitAuth | None = None,
) -> None:
    """Fetch. With `url`, fetches from it directly (bypassing a broken/SSH
    origin) and updates origin-tracking refs so ahead/behind stays accurate."""
    if url:
        _run(
            ["fetch", url, "+refs/heads/*:refs/remotes/origin/*"],
            cwd=repo_dir,
            timeout=timeout,
            env=_auth_env(auth),
        )
    else:
        _run(["fetch", "--all", "--prune"], cwd=repo_dir, timeout=timeout)


def _ensure_upstream(repo_dir: Path) -> str | None:
    """Make sure HEAD's branch tracks origin/<branch>; return the upstream ref."""
    try:
        return _run(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=repo_dir,
        )
    except GitError:
        pass
    try:
        branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
        if branch == "HEAD":  # detached
            return None
        _run(
            ["branch", f"--set-upstream-to=origin/{branch}", branch],
            cwd=repo_dir,
        )
        return f"origin/{branch}"
    except GitError:
        return None


def update(
    repo_dir: Path,
    timeout: int = 180,
    fetch_url: str | None = None,
    auth: GitAuth | None = None,
) -> str:
    """Fast-forward only update.

    With fetch_url (clean HTTPS URL + askpass auth): fetch into origin refs,
    set the tracking branch if missing, then merge --ff-only the upstream.
    Without: plain `git pull --ff-only`, falling back to origin HEAD for
    repos with no tracking branch configured.
    """
    if fetch_url:
        fetch(repo_dir, timeout=timeout, url=fetch_url, auth=auth)
        upstream = _ensure_upstream(repo_dir)
        if upstream:
            return _run(["merge", "--ff-only", upstream], cwd=repo_dir, timeout=timeout)
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
    upstream = _ensure_upstream(repo_dir)
    if upstream:
        return _run(["merge", "--ff-only", upstream], cwd=repo_dir, timeout=timeout)
    return _run(["merge", "--ff-only", "FETCH_HEAD"], cwd=repo_dir, timeout=timeout)


def stash(repo_dir: Path) -> str:
    """Stash uncommitted changes (git stash)."""
    return _run(["stash"], cwd=repo_dir)


def sync_status(
    repo_dir: Path,
    do_fetch: bool = False,
    fetch_url: str | None = None,
    auth: GitAuth | None = None,
) -> SyncStatus:
    """Inspect a local repo: branch, ahead/behind vs upstream, dirty tree."""
    status = SyncStatus()
    try:
        if do_fetch:
            try:
                fetch(repo_dir, url=fetch_url, auth=auth)
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

        # upstream comparison (after a URL-fetch, adopt origin/<branch> if needed)
        if not status.detached:
            upstream = _ensure_upstream(repo_dir) if (do_fetch and fetch_url) else None
            if upstream is None:
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
