"""git_ops against a real temp repo pair (origin + clone)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove import git_ops


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo_pair(tmp_path: Path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(["init", "--bare", "-b", "main"], origin)

    work = tmp_path / "work"
    git_ops.clone(str(origin), work)
    _git(["config", "user.email", "t@t"], work)
    _git(["config", "user.name", "t"], work)
    (work / "a.txt").write_text("1\n")
    _git(["add", "."], work)
    _git(["commit", "-m", "init"], work)
    _git(["push", "-u", "origin", "main"], work)

    clone = tmp_path / "clone"
    git_ops.clone(str(origin), clone)
    _git(["config", "user.email", "t@t"], clone)
    _git(["config", "user.name", "t"], clone)
    return origin, work, clone


def test_is_git_repo(tmp_path, repo_pair):
    _, work, _ = repo_pair
    assert git_ops.is_git_repo(work)
    assert not git_ops.is_git_repo(tmp_path)


def test_synced(repo_pair):
    _, _, clone = repo_pair
    st = git_ops.sync_status(clone)
    assert st.is_synced
    assert st.branch == "main"
    assert st.ahead == 0 and st.behind == 0


def test_dirty(repo_pair):
    _, _, clone = repo_pair
    (clone / "a.txt").write_text("changed\n")
    st = git_ops.sync_status(clone)
    assert st.dirty
    assert not st.is_synced


def test_normalize_remote_url_variants():
    n = git_ops.normalize_remote_url
    same = {
        n("https://gitlab.com/grp/repo.git"),
        n("https://oauth2:tok@gitlab.com/grp/repo.git"),
        n("git@gitlab.com:grp/repo.git"),
        n("ssh://git@gitlab.com/grp/repo.git"),
        n("https://gitlab.com/grp/repo"),
        n("https://gitlab.com/grp/repo/"),
    }
    assert same == {"gitlab.com/grp/repo"}
    assert n(None) is None
    assert n("") is None
    # different repos must not collapse
    assert n("https://h/a.git") != n("https://h/b.git")


def test_get_origin_url(repo_pair):
    _, _, clone = repo_pair
    url = git_ops.get_origin_url(clone)
    assert url is not None and url.endswith("origin.git")


def test_behind_then_update(repo_pair):
    _, work, clone = repo_pair
    # advance origin via work
    (work / "b.txt").write_text("2\n")
    _git(["add", "."], work)
    _git(["commit", "-m", "second"], work)
    _git(["push"], work)

    st = git_ops.sync_status(clone, do_fetch=True)
    assert st.behind == 1
    assert not st.is_synced

    git_ops.update(clone)
    st2 = git_ops.sync_status(clone)
    assert st2.is_synced


def _git_out(args, cwd) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def test_update_via_fetch_url(repo_pair):
    """update(fetch_url=...) bypasses the configured remote entirely."""
    origin, work, clone = repo_pair
    (work / "c.txt").write_text("3\n")
    _git(["add", "."], work)
    _git(["commit", "-m", "third"], work)
    _git(["push"], work)
    # break the configured origin to prove fetch_url is what's used
    _git(["remote", "set-url", "origin", "git@invalid.example:nope.git"], clone)

    git_ops.update(clone, fetch_url=str(origin))
    assert _git_out(["rev-parse", "HEAD"], work) == _git_out(
        ["rev-parse", "HEAD"], clone
    )


def test_update_no_upstream_falls_back(repo_pair):
    """A clone whose branch has no tracking info still ff-updates."""
    origin, work, clone = repo_pair
    (work / "d.txt").write_text("4\n")
    _git(["add", "."], work)
    _git(["commit", "-m", "fourth"], work)
    _git(["push"], work)
    _git(["branch", "--unset-upstream"], clone)

    git_ops.update(clone)
    st = git_ops.sync_status(clone)
    assert st.is_synced  # upstream restored + fast-forwarded


def test_scrub_secrets():
    s = git_ops.scrub_secrets
    assert (
        s("fatal: https://oauth2:glpat-abc123@gitlab.com/x.git failed")
        == "fatal: https://oauth2:***@gitlab.com/x.git failed"
    )
    assert ":sekrit@" not in s("https://x-access-token:sekrit@github.com/a/b")
    # URLs without credentials pass through untouched
    assert s("https://gitlab.com/x.git") == "https://gitlab.com/x.git"
