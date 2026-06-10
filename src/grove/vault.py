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
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1

DEFAULT_VAULT_PATH = Path(
    os.environ.get("GROVE_VAULT", "~/.config/grove/vault.enc")
).expanduser()


class VaultError(Exception):
    pass


class BadPassword(VaultError):
    pass


def vault_exists(path: Path | None = None) -> bool:
    return (path or DEFAULT_VAULT_PATH).exists()


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
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass


def create(password: str, path: Path | None = None) -> Vault:
    """Create a fresh, empty vault encrypted with `password`."""
    path = path or DEFAULT_VAULT_PATH
    if path.exists():
        raise VaultError(f"vault already exists at {path}")
    salt = os.urandom(16)
    key = _derive_key(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    header = {
        "magic": MAGIC,
        "kdf": "scrypt",
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
    }
    vault = Vault(path=path, data=VaultData(), _fernet=Fernet(key), _header=header)
    vault.save()
    return vault


def unlock(password: str, path: Path | None = None) -> Vault:
    """Open an existing vault. Raises BadPassword on wrong password."""
    path = path or DEFAULT_VAULT_PATH
    if not path.exists():
        raise VaultError(f"no vault at {path}")
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise VaultError(f"cannot read vault: {e}") from e
    if payload.get("magic") != MAGIC:
        raise VaultError("not a grove vault (bad magic)")

    salt = base64.b64decode(payload["salt"])
    key = _derive_key(
        password, salt, payload["n"], payload["r"], payload["p"]
    )
    fernet = Fernet(key)
    try:
        plain = fernet.decrypt(payload["ciphertext"].encode("ascii"))
    except InvalidToken as e:
        raise BadPassword("wrong master password") from e
    data = VaultData.from_dict(json.loads(plain.decode("utf-8")))
    header = {k: payload[k] for k in ("magic", "kdf", "n", "r", "p", "salt")}
    return Vault(path=path, data=data, _fernet=fernet, _header=header)
