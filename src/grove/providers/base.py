"""Provider abstraction. Each provider walks its remote hierarchy into RemoteNodes."""

from __future__ import annotations

from abc import ABC, abstractmethod

import requests

from ..config import RootSpec
from ..models import RemoteNode

USER_AGENT = "grove/0.1 (+https://github.com/grove)"


class ProviderError(Exception):
    pass


class AuthError(ProviderError):
    """401/403/HTML challenge — token, base_url or scope problem."""


class Provider(ABC):
    """Discover a remote repository tree for one config root."""

    name: str = ""

    def __init__(self, root: RootSpec, use_ssh: bool = False):
        self.root = root
        self.use_ssh = use_ssh
        self.session = requests.Session()
        # A real User-Agent avoids Cloudflare "Just a moment…" bot challenges
        # that python-requests' default UA triggers on some GitLab instances.
        self.session.headers.update(
            {"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        self.session.headers.update(self._auth_headers())

    @abstractmethod
    def _auth_headers(self) -> dict:
        ...

    @abstractmethod
    def discover(self) -> RemoteNode:
        """Return the root RemoteNode with the full subtree populated."""

    def _check(self, resp: requests.Response) -> None:
        """Raise a helpful error on auth failures / HTML challenge pages."""
        ctype = resp.headers.get("Content-Type", "")
        looks_html = "text/html" in ctype or "sign_in" in resp.url
        if resp.status_code in (401, 403) or looks_html:
            base = self.root["base_url"] or "(default for provider)"
            raise AuthError(
                f"{self.name}: {resp.status_code} authentication failed.\n"
                f"  • base_url = {base} — must point at the RIGHT instance "
                f"(self-hosted/SSO GitLab is NOT gitlab.com)\n"
                f"  • the API uses your token directly and BYPASSES SSO/"
                f"FortiAuth — but the token must be valid, unexpired and have "
                f"scope 'read_api' (or 'api')\n"
                f"  • request landed on: {resp.url}"
                + ("  [HTML/Cloudflare challenge]" if looks_html else "")
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name}: {resp.status_code} {resp.url}\n{resp.text[:300]}"
            )

    def _paginate_links(self, url: str, params: dict | None = None):
        """Follow RFC5988 Link-header pagination (GitHub/GitLab style)."""
        params = dict(params or {})
        while url:
            resp = self.session.get(url, params=params, timeout=30)
            self._check(resp)
            yield resp.json()
            url = resp.links.get("next", {}).get("url")
            params = None  # next url already carries query params
