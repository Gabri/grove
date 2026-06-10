"""Merge remote tree + local filesystem into a unified, state-annotated tree.

Local clones are matched to remote repos by their `origin` remote URL (normalised
across https/ssh/token variants), so an existing checkout is recognised wherever
it lives under `clone_base` — not only at the canonical mirrored path.
"""

from __future__ import annotations

from pathlib import Path

from . import git_ops
from .config import Config
from .models import NodeKind, NodeState, RemoteNode, UnifiedNode
from .providers import make_provider
from .state import repo_key


def discover_remote(config: Config) -> list[RemoteNode]:
    """Walk every configured root. Returns one RemoteNode per root."""
    roots: list[RemoteNode] = []
    for root_spec in config.roots:
        provider = make_provider(root_spec, use_ssh=config.use_ssh)
        roots.append(provider.discover())
    return roots


def _canonical_path(config: Config, provider: str, rel_path: str) -> Path:
    """Where a NEW clone of this repo would go (mirrors the remote hierarchy)."""
    return config.clone_base / rel_path


def _scan_local_repos(base: Path) -> dict[str, Path]:
    """Map normalised origin URL -> local repo dir for every clone under base."""
    index: dict[str, Path] = {}
    if not base.exists():
        return index
    for git_dir in base.rglob(".git"):
        repo_dir = git_dir.parent.resolve()
        norm = git_ops.normalize_remote_url(git_ops.get_origin_url(repo_dir))
        if norm and norm not in index:
            index[norm] = repo_dir
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

    `known_repos` (provider/path keys seen previously) drives the NEW badge.
    """
    known = known_repos or set()
    local_index = _scan_local_repos(config.clone_base)
    matched: set[Path] = set()

    forest = UnifiedNode(kind=NodeKind.GROUP, name="grove", path="", provider="")
    for remote in remote_roots:
        forest.children.append(
            _convert(config, remote, known, local_index, matched, inspect, do_fetch)
        )

    _attach_local_only(forest, local_index, matched)
    return forest


def _convert(
    config: Config,
    node: RemoteNode,
    known: set[str],
    local_index: dict[str, Path],
    matched: set[Path],
    inspect: bool,
    do_fetch: bool,
) -> UnifiedNode:
    if node.kind is NodeKind.REPO:
        norm = git_ops.normalize_remote_url(node.clone_url)
        existing = local_index.get(norm) if norm else None
        if existing is not None:
            local_path = existing
            matched.add(existing)
        else:
            local_path = _canonical_path(config, node.provider, node.path)
        u = UnifiedNode(
            kind=NodeKind.REPO,
            name=node.name,
            path=node.path,
            provider=node.provider,
            local_path=local_path,
            clone_url=node.clone_url,
            web_url=node.web_url,
            is_new=repo_key(node.provider, node.path) not in known if known else False,
        )
        _set_repo_state(u, inspect, do_fetch)
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
            _convert(
                config, child, known, local_index, matched, inspect, do_fetch
            )
        )
    return u


def _set_repo_state(u: UnifiedNode, inspect: bool, do_fetch: bool) -> None:
    if u.local_path is None or not git_ops.is_git_repo(u.local_path):
        u.state = NodeState.MISSING_LOCAL
        return
    if not inspect:
        u.state = NodeState.UNKNOWN
        return
    status = git_ops.sync_status(u.local_path, do_fetch=do_fetch)
    u.status = status
    if status.error:
        u.state = NodeState.ERROR
    elif status.is_synced:
        u.state = NodeState.SYNCED
    else:
        u.state = NodeState.OUT_OF_SYNC


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
            keys.add(repo_key(repo.provider, repo.path))
    return keys
