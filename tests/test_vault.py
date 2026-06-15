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


def test_kdf_downgrade_rejected(tmp_path):
    """A tampered header advertising weak KDF params must refuse to unlock."""
    import json

    path = tmp_path / "vault.enc"
    create("p" * 8, path)
    payload = json.loads(path.read_text())
    payload["n"] = 2  # attacker lowers the work factor
    path.write_text(json.dumps(payload))
    with pytest.raises(VaultError, match="too weak"):
        unlock("p" * 8, path)


def test_kdf_upgrade_on_unlock(tmp_path, monkeypatch):
    """Vaults created with old (weaker) params get re-encrypted on unlock."""
    import json

    path = tmp_path / "vault.enc"
    create("p" * 8, path, kdf_n=vaultmod.MIN_N)
    # pretend current strength is one step higher
    monkeypatch.setattr(vaultmod, "SCRYPT_N", vaultmod.MIN_N * 2)
    v = unlock("p" * 8, path)
    assert v._header["n"] == vaultmod.MIN_N * 2
    assert json.loads(path.read_text())["n"] == vaultmod.MIN_N * 2
    # and it still unlocks with the same password
    unlock("p" * 8, path)


def test_change_password(tmp_path):
    path = tmp_path / "vault.enc"
    v = create("old-pass-1", path)
    v.data.default_clone_base = "~/x"
    v.save()
    v.change_password("new-pass-2")

    with pytest.raises(BadPassword):
        unlock("old-pass-1", path)
    v2 = unlock("new-pass-2", path)
    assert v2.data.default_clone_base == "~/x"


def test_legacy_vault_migration(tmp_path, monkeypatch):
    """A pre-rename 'repotree' vault is found and migrated to the new location."""
    new = tmp_path / "grove" / "vault.enc"
    legacy = tmp_path / "repotree" / "vault.enc"
    monkeypatch.setattr(vaultmod, "DEFAULT_VAULT_PATH", new)
    monkeypatch.setattr(vaultmod, "LEGACY_VAULT_PATH", legacy)
    monkeypatch.delenv("GROVE_VAULT", raising=False)

    v = create("pw-123456", legacy)
    v.data.default_clone_base = "~/legacy"
    v.save()

    # default lookup finds the legacy vault even though the new one is absent
    assert vault_exists()
    assert not new.exists()

    # unlocking via the default path reads legacy AND migrates it forward
    v2 = unlock("pw-123456")
    assert v2.data.default_clone_base == "~/legacy"
    assert v2.path == new
    assert new.exists()
    assert legacy.exists()  # original kept as a backup

    # subsequent unlock now reads the migrated (new) vault
    v3 = unlock("pw-123456")
    assert v3.path == new
    assert v3.data.default_clone_base == "~/legacy"


def test_no_legacy_fallback_with_env_override(tmp_path, monkeypatch):
    """With GROVE_VAULT set, the legacy vault must NOT be picked up."""
    explicit = tmp_path / "explicit.enc"
    legacy = tmp_path / "repotree" / "vault.enc"
    monkeypatch.setattr(vaultmod, "DEFAULT_VAULT_PATH", explicit)
    monkeypatch.setattr(vaultmod, "LEGACY_VAULT_PATH", legacy)
    monkeypatch.setenv("GROVE_VAULT", str(explicit))
    create("pw-123456", legacy)
    assert not vault_exists()  # env override disables legacy fallback


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
