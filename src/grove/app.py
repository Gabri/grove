"""Textual TUI: unlock vault, pick workspace, browse/clone/update the tree."""

from __future__ import annotations

import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Header, Log, Static, Tree
from textual.widgets.tree import TreeNode

from . import git_ops
from .config import Config, ConfigError, config_from_workspace
from .discovery import build_unified, current_repo_keys, discover_remote
from .models import NodeKind, NodeState, UnifiedNode
from .screens import (
    BranchSelectScreen,
    ChoiceScreen,
    ConfirmScreen,
    CreateVaultScreen,
    TextPromptScreen,
    UnlockScreen,
    WorkspaceManagerScreen,
)
from .vault import BadPassword, Vault, VaultError, create, unlock, vault_exists
from .widgets import LEGEND, node_label

# username git expects for token auth, per provider
_TOKEN_USER = {"gitlab": "oauth2", "github": "x-access-token"}




class GroveApp(App):
    CSS = """
    Screen { background: $surface; }
    #status { height: 1; padding: 0 1; background: $panel; color: $text; }
    #tree { height: 1fr; border: round $primary; }
    #log { height: 8; border: round $secondary; }
    #legend { height: 1; padding: 0 1; color: $text-muted; }
    #keys-bar { height: auto; padding: 0 1; background: $panel; color: $text-muted; }

    ConfirmScreen, ChoiceScreen, CreateVaultScreen, UnlockScreen,
    WorkspaceManagerScreen, WorkspaceFormScreen, CredentialFormScreen,
    BranchSelectScreen {
        align: center middle;
    }
    #confirm-box {
        width: 64; height: auto; padding: 1 2;
        border: thick $primary; background: $panel;
    }
    #form-box {
        width: 72; max-height: 90%; padding: 1 2;
        border: thick $primary; background: $panel;
    }
    #form-box-wide {
        width: 72; max-height: 90%; padding: 1 2;
        border: thick $primary; background: $panel;
    }
    #keys-list { height: auto; min-height: 3; max-height: 8; border: round $secondary; margin: 0 0 1 0; }
    #ws-box {
        width: 72; height: 80%; padding: 1 2;
        border: thick $primary; background: $panel;
    }
    #ws-list { height: 1fr; border: round $secondary; margin: 1 0; }
    #form-title { text-style: bold; }
    #form-help, #form-error, #ws-hint, #ws-detail { margin: 1 0; }
    .field-label { margin-top: 1; }
    .field-help { color: $text-muted; }
    #confirm-buttons { height: auto; align: center middle; margin-top: 1; }
    #confirm-buttons Button { margin: 0 1; }
    Input, Select, TextArea { margin: 1 0; }
    #roots { height: 5; }
    """

    BINDINGS = [
        ("w", "workspaces", "Workspaces"),
        ("b", "set_base", "Set base dir"),
        ("r", "refresh", "Refresh"),
        ("f", "fetch_status", "Fetch status"),
        ("c", "clone", "Clone"),
        ("u", "update", "Update"),
        ("U", "update_all", "Update all"),
        ("slash", "filter", "Filter"),
        ("B", "checkout_branch", "Branch"),
        ("R", "rewrite_remotes", "Rewrite remotes"),
        ("s", "open_shell", "Shell"),
        ("o", "open_web", "Open in browser"),
        ("P", "change_password", "Change password"),
        ("q", "quit", "Quit"),
    ]

    @classmethod
    def _keys_hint(cls) -> str:
        parts = []
        for key, _, label in cls.BINDINGS:
            display = "/" if key == "slash" else key
            parts.append(f"[bold]{display}[/] {label}")
        return "  ".join(parts)

    def __init__(self, vault_path: Path | None = None):
        super().__init__()
        self.vault_path = vault_path
        self.vault: Vault | None = None
        self.config: Config | None = None
        self.forest: UnifiedNode | None = None
        self._filter: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static("locked", id="status")
            tree: Tree[UnifiedNode] = Tree("grove", id="tree")
            tree.show_root = False
            yield tree
            yield Log(id="log", highlight=True)
        yield Static(LEGEND, id="legend")
        yield Static(self._keys_hint(), id="keys-bar", markup=True)

    def on_mount(self) -> None:
        self.title = "grove"
        self.query_one("#log", Log).can_focus = False
        self._begin_unlock()

    # ---- logging / status ---------------------------------------------
    def log_msg(self, msg: str) -> None:
        log = self.query_one("#log", Log)
        for line in git_ops.scrub_secrets(str(msg)).split("\n"):
            log.write_line(line)

    def _set_loading(self, value: bool) -> None:
        self.query_one("#tree", Tree).loading = value

    def _clone_base_for(self, ws) -> str:
        assert self.vault is not None
        return ws.clone_base or self.vault.data.default_clone_base

    def _auth_for(self, provider: str) -> git_ops.GitAuth | None:
        """(username, token) for HTTPS git auth from the active workspace key."""
        if self.vault is None:
            return None
        ws = self.vault.data.active()
        if ws is None:
            return None
        cred = next((p for p in ws.providers if p.provider == provider), None)
        if cred is None or not cred.token:
            return None
        user = _TOKEN_USER.get(provider) or cred.user or "git"
        return (user, cred.token)

    def _update_status(self) -> None:
        bar = self.query_one("#status", Static)
        if self.vault is None:
            bar.update("locked")
            return
        ws = self.vault.data.active()
        if ws is None:
            bar.update("[yellow]no active workspace — press 'w'[/]")
            return
        providers = "  ".join(
            f"[cyan]{p.provider}[/]:[bold]{p.label}[/]" for p in ws.providers
        ) or "[yellow](no keys — press 'w' then 'a')[/]"
        base = self._clone_base_for(ws)
        filt = f"   [magenta]filter: {self._filter}[/]" if self._filter else ""
        bar.update(
            f"workspace [bold green]{ws.name}[/]   {providers}"
            f"   [dim]↧ {base}[/]{filt}"
        )
        self.sub_title = f"{ws.name} → {base}"

    # ---- vault flow ----------------------------------------------------
    def _begin_unlock(self) -> None:
        if vault_exists(self.vault_path):
            self._prompt_unlock()
        else:
            self.push_screen(CreateVaultScreen(), self._on_create)

    def _on_create(self, password: str | None) -> None:
        if password is None:
            self.exit()
            return
        try:
            self.vault = create(password, self.vault_path)
        except VaultError as e:
            self.log_msg(f"vault error: {e}")
            self.exit()
            return
        self.log_msg("Vault created. Add a workspace and a key.")
        self._after_unlock()

    def _prompt_unlock(self, error: str | None = None) -> None:
        self.push_screen(UnlockScreen(error), self._on_unlock)

    def _on_unlock(self, password: str | None) -> None:
        if password is None:
            self.exit()
            return
        try:
            self.vault = unlock(password, self.vault_path)
        except BadPassword:
            self._prompt_unlock("wrong password, try again")
            return
        except VaultError as e:
            self.log_msg(f"vault error: {e}")
            self.exit()
            return
        self._after_unlock()

    def _after_unlock(self) -> None:
        assert self.vault is not None
        self._update_status()
        if not self.vault.data.workspaces:
            self.log_msg("No workspaces — create one ('n') and add a key ('a').")
            self.action_workspaces()
            return
        if self.vault.data.active_workspace is None:
            self.vault.data.active_workspace = self.vault.data.workspaces[0].name
            self.vault.save()
        self._activate_current()

    def _activate_current(self) -> None:
        assert self.vault is not None
        ws = self.vault.data.active()
        self._update_status()
        if ws is None or not ws.providers:
            self.log_msg("Active workspace has no keys. Press 'w' to add one.")
            return
        try:
            self.config = config_from_workspace(self.vault.data, ws)
        except ConfigError as e:
            self.log_msg(f"config error: {e}")
            return
        self.action_refresh()

    def action_workspaces(self) -> None:
        if self.vault is None:
            return

        def done(switched: str | None) -> None:
            self._update_status()
            # only re-discover if something changed in (or that affects) the active ws
            if switched is not None and self.vault and self.vault.data.active() is not None:
                self._activate_current()

        self.push_screen(WorkspaceManagerScreen(self.vault), done)

    def action_set_base(self) -> None:
        if self.vault is None:
            return
        ws = self.vault.data.active()
        if ws is None:
            self.log_msg("No active workspace. Press 'w' first.")
            return
        current = ws.clone_base or self.vault.data.default_clone_base

        def done(value: str | None) -> None:
            if value is None or ws is None:
                return
            ws.clone_base = value.strip() or None
            self.vault.save()
            shown = ws.clone_base or self.vault.data.default_clone_base
            self.log_msg(f"Base dir set to {shown} — rediscovering…")
            self._activate_current()

        self.push_screen(
            TextPromptScreen(
                f"Local sync folder — {ws.name}",
                "Absolute or ~-path containing this workspace's repos. "
                "Existing clones matched by origin URL anywhere below it.",
                current,
            ),
            done,
        )

    def action_change_password(self) -> None:
        if self.vault is None:
            return

        def done(password: str | None) -> None:
            if password is None:
                return
            assert self.vault is not None
            self.vault.change_password(password)
            self.log_msg("Master password changed.")

        self.push_screen(
            CreateVaultScreen("Change master password", "Change"), done
        )

    # ---- filter / open --------------------------------------------------
    def action_filter(self) -> None:
        def done(value: str | None) -> None:
            if value is None:
                return
            self._filter = value.strip().lower()
            self._update_status()
            self._rebuild_tree()

        self.push_screen(
            TextPromptScreen(
                "Filter tree",
                "Show only repos whose name/path contains this text "
                "(empty = clear filter).",
                self._filter,
            ),
            done,
        )

    def action_open_web(self) -> None:
        node = self._selected()
        if node is None or not node.web_url:
            self.log_msg("No web URL for selection.")
            return
        webbrowser.open(node.web_url)
        self.log_msg(f"Opened {node.web_url}")

    def action_open_shell(self) -> None:
        node = self._selected()
        path: Path | None = None
        if node is not None and node.kind is NodeKind.REPO:
            if node.local_path and node.local_path.exists():
                path = node.local_path
        elif node is not None and node.kind is NodeKind.GROUP:
            for r in node.iter_repos():
                if r.local_path and r.local_path.exists():
                    path = r.local_path.parent
                    break
        if path is None and self.config is not None:
            path = self.config.clone_base
        if path is None:
            self.log_msg("No local path — clone something first.")
            return
        try:
            self._open_terminal_at(path)
            self.log_msg(f"Shell → {path}")
        except RuntimeError as e:
            self.log_msg(f"Terminal error: {e}")

    @staticmethod
    def _open_terminal_at(path: Path) -> None:
        _LAUNCHERS: list[tuple[str, list[str]]] = [
            ("alacritty", ["alacritty", "--working-directory", str(path)]),
            ("kitty", ["kitty", "--directory", str(path)]),
            ("wezterm", ["wezterm", "start", "--cwd", str(path)]),
            ("gnome-terminal", ["gnome-terminal", f"--working-directory={path}"]),
            ("konsole", ["konsole", "--workdir", str(path)]),
            ("xfce4-terminal", ["xfce4-terminal", f"--working-directory={path}"]),
            ("tilix", ["tilix", f"--working-directory={path}"]),
            ("xterm", ["xterm"]),
        ]
        term_env = os.environ.get("TERMINAL", "").strip()
        if term_env:
            _LAUNCHERS.insert(0, (term_env, [term_env]))
        for name, cmd in _LAUNCHERS:
            if shutil.which(name):
                subprocess.Popen(cmd, cwd=str(path), start_new_session=True)
                return
        raise RuntimeError(
            "No terminal emulator found. Set $TERMINAL to your terminal executable."
        )

    def action_checkout_branch(self) -> None:
        node = self._selected()
        if node is None or node.kind is not NodeKind.REPO:
            self.log_msg("Select a repo first.")
            return
        if node.local_path is None or not git_ops.is_git_repo(node.local_path):
            self.log_msg("Repo not cloned locally — clone it first.")
            return
        branches = git_ops.list_branches(node.local_path)
        if not branches:
            self.log_msg("No branches found.")
            return
        current = node.status.branch if node.status else None

        def done(branch: str | None) -> None:
            if branch is None or branch == current:
                return
            try:
                git_ops.checkout(node.local_path, branch)
            except git_ops.GitError as e:
                self.log_msg(f"checkout failed: {e}")
                return
            node.status = git_ops.sync_status(node.local_path)
            node.state = self._state_from_status(node.status)
            self._rebuild_tree(keep_cursor=node.path)
            self.log_msg(f"Switched {node.name} → {branch}")

        self.push_screen(BranchSelectScreen(branches, current), done)

    # ---- tree building -------------------------------------------------
    def _node_visible(self, node: UnifiedNode) -> bool:
        if not self._filter:
            return True
        if node.kind is NodeKind.REPO:
            return (
                self._filter in node.name.lower()
                or self._filter in node.path.lower()
            )
        return any(self._node_visible(c) for c in node.children)

    def _collect_expanded(self, node: TreeNode, out: set[str]) -> None:
        if node.data is not None and node.is_expanded:
            out.add(node.data.path)
        for child in node.children:
            self._collect_expanded(child, out)

    def _apply_expanded(self, node: TreeNode, paths: set[str]) -> None:
        if node.data is None or node.data.path in paths:
            node.expand()
        for child in node.children:
            self._apply_expanded(child, paths)

    def _collect_cursor(self) -> str | None:
        tree = self.query_one("#tree", Tree)
        cur = tree.cursor_node
        return cur.data.path if (cur and cur.data) else None

    def _restore_cursor(self, node: TreeNode, path: str) -> bool:
        if node.data is not None and node.data.path == path:
            self.query_one("#tree", Tree).move_cursor(node)
            return True
        for child in node.children:
            if self._restore_cursor(child, path):
                return True
        return False

    def _rebuild_tree(self, keep_cursor: str | None = None) -> None:
        tree = self.query_one("#tree", Tree)
        expanded: set[str] = set()
        self._collect_expanded(tree.root, expanded)
        cursor_path = keep_cursor if keep_cursor is not None else self._collect_cursor()
        tree.clear()
        if self.forest is None:
            return

        real_roots = [c for c in self.forest.children if c.path != "__local_only__"]
        local_only = [c for c in self.forest.children if c.path == "__local_only__"]

        if len(real_roots) == 1:
            # Single root: flatten it — children appear at tree top, root is in status bar
            for child in real_roots[0].children:
                if self._node_visible(child):
                    self._add_node(tree.root, child)
        else:
            for child in real_roots:
                if self._node_visible(child):
                    self._add_node(tree.root, child)

        for node in local_only:
            if self._node_visible(node):
                self._add_node(tree.root, node)

        if self._filter:
            tree.root.expand_all()  # filtered view: show everything that matched
        elif expanded:
            self._apply_expanded(tree.root, expanded)
        else:
            tree.root.expand_all()

        tree.focus()
        # Defer cursor restore: expand_all() posts async messages; moving the
        # cursor before those are processed drops it to line 0.
        if cursor_path:
            self.call_after_refresh(self._restore_cursor, tree.root, cursor_path)

    def _add_node(self, parent: TreeNode, node: UnifiedNode) -> None:
        if node.kind is NodeKind.REPO:
            parent.add_leaf(node_label(node), data=node)
        else:
            tn = parent.add(node_label(node), data=node)
            for child in node.children:
                if self._node_visible(child):
                    self._add_node(tn, child)

    def _selected(self) -> UnifiedNode | None:
        tree = self.query_one("#tree", Tree)
        cur = tree.cursor_node
        return cur.data if cur else None

    # ---- actions -------------------------------------------------------
    def action_refresh(self) -> None:
        if self.config is None:
            self.log_msg("No active workspace. Press 'w'.")
            return
        self.query_one("#tree", Tree).loading = True
        self.log_msg("Discovering remote tree…")
        self._discover_worker()

    @work(thread=True, exclusive=True)
    def _discover_worker(self) -> None:
        assert self.config is not None and self.vault is not None
        try:
            try:
                remote_roots = discover_remote(self.config)
            except Exception as e:  # noqa: BLE001 - surface any provider error
                self.call_from_thread(self.log_msg, f"ERROR discover: {e}")
                return

            ws = self.vault.data.active()
            known = set(ws.known_repos) if ws else set()
            forest = build_unified(
                self.config,
                remote_roots,
                known_repos=known,
                inspect=True,
                do_fetch=False,
            )
            # persist current repo keys into the workspace for NEW-detection
            if ws is not None:
                ws.known_repos = sorted(current_repo_keys(remote_roots))
                try:
                    self.vault.save()
                except OSError as e:
                    self.call_from_thread(self.log_msg, f"warn: vault save: {e}")

            self.forest = forest
            self.call_from_thread(self._rebuild_tree)
            repos = list(forest.iter_repos())
            new = sum(1 for r in repos if r.is_new)
            self.call_from_thread(
                self.log_msg,
                f"Found {len(repos)} repos"
                + (f" ({new} NEW)" if new else "")
                + ". Press 'f' for sync status.",
            )
        finally:
            self.call_from_thread(self._set_loading, False)

    def action_fetch_status(self) -> None:
        if self.forest is None:
            return
        self.query_one("#tree", Tree).loading = True
        self.log_msg("Fetching status (git fetch on cloned repos)…")
        self._fetch_worker()

    @work(thread=True, exclusive=True)
    def _fetch_worker(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        assert self.forest is not None
        use_ssh = self.config is not None and self.config.use_ssh
        ssh_key = self.config.ssh_key if use_ssh and self.config else None
        try:
            repos = [
                r
                for r in self.forest.iter_repos()
                if r.local_path and git_ops.is_git_repo(r.local_path)
            ]
            done = 0

            def _one(repo: UnifiedNode) -> None:
                nonlocal done
                st = git_ops.sync_status(
                    repo.local_path,
                    do_fetch=True,
                    fetch_url=repo.clone_url,
                    auth=None if use_ssh else self._auth_for(repo.provider),
                    ssh_key=ssh_key,
                )
                repo.status = st
                repo.state = self._state_from_status(st)
                origin = git_ops.get_origin_url(repo.local_path)
                repo.remote_mismatch = bool(origin) and (
                    git_ops.url_is_ssh(origin) != use_ssh
                )
                done += 1
                self.call_from_thread(
                    self.log_msg, f"  [{done}/{len(repos)}] {repo.path}"
                )

            with ThreadPoolExecutor(max_workers=8) as ex:
                list(ex.map(_one, repos))
            self.call_from_thread(self._rebuild_tree)
            self.call_from_thread(self.log_msg, "Status fetch done.")
        finally:
            self.call_from_thread(self._set_loading, False)

    @staticmethod
    def _state_from_status(st) -> NodeState:
        if st.error:
            return NodeState.ERROR
        return NodeState.SYNCED if st.is_synced else NodeState.OUT_OF_SYNC

    def action_clone(self) -> None:
        node = self._selected()
        if node is None or self.config is None:
            return
        targets = [
            r
            for r in node.iter_repos()
            if r.state is NodeState.MISSING_LOCAL and r.clone_url
        ]
        if not targets:
            self.log_msg("Nothing to clone in selection.")
            return

        def go(choice: str | None) -> None:
            if choice is None:
                return
            depth = 1 if choice == "shallow" else None
            self._bulk_worker([], targets, depth=depth)

        if len(targets) == 1:
            desc = targets[0].path
        else:
            listed = "\n".join(f"  • {r.path}" for r in targets[:8])
            extra = f"\n  … and {len(targets) - 8} more" if len(targets) > 8 else ""
            desc = f"{len(targets)} repos\n{listed}{extra}"
        self.push_screen(
            ChoiceScreen(
                "Clone repos",
                f"Clone {desc}\ninto {self.config.clone_base}?",
                [("full", "Clone"), ("shallow", "Shallow (depth 1)")],
            ),
            go,
        )

    def action_update(self) -> None:
        node = self._selected()
        if node is None:
            return
        targets = self._updatable(node)
        dirty = [
            r for r in node.iter_repos()
            if r.state is NodeState.OUT_OF_SYNC
            and r.local_path
            and r.status
            and r.status.dirty
            and r.status.behind > 0
        ]
        if not targets and not dirty:
            self.log_msg("Nothing to update in selection.")
            return
        if not dirty:
            self._bulk_worker(targets, [])
            return

        # Some repos have uncommitted changes — ask what to do
        names = "\n".join(f"  • {r.name}" for r in dirty)
        body = f"{'These repos have' if len(dirty) > 1 else 'This repo has'} uncommitted changes:\n{names}\n\nStash them and pull?"
        choices: list[tuple[str, str]] = [("stash_pull", "Stash & pull")]
        if targets:
            choices.append(("skip_dirty", f"Skip dirty, pull {len(targets)} clean"))

        def on_choice(choice: str | None) -> None:
            if choice == "stash_pull":
                self._stash_and_update_worker(dirty, targets)
            elif choice == "skip_dirty":
                self._bulk_worker(targets, [])

        self.push_screen(ChoiceScreen("Uncommitted changes", body, choices), on_choice)

    @work(thread=True)
    def _stash_and_update_worker(
        self, dirty: list[UnifiedNode], clean: list[UnifiedNode]
    ) -> None:
        stashed: list[UnifiedNode] = []
        for repo in dirty:
            try:
                git_ops.stash(repo.local_path)
                self.call_from_thread(self.log_msg, f"  stashed {repo.name}")
                stashed.append(repo)
            except git_ops.GitError as e:
                self.call_from_thread(self.log_msg, f"  stash failed {repo.name}: {e}")
        # Refresh status so these repos pass _updatable checks
        for repo in stashed:
            st = git_ops.sync_status(repo.local_path)
            repo.status = st
            repo.state = self._state_from_status(st)
        combined = stashed + clean
        if combined:
            self.call_from_thread(self._bulk_worker, combined, [])
        else:
            self.call_from_thread(self.log_msg, "Nothing to update after stash.")
            self.call_from_thread(self._rebuild_tree)

    def action_update_all(self) -> None:
        scope = self._selected() or self.forest
        if scope is None:
            return
        to_update = self._updatable(scope)
        to_clone = [
            r for r in scope.iter_repos()
            if r.state is NodeState.MISSING_LOCAL and r.clone_url
        ]
        if not to_update and not to_clone:
            self.log_msg("Everything up to date in selection.")
            return
        scope_label = scope.name if scope is not self.forest else "all"

        parts: list[str] = []
        if to_update:
            parts.append(f"pull {len(to_update)} out-of-sync repo(s)")
        if to_clone:
            parts.append(f"clone {len(to_clone)} missing repo(s)")

        def go(confirmed: bool) -> None:
            if confirmed:
                self._bulk_worker(to_update, to_clone)

        self.push_screen(
            ConfirmScreen(
                "Update + Clone",
                f"Under '{scope_label}': {' and '.join(parts)}?",
                "OK",
            ),
            go,
        )

    def action_rewrite_remotes(self) -> None:
        """Rewrite origin URLs of cloned repos to match the workspace protocol."""
        if self.forest is None or self.config is None:
            return
        node = self._selected() or self.forest
        want_ssh = self.config.use_ssh
        mismatched = [
            r for r in node.iter_repos()
            if r.local_path
            and r.clone_url
            and git_ops.is_git_repo(r.local_path)
            and git_ops.url_is_ssh(git_ops.get_origin_url(r.local_path)) != want_ssh
        ]
        if not mismatched:
            proto = "SSH" if want_ssh else "HTTPS"
            self.log_msg(f"All cloned remotes already use {proto}.")
            return
        proto_label = "SSH" if want_ssh else "HTTPS"
        names = "\n".join(f"  • {r.name}" for r in mismatched)

        def go(confirmed: bool) -> None:
            if confirmed:
                self._rewrite_remotes_worker(mismatched)

        self.push_screen(
            ConfirmScreen(
                "Rewrite remotes",
                f"Switch {len(mismatched)} repo(s) to {proto_label}:\n{names}",
                f"Switch to {proto_label}",
            ),
            go,
        )

    @work(thread=True)
    def _rewrite_remotes_worker(self, repos: list[UnifiedNode]) -> None:
        for repo in repos:
            try:
                git_ops.set_remote_url(repo.local_path, repo.clone_url)
                repo.remote_mismatch = False
                self.call_from_thread(
                    self.log_msg, f"  {repo.name}: origin → {repo.clone_url}"
                )
            except git_ops.GitError as e:
                self.call_from_thread(self.log_msg, f"  ERROR {repo.name}: {e}")
        self.call_from_thread(self.log_msg, "Remote rewrite done.")
        self.call_from_thread(self._rebuild_tree)

    @staticmethod
    def _updatable(node: UnifiedNode) -> list[UnifiedNode]:
        return [
            r
            for r in node.iter_repos()
            if r.state is NodeState.OUT_OF_SYNC
            and r.local_path
            and r.status
            and not r.status.dirty
            and (r.status.behind > 0 or not r.status.has_upstream)
        ]

    @work(thread=True)
    def _bulk_worker(
        self,
        to_update: list[UnifiedNode],
        to_clone: list[UnifiedNode],
        depth: int | None = None,
    ) -> None:
        """Update then clone, sequentially, with a single tree rebuild at the end."""
        total = len(to_update) + len(to_clone)
        step = 0
        use_ssh = self.config is not None and self.config.use_ssh
        ssh_key = self.config.ssh_key if use_ssh and self.config else None
        for repo in to_update:
            step += 1
            self.call_from_thread(
                self.log_msg, f"  [{step}/{total}] pull {repo.path}…"
            )
            auth = None if use_ssh else self._auth_for(repo.provider)
            try:
                git_ops.update(repo.local_path, fetch_url=repo.clone_url, auth=auth, ssh_key=ssh_key)
                repo.status = git_ops.sync_status(repo.local_path)
                repo.state = self._state_from_status(repo.status)
            except Exception as e:  # noqa: BLE001
                repo.state = NodeState.ERROR
                self.call_from_thread(self.log_msg, f"    ERROR: {e}")
        for repo in to_clone:
            step += 1
            self.call_from_thread(
                self.log_msg, f"  [{step}/{total}] cloning {repo.path}…"
            )
            auth = None if use_ssh else self._auth_for(repo.provider)
            try:
                git_ops.clone(repo.clone_url, repo.local_path, auth=auth, depth=depth, ssh_key=ssh_key)
                repo.status = git_ops.sync_status(repo.local_path)
                repo.state = self._state_from_status(repo.status)
            except Exception as e:  # noqa: BLE001
                repo.state = NodeState.ERROR
                self.call_from_thread(self.log_msg, f"    ERROR: {e}")
        self.call_from_thread(self._rebuild_tree)
        self.call_from_thread(self.log_msg, "Done.")


def run(vault_path: Path | None = None) -> None:
    GroveApp(vault_path).run()
