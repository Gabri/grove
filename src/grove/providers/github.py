"""GitHub provider: list repos for an org or user (flat hierarchy: root -> repos)."""

from __future__ import annotations

from ..models import NodeKind, RemoteNode
from .base import Provider, ProviderError


class GitHubProvider(Provider):
    name = "github"

    def _auth_headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json"}
        if self.root.token:
            h["Authorization"] = f"Bearer {self.root.token}"
        return h

    @property
    def api(self) -> str:
        return (self.root["base_url"] or "https://api.github.com").rstrip("/")

    def discover(self) -> RemoteNode:
        org = self.root["org"]
        user = self.root["user"]
        if org:
            list_url = f"{self.api}/orgs/{org}/repos"
            root_name = org
        elif user:
            list_url = f"{self.api}/users/{user}/repos"
            root_name = user
        else:
            raise ProviderError("github root: need 'org' or 'user'")

        node = RemoteNode(
            kind=NodeKind.GROUP,
            name=root_name,
            path=root_name,
            provider=self.name,
        )
        for page in self._paginate_links(list_url, params={"per_page": 100}):
            for repo in page:
                node.children.append(self._repo_node(repo, root_name))
        return node

    def _repo_node(self, repo: dict, parent_path: str) -> RemoteNode:
        rpath = f"{parent_path}/{repo['name']}"
        # clone URLs are credential-free; auth goes through GIT_ASKPASS
        url = repo.get("ssh_url") if self.use_ssh else repo.get("clone_url")
        return RemoteNode(
            kind=NodeKind.REPO,
            name=repo["name"],
            path=rpath,
            provider=self.name,
            clone_url=url,
            web_url=repo.get("html_url"),
        )
