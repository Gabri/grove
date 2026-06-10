"""Encrypted vault: roundtrip, wrong password, workspace -> config flattening."""

from __future__ import annotations

import pytest

from grove import vault as vaultmod
from grove.config import config_from_workspace
from grove.models import ProviderCred, Workspace
from grove.vault import BadPassword, VaultError, create, unlock, vault_exists


def test_create_and_unlock_roundtrip(tmp_path):
    path = tmp_path / "vault.enc"
    assert not vault_exists(path)

    v = create("hunter2", path)
    v.data.default_clone_base = "~/code"
    ws = Workspace(name="clientx", clone_base="~/code/x")
    ws.providers.append(
        ProviderCred(
            provider="gitlab",
            label="work",
            token="glpat-secret",
            roots=[{"group": "clientx/team"}],
        )
    )
    v.data.workspaces.append(ws)
    v.data.active_workspace = "clientx"
    v.save()

    assert vault_exists(path)
    # the secret must NOT be readable in plaintext on disk
    raw = path.read_text()
    assert "glpat-secret" not in raw
    assert "clientx" not in raw  # workspace names are inside ciphertext too

    v2 = unlock("hunter2", path)
    assert v2.data.active_workspace == "clientx"
    ws2 = v2.data.active()
    assert ws2 is not None
    assert ws2.providers[0].token == "glpat-secret"
    assert ws2.providers[0].label == "work"
    assert ws2.providers[0].roots == [{"group": "clientx/team"}]


def test_wrong_password(tmp_path):
    path = tmp_path / "vault.enc"
    create("correct", path)
    with pytest.raises(BadPassword):
        unlock("wrong", path)


def test_create_twice_fails(tmp_path):
    path = tmp_path / "vault.enc"
    create("p", path)
    with pytest.raises(VaultError):
        create("p", path)


def test_multiple_keys_same_provider(tmp_path):
    """A vault can hold two GitLab keys in different workspaces."""
    path = tmp_path / "vault.enc"
    v = create("p", path)
    for name, token, grp in [
        ("clientx", "tok-x", "x/team"),
        ("clienty", "tok-y", "y/team"),
    ]:
        ws = Workspace(name=name)
        ws.providers.append(
            ProviderCred("gitlab", f"{name}-gl", token, roots=[{"group": grp}])
        )
        v.data.workspaces.append(ws)
    v.save()

    v2 = unlock("p", path)
    tokens = {
        w.name: w.providers[0].token for w in v2.data.workspaces
    }
    assert tokens == {"clientx": "tok-x", "clienty": "tok-y"}


def test_config_from_workspace(tmp_path):
    v = create("p", tmp_path / "vault.enc")
    v.data.default_clone_base = "/tmp/base"
    ws = Workspace(name="w")
    ws.providers.append(
        ProviderCred(
            "gitlab",
            "work",
            "tok",
            base_url="https://gl.corp",
            roots=[{"group": "a"}, {"group": "b"}],
        )
    )
    ws.providers.append(
        ProviderCred("github", "gh", "tok2", roots=[{"org": "acme"}])
    )

    cfg = config_from_workspace(v.data, ws)
    # 2 gitlab roots + 1 github root = 3 RootSpecs
    assert len(cfg.roots) == 3
    gl = [r for r in cfg.roots if r.provider == "gitlab"]
    assert {r["group"] for r in gl} == {"a", "b"}
    assert all(r.token == "tok" for r in gl)
    assert all(r["base_url"] == "https://gl.corp" for r in gl)
    assert str(cfg.clone_base) == "/tmp/base"
