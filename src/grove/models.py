"""Data model: remote tree nodes, local repo status, unified node state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class NodeKind(str, Enum):
    GROUP = "group"  # group / subgroup / org / workspace / project (a container)
    REPO = "repo"  # a clonable git repository (a leaf)


class NodeState(str, Enum):
    """State of a leaf (repo) in the unified tree, drives colouring."""

    SYNCED = "synced"  # local present, up to date, clean tree
    OUT_OF_SYNC = "out_of_sync"  # behind / ahead / dirty
    MISSING_LOCAL = "missing_local"  # exists on remote, not cloned yet
    LOCAL_ONLY = "local_only"  # local git dir no longer on remote
    ERROR = "error"  # fetch / parse failure
    UNKNOWN = "unknown"  # not yet inspected


@dataclass
class RemoteNode:
    """A node returned by a provider's discovery walk."""

    kind: NodeKind
    name: str
    # path relative to the provider root, e.g. "groupA/subB/repo"
    path: str
    provider: str
    clone_url: str | None = None  # only for REPO
    web_url: str | None = None
    children: list[RemoteNode] = field(default_factory=list)

    def iter_repos(self):
        if self.kind is NodeKind.REPO:
            yield self
        for child in self.children:
            yield from child.iter_repos()


@dataclass
class SyncStatus:
    """Result of inspecting a local git repo."""

    branch: str | None = None
    ahead: int = 0
    behind: int = 0
    dirty: bool = False
    has_upstream: bool = True
    detached: bool = False
    error: str | None = None

    @property
    def is_synced(self) -> bool:
        return (
            self.error is None
            and self.has_upstream
            and not self.detached
            and not self.dirty
            and self.ahead == 0
            and self.behind == 0
        )


@dataclass
class UnifiedNode:
    """Merged remote + local view shown in the TUI."""

    kind: NodeKind
    name: str
    path: str
    provider: str
    local_path: Path | None = None
    clone_url: str | None = None
    web_url: str | None = None
    state: NodeState = NodeState.UNKNOWN
    status: SyncStatus | None = None
    is_new: bool = False  # appeared on remote since last saved state
    children: list[UnifiedNode] = field(default_factory=list)

    def iter_repos(self):
        if self.kind is NodeKind.REPO:
            yield self
        for child in self.children:
            yield from child.iter_repos()

    def repos_in_state(self, state: NodeState):
        return [r for r in self.iter_repos() if r.state is state]


# --------------------------------------------------------------------------
# Encrypted vault model: workspaces, each binding one credential per provider.
# --------------------------------------------------------------------------


@dataclass
class ProviderCred:
    """One provider key inside a workspace, with a human label + its roots."""

    provider: str  # gitlab | github | bitbucket
    label: str
    token: str
    user: str | None = None
    base_url: str | None = None
    # discovery roots for this key, e.g. [{"group": "team/x"}], [{"org": "acme"}]
    roots: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "label": self.label,
            "token": self.token,
            "user": self.user,
            "base_url": self.base_url,
            "roots": self.roots,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProviderCred:
        return cls(
            provider=d["provider"],
            label=d.get("label", d["provider"]),
            token=d.get("token", ""),
            user=d.get("user"),
            base_url=d.get("base_url"),
            roots=d.get("roots", []),
        )


@dataclass
class Workspace:
    """A named context (e.g. a client): its keys, roots and saved tree state."""

    name: str
    providers: list[ProviderCred] = field(default_factory=list)
    clone_base: str | None = None  # override of vault default
    known_repos: list[str] = field(default_factory=list)  # for NEW detection

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "clone_base": self.clone_base,
            "providers": [p.to_dict() for p in self.providers],
            "known_repos": self.known_repos,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Workspace:
        return cls(
            name=d["name"],
            clone_base=d.get("clone_base"),
            providers=[ProviderCred.from_dict(p) for p in d.get("providers", [])],
            known_repos=d.get("known_repos", []),
        )

    def provider_summary(self) -> str:
        if not self.providers:
            return "(no keys)"
        return " ".join(f"{p.provider}:{p.label}" for p in self.providers)


@dataclass
class VaultData:
    """Decrypted vault contents."""

    active_workspace: str | None = None
    default_clone_base: str = "~/repos"
    protocol: str = "https"  # https | ssh
    workspaces: list[Workspace] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "active_workspace": self.active_workspace,
            "default_clone_base": self.default_clone_base,
            "protocol": self.protocol,
            "workspaces": [w.to_dict() for w in self.workspaces],
        }

    @classmethod
    def from_dict(cls, d: dict) -> VaultData:
        return cls(
            active_workspace=d.get("active_workspace"),
            default_clone_base=d.get("default_clone_base", "~/repos"),
            protocol=d.get("protocol", "https"),
            workspaces=[Workspace.from_dict(w) for w in d.get("workspaces", [])],
        )

    def get_workspace(self, name: str) -> Workspace | None:
        return next((w for w in self.workspaces if w.name == name), None)

    def active(self) -> Workspace | None:
        if self.active_workspace is None:
            return None
        return self.get_workspace(self.active_workspace)
