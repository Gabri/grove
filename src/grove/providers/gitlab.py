"""GitLab provider: walk a group's subgroups + projects recursively (API v4)."""

from __future__ import annotations

from urllib.parse import quote

from ..models import NodeKind, RemoteNode
from .base import Provider, ProviderError


class GitLabProvider(Provider):
    name = "gitlab"

    def _auth_headers(self) -> dict:
        return {"PRIVATE-TOKEN": self.root.token or ""}

    @property
    def base_url(self) -> str:
        return (self.root["base_url"] or "https://gitlab.com").rstrip("/")

    @property
    def api(self) -> str:
        return f"{self.base_url}/api/v4"

    def discover(self) -> RemoteNode:
        group = self.root["group"]
        if not group:
            raise ProviderError("gitlab root: missing 'group'")
        return self._walk_group(group_ref=str(group), rel_path="")

    def _walk_group(self, group_ref: str, rel_path: str) -> RemoteNode:
        gid = quote(group_ref, safe="")
        # fetch group meta for its path/name
        meta_pages = list(self._paginate_links(f"{self.api}/groups/{gid}"))
        meta = meta_pages[0] if meta_pages else {}
        gpath = meta.get("path", group_ref.split("/")[-1])
        node_path = f"{rel_path}/{gpath}".strip("/") if rel_path else gpath
        node = RemoteNode(
            kind=NodeKind.GROUP,
            name=meta.get("name", gpath),
            path=node_path,
            provider=self.name,
            web_url=meta.get("web_url"),
        )

        # direct projects
        for page in self._paginate_links(
            f"{self.api}/groups/{gid}/projects",
            params={"per_page": 100, "archived": "false", "with_shared": "false"},
        ):
            for proj in page:
                node.children.append(self._project_node(proj, node_path))

        # subgroups (recurse)
        for page in self._paginate_links(
            f"{self.api}/groups/{gid}/subgroups", params={"per_page": 100}
        ):
            for sub in page:
                node.children.append(
                    self._walk_group(str(sub["id"]), node_path)
                )
        return node

    def _project_node(self, proj: dict, parent_path: str) -> RemoteNode:
        rpath = f"{parent_path}/{proj['path']}".strip("/")
        # clone URLs are credential-free: auth is injected per-command via
        # GIT_ASKPASS (see git_ops) so tokens never land in .git/config
        url = (
            proj.get("ssh_url_to_repo")
            if self.use_ssh
            else proj.get("http_url_to_repo")
        )
        return RemoteNode(
            kind=NodeKind.REPO,
            name=proj["path"],
            path=rpath,
            provider=self.name,
            clone_url=url,
            web_url=proj.get("web_url"),
        )
