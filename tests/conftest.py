"""Shared test config: use the minimum-allowed KDF cost so tests stay fast."""

from __future__ import annotations

import pytest

import grove.vault as vaultmod


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """scrypt N=2^17 takes ~0.5s per derivation; tests use the floor (2^14)."""
    monkeypatch.setattr(vaultmod, "SCRYPT_N", vaultmod.MIN_N)
