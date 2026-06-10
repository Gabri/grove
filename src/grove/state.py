"""Helper for the per-workspace NEW-repo detection key.

The set of known repos is persisted inside the encrypted vault (per workspace);
this module just defines the canonical key format.
"""

from __future__ import annotations


def repo_key(provider: str, path: str) -> str:
    return f"{provider}/{path}"
