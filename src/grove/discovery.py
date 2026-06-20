"""Merge remote tree + local filesystem into a unified, state-annotated tree.

Local clones are matched to remote repos by their `origin` remote URL (normalised
across https/ssh/token variants), so an existing checkout is recognised wherever
it lives under `clone_base` — not only at the canonical mirrored path.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import git_ops
from .config import Config
from .models import NodeKind, NodeState, RemoteNode, UnifiedNode
from .providers import make_provider
from .state import legacy_repo_key, repo_key

_MAX_WORKERS = 8


def discover_remote(config: Config) -> list[RemoteNode]:
    """Walk every configured root (in parallel). Returns one RemoteNode per root."""
    providers = [
        make_provider(root_spec, use_ssh=config.use_ssh)
        for root_spec in config.roots
    ]
    if len(providers) == 1:
        return [providers[0].discover()]
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(providers))) as ex:
        return list(ex.map(lambda p: p.discover(), providers))


def _canonical_path(config: Config, rel_path: str, root_prefix: str = "") -> Path:
    """Where a NEW clone of this repo would go.

    Strips root_prefix so clone_base maps to the root group directly — e.g. with
    clone_base=~/ws/internals and root_prefix="internals", repo "internals/sub/r"
    lands at ~/ws/internals/sub/r, not ~/ws/internals/internals/sub/r.
    """
    if root_prefix and (
        rel_path == root_prefix or rel_path.startswith(root_prefix + "/")
    ):
        rel_path = rel_path[len(root_prefix) :].lstrip("/")
    return config.clone_base / rel_path if rel_path else config.clone_base


def _scan_local_repos(base: Path) -> dict[str, Path]:
    """Map normalised origin URL -> local repo dir for every clone under base.

    Uses os.walk with pruning: once a repo is found we don't descend into it,
    so vendored checkouts (node_modules, .terraform, …) are skipped cheaply.
    """
    index: dict[str, Path] = {}
    if not base.exists():
        return index
    for dirpath, dirnames, filenames in os.walk(base):
        if ".git" in dirnames or ".git" in filenames:  # worktrees use a .git file
            repo_dir = Path(dirpath).resolve()
            norm = git_ops.normalize_remote_url(git_ops.get_origin_url(repo_dir))
            if norm and norm not in index:
                index[norm] = repo_dir
            dirnames.clear()  # don't descend into the repo
            continue
        # never walk into raw .git dirs encountered some other way
        dirnames[:] = [d for d in dirnames if d != ".git"]
    return index


def build_unified(
    config: Config,
    remote_roots: list[RemoteNode],
    *,
    known_repos: set[str] | None = None,
    inspect: bool = False,
    do_fetch: bool = False,
) -> UnifiedNode:
    """Build the unified tree. If inspect, compute git status for cloned repos.

    `known_repos` (repo keys seen previously) drives the NEW badge.
    """
    known = known_repos or set()
    local_index = _scan_local_repos(config.clone_base)
    matched: set[Path] = set()

    forest = UnifiedNode(kind=NodeKind.GROUP, name="grove", path="", provider="")
    pending: list[UnifiedNode] = []  # cloned repos awaiting status inspection
    for remote in remote_roots:
        forest.children.append(
            _convert(config, remote, known, local_index, matched, pending,
                     root_prefix=remote.path)
        )

    if inspect and pending:
        use_ssh = config.use_ssh

        def _inspect(u: UnifiedNode) -> None:
            status = git_ops.sync_status(u.local_path, do_fetch=do_fetch)
            u.status = status
            if status.error:
                u.state = NodeState.ERROR
            elif status.is_synced:
                u.state = NodeState.SYNCED
            else:
                u.state = NodeState.OUT_OF_SYNC
            # Flag when local origin protocol doesn't match workspace setting
            origin = git_ops.get_origin_url(u.local_path)
            u.remote_mismatch = bool(origin) and (git_ops.url_is_ssh(origin) != use_ssh)

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            list(ex.map(_inspect, pending))

    _attach_local_only(forest, local_index, matched)
    return forest


def _convert(
    config: Config,
    node: RemoteNode,
    known: set[str],
    local_index: dict[str, Path],
    matched: set[Path],
    pending: list[UnifiedNode],
    root_prefix: str = "",
) -> UnifiedNode:
    if node.kind is NodeKind.REPO:
        norm = git_ops.normalize_remote_url(node.clone_url)
        existing = local_index.get(norm) if norm else None
        if existing is not None:
            local_path = existing
            matched.add(existing)
        else:
            local_path = _canonical_path(config, node.path, root_prefix)
        is_new = False
        if known:
            key = repo_key(node.provider, node.path, node.clone_url)
            legacy = legacy_repo_key(node.provider, node.path)
            is_new = key not in known and legacy not in known
        u = UnifiedNode(
            kind=NodeKind.REPO,
            name=node.name,
            path=node.path,
            provider=node.provider,
            local_path=local_path,
            clone_url=node.clone_url,
            web_url=node.web_url,
            is_new=is_new,
        )
        if u.local_path is not None and git_ops.is_git_repo(u.local_path):
            pending.append(u)  # status computed later (possibly in parallel)
        else:
            u.state = NodeState.MISSING_LOCAL
        return u

    u = UnifiedNode(
        kind=NodeKind.GROUP,
        name=node.name,
        path=node.path,
        provider=node.provider,
        web_url=node.web_url,
    )
    for child in node.children:
        u.children.append(
            _convert(config, child, known, local_index, matched, pending, root_prefix)
        )
    return u


def _attach_local_only(
    forest: UnifiedNode, local_index: dict[str, Path], matched: set[Path]
) -> None:
    """Cloned repos whose origin matches no remote repo in the active workspace."""
    extras: list[UnifiedNode] = []
    for repo_dir in local_index.values():
        if repo_dir in matched:
            continue
        extras.append(
            UnifiedNode(
                kind=NodeKind.REPO,
                name=repo_dir.name,
                path=str(repo_dir),
                provider="",
                local_path=repo_dir,
                state=NodeState.LOCAL_ONLY,
            )
        )
    if extras:
        forest.children.append(
            UnifiedNode(
                kind=NodeKind.GROUP,
                name="(local only — origin not in this workspace)",
                path="__local_only__",
                provider="",
                children=extras,
            )
        )


def current_repo_keys(remote_roots: list[RemoteNode]) -> set[str]:
    keys: set[str] = set()
    for root in remote_roots:
        for repo in root.iter_repos():
            keys.add(repo_key(repo.provider, repo.path, repo.clone_url))
    return keys
