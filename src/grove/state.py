"""Helper for the per-workspace NEW-repo detection key.

The set of known repos is persisted inside the encrypted vault (per workspace);
this module just defines the canonical key format.
"""

from __future__ import annotations

from .git_ops import normalize_remote_url


def repo_key(provider: str, path: str, clone_url: str | None = None) -> str:
    """Stable identity of a remote repo.

    Prefers the normalised clone URL (host included) so two keys for the same
    provider but different instances (e.g. gitlab.com vs gitlab.client.com)
    never collide. Falls back to provider/path.
    """
    norm = normalize_remote_url(clone_url)
    if norm:
        return f"{provider}@{norm}"
    return f"{provider}/{path}"


def legacy_repo_key(provider: str, path: str) -> str:
    """Pre-host-aware key format, checked for backward compatibility."""
    return f"{provider}/{path}"
