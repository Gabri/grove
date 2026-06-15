# grove

TUI to clone and keep in sync a **tree** of git repositories spread across
**GitLab**, **GitHub** and **Bitbucket**.

It auto-discovers the remote hierarchy via the provider APIs, mirrors it on disk,
shows at a glance which repos are out of sync, lets you update them, and flags
**new** repos that appeared on the remote since your last run.

Keys live in an **encrypted vault** (master password) and are organised into
**workspaces** you can switch between.

## Concepts

- **Vault** — a single encrypted file (`~/.config/grove/vault.enc`), unlocked
  with a master password (min 8 chars). All tokens live inside it; nothing is
  stored in plaintext. Encryption: scrypt-derived key (N=2¹⁷) + Fernet (AES).
  Old vaults are transparently re-encrypted at current strength on unlock;
  headers advertising weakened KDF parameters are rejected (anti-tampering).
- **Token hygiene** — tokens are **never embedded in git URLs** (they would
  land in `.git/config` and the process list). Auth is injected per-command
  through a `GIT_ASKPASS` helper that reads environment variables, and
  anything resembling a credential is masked in the log panel.
- **Workspace** — a named context (e.g. per client). Each workspace holds one
  key per provider (at least one key total), the discovery roots for those keys,
  and its own saved tree state. Switch workspace → switch keys. Keep separate
  GitLab keys for different clients in different workspaces.

## What it does

- **Auto-discovery** — walks GitLab groups/subgroups, a GitHub org/user, and
  Bitbucket workspaces/projects through their APIs.
- **Mirrored layout** — clones into `clone_base/<group>/.../<repo>`.
- **Sync at a glance** — coloured tree:
  - `✓` green — synced
  - `↻` yellow — out of sync (behind `↓`, ahead `↑`, dirty `✎`)
  - `↓` cyan — on remote, not cloned yet
  - `⚠` red — local only (no longer on remote)
  - `✗` red — error
- **New-over-time** — a `NEW` badge marks repos absent on your previous run.
- **Easy update** — `git pull --ff-only` on one repo or all out-of-sync ones.

## Install

```sh
# Install as a system tool (puts `grove` on PATH via ~/.local/bin/)
uv tool install .

# Editable install — changes to source take effect immediately (dev)
uv tool install --editable .

# Reinstall after pulling updates
uv tool install --reinstall .
```

`~/.local/bin/` must be on your `PATH` (it usually already is; if not, add it to
your shell profile).

Alternatively with pipx:

```sh
pipx install .
# or
pipx install --editable .
```

After installation `grove` is available as a plain command:

```sh
grove
grove --list
grove --vault /path/to/vault.enc
grove --export dump.json   # decrypted backup — plaintext, handle with care
grove --import dump.json   # restore into a fresh vault (new password)
```

## Run (without installing)

```sh
./run.sh          # syncs deps and launches the TUI
# or
uv run grove
```

On first launch you set a master password, then create a workspace and add a key
— all inside the TUI.

### Keys (in the TUI)

| key | action |
|-----|--------|
| `w` | workspaces: create/switch/delete, add keys (label + token + roots) |
| `b` | set the local base dir of the active workspace |
| `r` | refresh remote discovery + rescan local |
| `f` | fetch sync status (`git fetch`, parallel) for cloned repos |
| `c` | clone selected repo / subtree — full or shallow (`--depth 1`) |
| `u` | update (ff-only) selected repo / subtree; prompts stash if dirty |
| `U` | update + clone everything under the selected node (with confirm) |
| `B` | switch branch on selected repo (popup with branch list) |
| `/` | filter the tree by name/path (empty = clear) |
| `o` | open the selected repo/group in the browser |
| `P` | change the vault master password |
| `q` | quit |

The status bar shows the **active workspace** and its **provider keys** (by
label), so you always know which credentials are in use.

### Adding a key

In the workspace manager (`w` → `a`): pick provider, give it a **label**
(e.g. `work`, `personal`), the token / app-password, the `base_url`, and the
**roots** (one per line).

- **roots** are the **remote** groups/orgs to scan, *not* a local folder:

  ```
  team/platform        # gitlab group (+ its subgroups), shorthand
  org=acme             # github org
  user=me              # github user
  workspace=myws       # bitbucket workspace
  ```

- **base_url** is **required for self-hosted / SSO GitLab** (e.g.
  `https://gitlab.client.com`). Leave empty only for gitlab.com. The API uses
  your token directly and **bypasses SSO (FortiAuth/SAML)** — but the token must
  be valid, unexpired, and have scope **`read_api`** (or `api`).

Re-adding a key for a provider that already exists in the workspace **replaces**
it (one key per provider per workspace) — that's how you fix a wrong base_url.

## Pointing at clones you already have

Set the workspace **clone base dir** to the folder where your repos already live.
grove matches existing clones by their `origin` remote URL (normalised across
https/ssh/token forms), so a checkout is recognised **wherever** it sits under
that folder — its on-disk layout need not match the remote hierarchy. Only
*new* repos are cloned, into `clone_base/<group>/.../<repo>`.

## Notes

- Clone defaults to **HTTPS + token**; the vault-level `protocol: ssh` uses SSH URLs.
- Update is **fast-forward only** — never merges/rebases divergent history.
  Repos with uncommitted changes (`✎`) prompt for a **stash & pull** or skip.
- Requires the `git` CLI on PATH.
- Override paths: `GROVE_VAULT` env var or `--vault`.
