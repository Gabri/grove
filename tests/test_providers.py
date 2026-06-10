"""Provider discovery with the network mocked at requests.Session.get."""

from __future__ import annotations

from unittest.mock import patch

from grove.config import RootSpec
from grove.models import NodeKind
from grove.providers.github import GitHubProvider
from grove.providers.gitlab import GitLabProvider


# _paginate_links yields already-parsed JSON pages; mocks return those directly.


def test_github_org_flat(tmp_path):
    root = RootSpec(provider="github", token="t", options={"org": "acme"})
    pages = [
        [
            {
                "name": "repo-a",
                "clone_url": "https://github.com/acme/repo-a.git",
                "ssh_url": "git@github.com:acme/repo-a.git",
                "html_url": "https://github.com/acme/repo-a",
            },
            {
                "name": "repo-b",
                "clone_url": "https://github.com/acme/repo-b.git",
                "ssh_url": "git@github.com:acme/repo-b.git",
                "html_url": "https://github.com/acme/repo-b",
            },
        ]
    ]
    with patch.object(GitHubProvider, "_paginate_links", return_value=iter(pages)):
        node = GitHubProvider(root).discover()

    repos = list(node.iter_repos())
    assert {r.name for r in repos} == {"repo-a", "repo-b"}
    assert all(r.kind is NodeKind.REPO for r in repos)
    # token injected into https url
    a = next(r for r in repos if r.name == "repo-a")
    assert "x-access-token:t@" in a.clone_url
    assert a.path == "acme/repo-a"


def test_github_ssh_protocol():
    root = RootSpec(provider="github", token="t", options={"org": "acme"})
    pages = [
        [
            {
                "name": "r",
                "clone_url": "https://github.com/acme/r.git",
                "ssh_url": "git@github.com:acme/r.git",
            }
        ]
    ]
    with patch.object(GitHubProvider, "_paginate_links", return_value=iter(pages)):
        node = GitHubProvider(root, use_ssh=True).discover()
    repo = next(node.iter_repos())
    assert repo.clone_url == "git@github.com:acme/r.git"


def test_gitlab_nested_groups():
    root = RootSpec(
        provider="gitlab",
        token="t",
        options={"group": "top", "base_url": "https://gl.test"},
    )

    def fake_paginate(self, url, params=None):
        if url.endswith("/groups/top"):
            return iter([{"id": 1, "path": "top", "name": "Top"}])
        if url.endswith("/groups/1/projects") or url.endswith("/groups/top/projects"):
            return iter(
                [
                    [
                        {
                            "path": "p1",
                            "http_url_to_repo": "https://gl.test/top/p1.git",
                            "ssh_url_to_repo": "git@gl.test:top/p1.git",
                            "web_url": "https://gl.test/top/p1",
                        }
                    ]
                ]
            )
        if url.endswith("/groups/1/subgroups") or url.endswith("/groups/top/subgroups"):
            return iter([[{"id": 2}]])
        if url.endswith("/groups/2"):
            return iter([{"id": 2, "path": "sub", "name": "Sub"}])
        if url.endswith("/groups/2/projects"):
            return iter(
                [
                    [
                        {
                            "path": "p2",
                            "http_url_to_repo": "https://gl.test/top/sub/p2.git",
                            "ssh_url_to_repo": "git@gl.test:top/sub/p2.git",
                        }
                    ]
                ]
            )
        if url.endswith("/groups/2/subgroups"):
            return iter([[]])
        raise AssertionError(f"unexpected url {url}")

    with patch.object(GitLabProvider, "_paginate_links", fake_paginate):
        node = GitLabProvider(root).discover()

    paths = {r.path for r in node.iter_repos()}
    assert paths == {"top/p1", "top/sub/p2"}
    p1 = next(r for r in node.iter_repos() if r.name == "p1")
    assert "oauth2:t@" in p1.clone_url
