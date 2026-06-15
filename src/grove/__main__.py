"""Entrypoint: python -m grove [--vault PATH] [--list]."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grove")
    parser.add_argument(
        "--vault", type=Path, default=None, help="path to the encrypted vault"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="unlock (password prompt), print the active workspace tree, exit",
    )
    parser.add_argument(
        "--export",
        type=Path,
        metavar="FILE",
        help="unlock and write the DECRYPTED vault contents to FILE (json)",
    )
    parser.add_argument(
        "--import",
        dest="import_file",
        type=Path,
        metavar="FILE",
        help="create a NEW vault from a previously exported json FILE",
    )
    args = parser.parse_args(argv)

    if args.list:
        return _list(args.vault)
    if args.export:
        return _export(args.vault, args.export)
    if args.import_file:
        return _import(args.vault, args.import_file)

    from .app import run

    run(args.vault)
    return 0


def _export(vault_path: Path | None, out: Path) -> int:
    import json

    from .vault import BadPassword, VaultError, unlock, vault_exists

    if not vault_exists(vault_path):
        print("no vault to export", file=sys.stderr)
        return 2
    try:
        vault = unlock(getpass.getpass("master password: "), vault_path)
    except (BadPassword, VaultError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out.write_text(json.dumps(vault.data.to_dict(), indent=2))
    try:
        out.chmod(0o600)
    except OSError:
        pass
    print(
        f"WARNING: {out} contains your tokens in PLAINTEXT. "
        "Move it somewhere safe and delete it after import.",
        file=sys.stderr,
    )
    return 0


def _import(vault_path: Path | None, src: Path) -> int:
    import json

    from .models import VaultData
    from .vault import VaultError, create, vault_exists

    if vault_exists(vault_path):
        print("a vault already exists — refusing to overwrite", file=sys.stderr)
        return 2
    try:
        data = VaultData.from_dict(json.loads(src.read_text()))
    except (OSError, json.JSONDecodeError, KeyError) as e:
        print(f"cannot read export file: {e}", file=sys.stderr)
        return 2
    pw1 = getpass.getpass("new master password: ")
    pw2 = getpass.getpass("confirm: ")
    if pw1 != pw2:
        print("passwords do not match", file=sys.stderr)
        return 2
    if len(pw1) < 8:
        print("password too short (min 8 chars)", file=sys.stderr)
        return 2
    try:
        vault = create(pw1, vault_path)
    except VaultError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    vault.data = data
    vault.save()
    print(f"imported {len(data.workspaces)} workspace(s) into encrypted vault")
    print("remember to DELETE the plaintext export file", file=sys.stderr)
    return 0


def _list(vault_path: Path | None) -> int:
    from .config import ConfigError, config_from_workspace
    from .discovery import build_unified, discover_remote
    from .models import NodeKind
    from .vault import BadPassword, VaultError, unlock, vault_exists

    if not vault_exists(vault_path):
        print("no vault yet — run 'grove' to create one", file=sys.stderr)
        return 2
    try:
        vault = unlock(getpass.getpass("master password: "), vault_path)
    except BadPassword:
        print("wrong password", file=sys.stderr)
        return 2
    except VaultError as e:
        print(f"vault error: {e}", file=sys.stderr)
        return 2

    ws = vault.data.active()
    if ws is None:
        print("no active workspace", file=sys.stderr)
        return 1
    print(f"# workspace: {ws.name}  [{ws.provider_summary()}]")
    try:
        config = config_from_workspace(vault.data, ws)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    try:
        roots = discover_remote(config)
    except Exception as e:  # noqa: BLE001
        print(f"discovery error: {e}", file=sys.stderr)
        return 1
    forest = build_unified(
        config, roots, known_repos=set(ws.known_repos), inspect=True
    )

    def walk(node, depth=0):
        pad = "  " * depth
        if node.kind is NodeKind.REPO:
            print(f"{pad}- {node.name} [{node.state.value}]")
        else:
            if node.path:
                print(f"{pad}{node.name}/")
            for child in node.children:
                walk(child, depth + 1)

    for root in forest.children:
        walk(root, 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
