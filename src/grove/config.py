"""Runtime config consumed by discovery/providers.

Persistent config now lives in the encrypted vault (see vault.py); this module
turns the *active workspace* into the flat (clone_base, protocol, roots) view
that the discovery pipeline and providers already understand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .models import VaultData, Workspace


class ConfigError(Exception):
    pass


@dataclass
class RootSpec:
    """A single discovery root (one provider entry point), with its secret."""

    provider: str
    token: str | None = None
    user: str | None = None
    # provider-specific fields (base_url, group/org/workspace, ...)
    options: dict = field(default_factory=dict)

    def __getitem__(self, key):
        return self.options.get(key)


@dataclass
class Config:
    clone_base: Path
    protocol: str  # "https" | "ssh"
    roots: list[RootSpec]

    @property
    def use_ssh(self) -> bool:
        return self.protocol == "ssh"


def config_from_workspace(vault: VaultData, workspace: Workspace) -> Config:
    """Flatten a workspace's provider credentials + roots into a Config."""
    roots: list[RootSpec] = []
    for cred in workspace.providers:
        specs = cred.roots or [{}]
        for spec in specs:
            options = {"base_url": cred.base_url, **spec}
            roots.append(
                RootSpec(
                    provider=cred.provider,
                    token=cred.token,
                    user=cred.user,
                    options=options,
                )
            )
    if not roots:
        raise ConfigError(
            f"workspace '{workspace.name}' has no keys/roots to discover"
        )
    base = Path(workspace.clone_base or vault.default_clone_base).expanduser()
    return Config(clone_base=base, protocol=vault.protocol, roots=roots)
