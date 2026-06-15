"""End-to-end TUI flows driven headless via Textual's Pilot."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from textual.widgets import Input, TextArea, Tree

from grove import vault as vaultmod
from grove.app import GroveApp
from grove.models import NodeKind, ProviderCred, RemoteNode, Workspace
from grove.screens import (
    CreateVaultScreen,
    CredentialFormScreen,
    TextPromptScreen,
    UnlockScreen,
    WorkspaceFormScreen,
    WorkspaceManagerScreen,
    parse_root_url,
)


def _seed_vault(path: Path) -> None:
    v = vaultmod.create("secret-pw", path)
    ws = Workspace(name="clientx", clone_base=str(path.parent / "repos"))
    ws.providers.append(
        ProviderCred("gitlab", "work", "glpat-x", roots=[{"group": "clientx"}])
    )
    v.data.workspaces.append(ws)
    v.data.active_workspace = "clientx"
    v.save()


def _fake_discover(_config):
    root = RemoteNode(NodeKind.GROUP, "clientx", "clientx", "gitlab")
    root.children.append(
        RemoteNode(NodeKind.REPO, "r1", "clientx/r1", "gitlab", clone_url="x")
    )
    return [root]


def test_parse_root_url():
    # public gitlab: deep group path, no base_url needed
    assert parse_root_url(
        "gitlab", "https://gitlab.com/maggiolispa/delivery/area/maggioli/azure"
    ) == ({"group": "maggiolispa/delivery/area/maggioli/azure"}, None)
    # self-hosted gitlab: host becomes base_url
    assert parse_root_url("gitlab", "https://gitlab.client.com/team/platform") == (
        {"group": "team/platform"},
        "https://gitlab.client.com",
    )
    # bare path and explicit key=value still work
    assert parse_root_url("gitlab", "team/x") == ({"group": "team/x"}, None)
    assert parse_root_url("gitlab", "org=acme") == ({"org": "acme"}, None)
    # github public org / user, and enterprise host -> /api/v3
    assert parse_root_url("github", "https://github.com/orgs/acme") == (
        {"org": "acme"},
        None,
    )
    assert parse_root_url("github", "https://github.com/users/me") == (
        {"user": "me"},
        None,
    )
    assert parse_root_url("github", "https://gh.corp.com/acme") == (
        {"org": "acme"},
        "https://gh.corp.com/api/v3",
    )
    # bitbucket workspace
    assert parse_root_url("bitbucket", "https://bitbucket.org/myteam/repo") == (
        {"workspace": "myteam"},
        None,
    )


async def test_credential_form_parses_pasted_url(tmp_path):
    """Pasting a self-hosted URL fills base_url + group with no manual fields."""
    vpath = tmp_path / "vault.enc"
    _seed_vault(vpath)
    with patch("grove.app.discover_remote", _fake_discover):
        app = GroveApp(vpath)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.screen.query_one("#pw", Input).value = "secret-pw"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("w")
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, CredentialFormScreen)
            app.screen.query_one("#label", Input).value = "work"
            app.screen.query_one("#token", Input).value = "glpat-z"
            app.screen.query_one("#roots", TextArea).text = (
                "https://gitlab.client.com/team/platform"
            )
            await pilot.click("#ok")
            await pilot.pause()

            cred = next(
                p
                for p in app.vault.data.get_workspace("clientx").providers
                if p.provider == "gitlab"
            )
            assert cred.base_url == "https://gitlab.client.com"
            assert cred.roots == [{"group": "team/platform"}]


async def test_rename_workspace(tmp_path):
    vpath = tmp_path / "vault.enc"
    _seed_vault(vpath)
    with patch("grove.app.discover_remote", _fake_discover):
        app = GroveApp(vpath)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.screen.query_one("#pw", Input).value = "secret-pw"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, WorkspaceManagerScreen)
            await pilot.press("r")
            await pilot.pause()
            # r now opens WorkspaceFormScreen (name + base) instead of TextPromptScreen
            assert isinstance(app.screen, WorkspaceFormScreen)
            app.screen.query_one("#name", Input).value = "renamed"
            await pilot.click("#ok")
            await pilot.pause()

            assert app.vault.data.get_workspace("renamed") is not None
            assert app.vault.data.get_workspace("clientx") is None
            assert app.vault.data.active_workspace == "renamed"


async def test_copy_workspace(tmp_path):
    vpath = tmp_path / "vault.enc"
    _seed_vault(vpath)
    with patch("grove.app.discover_remote", _fake_discover):
        app = GroveApp(vpath)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.screen.query_one("#pw", Input).value = "secret-pw"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, WorkspaceManagerScreen)
            await pilot.press("c")
            await pilot.pause()
            # copy opens WorkspaceFormScreen pre-filled with "copy-of-clientx"
            assert isinstance(app.screen, WorkspaceFormScreen)
            assert app.screen.query_one("#name", Input).value == "copy-of-clientx"
            # change name to something else
            app.screen.query_one("#name", Input).value = "clientx-2"
            await pilot.click("#ok")
            await pilot.pause()

            assert app.vault.data.get_workspace("clientx") is not None  # original kept
            assert app.vault.data.get_workspace("clientx-2") is not None
            copy_ws = app.vault.data.get_workspace("clientx-2")
            assert copy_ws.providers[0].token == "glpat-x"  # copied key

            # copy auto-opens CredentialFormScreen for key editing — cancel it
            assert isinstance(app.screen, CredentialFormScreen)
            await pilot.press("escape")
            await pilot.pause()

            # duplicate name must be rejected: try copy again expecting conflict
            assert isinstance(app.screen, WorkspaceManagerScreen)
            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, WorkspaceFormScreen)
            app.screen.query_one("#name", Input).value = "clientx-2"
            await pilot.click("#ok")
            await pilot.pause()
            # error notification; workspace list unchanged (still no clientx-2 duplicate)
            assert (
                len([w for w in app.vault.data.workspaces if w.name == "clientx-2"]) == 1
            )


async def test_unlock_activate_discover(tmp_path):
    vpath = tmp_path / "vault.enc"
    _seed_vault(vpath)
    with patch("grove.app.discover_remote", _fake_discover):
        app = GroveApp(vpath)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, UnlockScreen)
            app.screen.query_one("#pw", Input).value = "secret-pw"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.vault is not None and app.config is not None
            assert app.vault.data.active().name == "clientx"
            # NEW-detection state persisted into the workspace
            # (key prefers the normalised clone URL: "gitlab@<norm-url>")
            assert "gitlab@x" in app.vault.data.active().known_repos

            labels: list[str] = []

            def collect(node):
                for c in node.children:
                    labels.append(str(c.label))
                    collect(c)

            collect(app.query_one("#tree", Tree).root)
            assert any("r1" in lbl for lbl in labels)
            # loader is cleared once discovery finishes
            assert app.query_one("#tree", Tree).loading is False


async def test_set_clone_base(tmp_path):
    vpath = tmp_path / "vault.enc"
    _seed_vault(vpath)
    with patch("grove.app.discover_remote", _fake_discover):
        app = GroveApp(vpath)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.screen.query_one("#pw", Input).value = "secret-pw"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, WorkspaceManagerScreen)
            await pilot.press("b")
            await pilot.pause()
            assert isinstance(app.screen, TextPromptScreen)
            app.screen.query_one("#value", Input).value = "~/work/clientx"
            await pilot.click("#ok")
            await pilot.pause()
            assert (
                app.vault.data.get_workspace("clientx").clone_base
                == "~/work/clientx"
            )


async def test_wrong_password_then_retry(tmp_path):
    vpath = tmp_path / "vault.enc"
    _seed_vault(vpath)
    with patch("grove.app.discover_remote", _fake_discover):
        app = GroveApp(vpath)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            app.screen.query_one("#pw", Input).value = "nope"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, UnlockScreen)  # re-prompted
            assert app.vault is None
            app.screen.query_one("#pw", Input).value = "secret-pw"
            await pilot.press("enter")
            await pilot.pause()
            assert app.vault is not None


async def test_create_vault_then_manage_keys(tmp_path):
    """First run: create vault, add a workspace + key entirely in the TUI."""
    vpath = tmp_path / "vault.enc"
    with patch("grove.app.discover_remote", lambda _c: []):
        app = GroveApp(vpath)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, CreateVaultScreen)
            app.screen.query_one("#pw1", Input).value = "secret-pw"
            app.screen.query_one("#pw2", Input).value = "secret-pw"
            await pilot.click("#ok")
            await pilot.pause()
            assert isinstance(app.screen, WorkspaceManagerScreen)

            await pilot.press("n")
            await pilot.pause()
            app.screen.query_one("#name", Input).value = "clienty"
            await pilot.click("#ok")
            await pilot.pause()
            assert app.vault.data.get_workspace("clienty") is not None

            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, CredentialFormScreen)
            app.screen.query_one("#label", Input).value = "ey-gl"
            app.screen.query_one("#token", Input).value = "glpat-ey"
            app.screen.query_one("#roots", TextArea).text = "clienty/team\norg=other"
            await pilot.click("#ok")
            await pilot.pause()

            ws = app.vault.data.get_workspace("clienty")
            assert len(ws.providers) == 1
            cred = ws.providers[0]
            assert cred.token == "glpat-ey"
            assert cred.roots == [{"group": "clienty/team"}, {"org": "other"}]

            await pilot.press("enter")  # switch to it
            await pilot.pause()
            assert app.vault.data.active_workspace == "clienty"

    # secret is encrypted at rest
    assert "glpat-ey" not in vpath.read_text()
    reopened = vaultmod.unlock("secret-pw", vpath)
    assert reopened.data.get_workspace("clienty").providers[0].token == "glpat-ey"
