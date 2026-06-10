"""Provider registry."""

from __future__ import annotations

from ..config import RootSpec
from .base import Provider, ProviderError
from .bitbucket import BitbucketProvider
from .github import GitHubProvider
from .gitlab import GitLabProvider

_REGISTRY: dict[str, type[Provider]] = {
    GitLabProvider.name: GitLabProvider,
    GitHubProvider.name: GitHubProvider,
    BitbucketProvider.name: BitbucketProvider,
}


def make_provider(root: RootSpec, use_ssh: bool = False) -> Provider:
    cls = _REGISTRY.get(root.provider)
    if cls is None:
        raise ProviderError(
            f"unknown provider {root.provider!r}; "
            f"available: {', '.join(sorted(_REGISTRY))}"
        )
    return cls(root, use_ssh=use_ssh)


__all__ = ["make_provider", "Provider", "ProviderError"]
