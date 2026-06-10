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
    WorkspaceManagerScreen,
)


def _seed_vault(path: Path) -> None:
    v = vaultmod.create("secret", path)
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


async def test_unlock_activate_discover(tmp_path):
    vpath = tmp_path / "vault.enc"
    _seed_vault(vpath)
    with patch("grove.app.discover_remote", _fake_discover):
        app = GroveApp(vpath)
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, UnlockScreen)
            app.screen.query_one("#pw", Input).value = "secret"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.vault is not None and app.config is not None
            assert app.vault.data.active().name == "clientx"
            # NEW-detection state persisted into the workspace
            assert "gitlab/clientx/r1" in app.vault.data.active().known_repos

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
            app.screen.query_one("#pw", Input).value = "secret"
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
            app.screen.query_one("#pw", Input).value = "secret"
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
            app.screen.query_one("#pw1", Input).value = "secret"
            app.screen.query_one("#pw2", Input).value = "secret"
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
    reopened = vaultmod.unlock("secret", vpath)
    assert reopened.data.get_workspace("clienty").providers[0].token == "glpat-ey"
