"""Bitbucket Cloud provider: workspace -> projects -> repos (API 2.0)."""

from __future__ import annotations

from ..models import NodeKind, RemoteNode
from .base import Provider, ProviderError


class BitbucketProvider(Provider):
    name = "bitbucket"

    def _auth_headers(self) -> dict:
        return {}  # uses HTTP basic auth instead

    def _auth(self):
        if self.root.user and self.root.token:
            return (self.root.user, self.root.token)
        return None

    @property
    def api(self) -> str:
        return (self.root["base_url"] or "https://api.bitbucket.org/2.0").rstrip("/")

    def _paginate(self, url: str, params: dict | None = None):
        """Bitbucket uses a 'next' field in the body (not Link headers)."""
        auth = self._auth()
        while url:
            resp = self.session.get(url, params=params, auth=auth, timeout=30)
            self._check(resp)
            data = resp.json()
            yield from data.get("values", [])
            url = data.get("next")
            params = None

    def discover(self) -> RemoteNode:
        ws = self.root["workspace"]
        if not ws:
            raise ProviderError("bitbucket root: missing 'workspace'")

        root = RemoteNode(
            kind=NodeKind.GROUP, name=ws, path=ws, provider=self.name
        )
        # group repos under their project key
        projects: dict[str, RemoteNode] = {}
        for proj in self._paginate(f"{self.api}/workspaces/{ws}/projects"):
            key = proj.get("key", "NONE")
            pnode = RemoteNode(
                kind=NodeKind.GROUP,
                name=proj.get("name", key),
                path=f"{ws}/{key}",
                provider=self.name,
                web_url=(proj.get("links", {}).get("html", {}) or {}).get("href"),
            )
            projects[key] = pnode
            root.children.append(pnode)

        for repo in self._paginate(f"{self.api}/repositories/{ws}"):
            key = (repo.get("project") or {}).get("key", "NONE")
            parent = projects.get(key)
            if parent is None:
                parent = RemoteNode(
                    kind=NodeKind.GROUP,
                    name=key,
                    path=f"{ws}/{key}",
                    provider=self.name,
                )
                projects[key] = parent
                root.children.append(parent)
            parent.children.append(self._repo_node(repo, parent.path))
        return root

    def _repo_node(self, repo: dict, parent_path: str) -> RemoteNode:
        slug = repo.get("slug") or repo["name"]
        rpath = f"{parent_path}/{slug}"
        clone_url = self._pick_clone_url(repo)
        return RemoteNode(
            kind=NodeKind.REPO,
            name=slug,
            path=rpath,
            provider=self.name,
            clone_url=clone_url,
            web_url=(repo.get("links", {}).get("html", {}) or {}).get("href"),
        )

    def _pick_clone_url(self, repo: dict) -> str | None:
        clones = (repo.get("links", {}) or {}).get("clone", []) or []
        want = "ssh" if self.use_ssh else "https"
        for c in clones:
            if c.get("name") == want:
                href = c.get("href", "")
                if want == "https" and "@" in href:
                    # strip any embedded user; auth goes through GIT_ASKPASS
                    rest = href.split("@", 1)[-1]
                    href = f"https://{rest}"
                return href
        return clones[0].get("href") if clones else None
