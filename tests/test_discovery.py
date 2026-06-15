"""Unified tree state: origin-based matching of local clones, NEW, local-only."""

from __future__ import annotations

import subprocess
from pathlib import Path

from grove.config import Config
from grove.discovery import build_unified
from grove.models import NodeKind, NodeState, RemoteNode


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(path: Path, origin: str | None = None):
    path.mkdir(parents=True)
    _git(["init", "-b", "main"], path)
    _git(["config", "user.email", "t@t"], path)
    _git(["config", "user.name", "t"], path)
    (path / "f").write_text("x")
    _git(["add", "."], path)
    _git(["commit", "-m", "c"], path)
    if origin:
        _git(["remote", "add", "origin", origin], path)


def _remote_root(provider="gitlab"):
    root = RemoteNode(NodeKind.GROUP, "grp", "grp", provider)
    root.children.append(
        RemoteNode(NodeKind.REPO, "r1", "grp/r1", provider, clone_url="https://h/x.git")
    )
    sub = RemoteNode(NodeKind.GROUP, "sub", "grp/sub", provider)
    sub.children.append(
        RemoteNode(
            NodeKind.REPO, "r2", "grp/sub/r2", provider, clone_url="https://h/y.git"
        )
    )
    root.children.append(sub)
    return root


def _config(tmp_path: Path) -> Config:
    return Config(clone_base=tmp_path / "repos", protocol="https", roots=[])


def test_missing_local(tmp_path):
    cfg = _config(tmp_path)
    forest = build_unified(cfg, [_remote_root()], inspect=True)
    repos = list(forest.iter_repos())
    assert len(repos) == 2
    assert all(r.state is NodeState.MISSING_LOCAL for r in repos)


def test_cloned_repo_matched_at_canonical_path(tmp_path):
    cfg = _config(tmp_path)
    _make_repo(
        cfg.clone_base / "grp" / "r1", origin="https://h/x.git"
    )
    forest = build_unified(cfg, [_remote_root()], inspect=True)
    states = {r.path: r.state for r in forest.iter_repos()}
    # r1 cloned (no upstream) -> OUT_OF_SYNC; r2 still missing
    assert states["grp/r1"] is NodeState.OUT_OF_SYNC
    assert states["grp/sub/r2"] is NodeState.MISSING_LOCAL


def test_existing_clone_matched_anywhere_by_origin(tmp_path):
    """A clone in a non-canonical folder is matched via its origin URL."""
    cfg = _config(tmp_path)
    elsewhere = cfg.clone_base / "totally" / "different" / "spot"
    # ssh form of the same remote; normalisation must still match the https url
    _make_repo(elsewhere, origin="git@h:x.git")
    forest = build_unified(cfg, [_remote_root()], inspect=True)
    r1 = next(r for r in forest.iter_repos() if r.path == "grp/r1")
    assert r1.state is not NodeState.MISSING_LOCAL
    assert r1.local_path == elsewhere.resolve()
    # and it is NOT also reported as local-only
    assert not any(
        r.state is NodeState.LOCAL_ONLY for r in forest.iter_repos()
    )


def test_new_badge(tmp_path):
    cfg = _config(tmp_path)
    known = {"gitlab/grp/r1"}
    forest = build_unified(cfg, [_remote_root()], known_repos=known, inspect=True)
    by_path = {r.path: r for r in forest.iter_repos()}
    assert by_path["grp/r1"].is_new is False
    assert by_path["grp/sub/r2"].is_new is True


def test_local_only(tmp_path):
    cfg = _config(tmp_path)
    _make_repo(
        cfg.clone_base / "stray", origin="https://h/ghost.git"
    )
    forest = build_unified(cfg, [_remote_root()], inspect=True)
    local_only = [
        r for r in forest.iter_repos() if r.state is NodeState.LOCAL_ONLY
    ]
    assert any(r.name == "stray" for r in local_only)


def test_canonical_path_strips_root_prefix(tmp_path):
    """New clone paths strip the root group prefix to avoid double folders."""
    from grove.discovery import _canonical_path

    cfg = _config(tmp_path)
    # repo "grp/sub/r2" under root "grp" → clone_base/sub/r2, NOT clone_base/grp/sub/r2
    assert _canonical_path(cfg, "grp/sub/r2", "grp") == cfg.clone_base / "sub" / "r2"
    assert _canonical_path(cfg, "grp/r1", "grp") == cfg.clone_base / "r1"
    # root itself (edge case): maps to clone_base
    assert _canonical_path(cfg, "grp", "grp") == cfg.clone_base
    # no prefix: unchanged (backward compat)
    assert _canonical_path(cfg, "grp/r1", "") == cfg.clone_base / "grp" / "r1"


def test_missing_local_canonical_strips_root(tmp_path):
    """build_unified: new-clone local_path strips the root prefix."""
    cfg = _config(tmp_path)
    forest = build_unified(cfg, [_remote_root()], inspect=True)
    by_path = {r.path: r for r in forest.iter_repos()}
    # canonical paths have "grp" stripped
    assert by_path["grp/r1"].local_path == cfg.clone_base / "r1"
    assert by_path["grp/sub/r2"].local_path == cfg.clone_base / "sub" / "r2"


def test_scan_prunes_nested_repos(tmp_path):
    """Vendored checkouts inside a repo are not indexed (walk is pruned)."""
    from grove.discovery import _scan_local_repos

    cfg = _config(tmp_path)
    _make_repo(cfg.clone_base / "outer", origin="https://h/outer.git")
    # nested clone inside the outer repo (e.g. vendored module)
    _make_repo(
        cfg.clone_base / "outer" / "vendor" / "inner",
        origin="https://h/inner.git",
    )
    index = _scan_local_repos(cfg.clone_base)
    assert "h/outer" in index
    assert "h/inner" not in index  # pruned, never visited
