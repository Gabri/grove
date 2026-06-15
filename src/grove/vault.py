"""Encrypted, master-password protected store for workspaces + provider keys.

File layout (JSON, the only plaintext is the KDF header; secrets are inside the
Fernet ciphertext):

    {
      "magic": "RPTV1",
      "kdf": "scrypt", "n": 16384, "r": 8, "p": 1,
      "salt": "<b64>",
      "ciphertext": "<fernet-token>"
    }
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .models import VaultData

MAGIC = "RPTV1"
SCRYPT_N = 2**17  # OWASP-recommended for interactive logins
SCRYPT_R = 8
SCRYPT_P = 1
# Floors: refuse to unlock vaults whose header advertises weaker params
# (prevents a KDF-downgrade attack by someone who can rewrite the file).
MIN_N = 2**14
MIN_R = 8
MIN_P = 1

DEFAULT_VAULT_PATH = Path(
    os.environ.get("GROVE_VAULT", "~/.config/grove/vault.enc")
).expanduser()
# Pre-rename location (the project used to be called "repotree"). If no current
# vault exists we transparently read from here and migrate to DEFAULT_VAULT_PATH,
# leaving the old file untouched as a backup.
LEGACY_VAULT_PATH = Path("~/.config/repotree/vault.enc").expanduser()


def _env_override() -> bool:
    return bool(os.environ.get("GROVE_VAULT"))


def _read_path(path: Path | None) -> Path:
    """Resolve where to READ from: explicit > current default > legacy > default."""
    if path is not None:
        return path
    if DEFAULT_VAULT_PATH.exists():
        return DEFAULT_VAULT_PATH
    if not _env_override() and LEGACY_VAULT_PATH.exists():
        return LEGACY_VAULT_PATH
    return DEFAULT_VAULT_PATH


class VaultError(Exception):
    pass


class BadPassword(VaultError):
    pass


def vault_exists(path: Path | None = None) -> bool:
    if path is not None:
        return path.exists()
    if DEFAULT_VAULT_PATH.exists():
        return True
    return not _env_override() and LEGACY_VAULT_PATH.exists()


def _derive_key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)
    raw = kdf.derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


@dataclass
class Vault:
    """An unlocked vault. Mutate `.data`, then call `.save()`."""

    path: Path
    data: VaultData
    _fernet: Fernet
    _header: dict

    def save(self) -> None:
        token = self._fernet.encrypt(
            json.dumps(self.data.to_dict()).encode("utf-8")
        )
        payload = {**self._header, "ciphertext": token.decode("ascii")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def change_password(self, new_password: str) -> None:
        """Re-encrypt with a fresh salt + key derived from new_password."""
        salt = os.urandom(16)
        key = _derive_key(new_password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
        self._fernet = Fernet(key)
        self._header = _make_header(salt)
        self.save()


def _make_header(salt: bytes) -> dict:
    return {
        "magic": MAGIC,
        "kdf": "scrypt",
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
    }


def create(
    password: str, path: Path | None = None, *, kdf_n: int | None = None
) -> Vault:
    """Create a fresh, empty vault encrypted with `password`."""
    path = path or DEFAULT_VAULT_PATH
    if path.exists():
        raise VaultError(f"vault already exists at {path}")
    if kdf_n is None:
        kdf_n = SCRYPT_N
    salt = os.urandom(16)
    key = _derive_key(password, salt, kdf_n, SCRYPT_R, SCRYPT_P)
    header = {**_make_header(salt), "n": kdf_n}
    vault = Vault(path=path, data=VaultData(), _fernet=Fernet(key), _header=header)
    vault.save()
    return vault


def unlock(password: str, path: Path | None = None) -> Vault:
    """Open an existing vault. Raises BadPassword on wrong password.

    With no explicit path we read the current vault, or transparently fall back
    to the pre-rename legacy location and migrate it to the current default.
    """
    read_path = _read_path(path)
    # where future saves go: an explicit path stays put; otherwise the current
    # default (so a legacy vault is migrated forward on first unlock).
    write_path = path if path is not None else DEFAULT_VAULT_PATH
    if not read_path.exists():
        raise VaultError(f"no vault at {read_path}")
    try:
        payload = json.loads(read_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise VaultError(f"cannot read vault: {e}") from e
    if payload.get("magic") != MAGIC:
        raise VaultError("not a grove vault (bad magic)")

    n, r, p = payload.get("n", 0), payload.get("r", 0), payload.get("p", 0)
    if n < MIN_N or r < MIN_R or p < MIN_P:
        raise VaultError(
            f"vault KDF parameters too weak (n={n}, r={r}, p={p}) — "
            "refusing to unlock (possible tampering)"
        )

    salt = base64.b64decode(payload["salt"])
    key = _derive_key(password, salt, n, r, p)
    fernet = Fernet(key)
    try:
        plain = fernet.decrypt(payload["ciphertext"].encode("ascii"))
    except InvalidToken as e:
        raise BadPassword("wrong master password") from e
    data = VaultData.from_dict(json.loads(plain.decode("utf-8")))
    header = {k: payload[k] for k in ("magic", "kdf", "n", "r", "p", "salt")}
    vault = Vault(path=write_path, data=data, _fernet=fernet, _header=header)
    if n < SCRYPT_N:
        # transparent KDF upgrade: re-encrypt with current-strength params
        # (also writes to write_path, covering the migration case).
        vault.change_password(password)
    elif read_path != write_path:
        # migrate the legacy vault to the current location (old file kept).
        vault.save()
    return vault
