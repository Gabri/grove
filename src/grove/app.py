"""Textual TUI: unlock vault, pick workspace, browse/clone/update the tree."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Log, Static, Tree
from textual.widgets.tree import TreeNode

from . import git_ops
from .config import Config, ConfigError, config_from_workspace
from .discovery import build_unified, current_repo_keys, discover_remote
from .models import NodeKind, NodeState, UnifiedNode
from .screens import (
    ConfirmScreen,
    CreateVaultScreen,
    TextPromptScreen,
    UnlockScreen,
    WorkspaceManagerScreen,
)
from .vault import BadPassword, Vault, VaultError, create, unlock, vault_exists
from .widgets import LEGEND, node_label


class GroveApp(App):
    CSS = """
    Screen { background: $surface; }
    #status { height: 1; padding: 0 1; background: $panel; color: $text; }
    #tree { height: 1fr; border: round $primary; }
    #log { height: 8; border: round $secondary; }
    #legend { height: 1; padding: 0 1; color: $text-muted; }

    ConfirmScreen, CreateVaultScreen, UnlockScreen,
    WorkspaceManagerScreen, WorkspaceFormScreen, CredentialFormScreen {
        align: center middle;
    }
    #confirm-box, #form-box {
        width: 64; height: auto; padding: 1 2;
        border: thick $primary; background: $panel;
    }
    #form-box-wide {
        width: 72; max-height: 90%; padding: 1 2;
        border: thick $primary; background: $panel;
    }
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
        ("q", "quit", "Quit"),
    ]

    def __init__(self, vault_path: Path | None = None):
        super().__init__()
        self.vault_path = vault_path
        self.vault: Vault | None = None
        self.config: Config | None = None
        self.forest: UnifiedNode | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static("locked", id="status")
            tree: Tree[UnifiedNode] = Tree("grove", id="tree")
            tree.show_root = False
            yield tree
            yield Log(id="log", highlight=True)
        yield Static(LEGEND, id="legend")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "grove"
        self.query_one("#log", Log).can_focus = False
        self._begin_unlock()

    # ---- logging / status ---------------------------------------------
    def log_msg(self, msg: str) -> None:
        log = self.query_one("#log", Log)
        for line in str(msg).split("\n"):
            log.write_line(line)

    def _set_loading(self, value: bool) -> None:
        self.query_one("#tree", Tree).loading = value

    def _clone_base_for(self, ws) -> str:
        assert self.vault is not None
        return ws.clone_base or self.vault.data.default_clone_base

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
        bar.update(
            f"workspace [bold green]{ws.name}[/]   {providers}"
            f"   [dim]↧ {base}[/]"
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
            # re-activate whenever the manager closes (handles switch + edits)
            if self.vault and self.vault.data.active() is not None:
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

    # ---- tree building -------------------------------------------------
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

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#tree", Tree)
        expanded: set[str] = set()
        self._collect_expanded(tree.root, expanded)
        tree.clear()
        if self.forest is None:
            return
        for child in self.forest.children:
            self._add_node(tree.root, child)
        if expanded:
            self._apply_expanded(tree.root, expanded)
        else:
            tree.root.expand_all()
        tree.focus()

    def _add_node(self, parent: TreeNode, node: UnifiedNode) -> None:
        if node.kind is NodeKind.REPO:
            parent.add_leaf(node_label(node), data=node)
        else:
            tn = parent.add(node_label(node), data=node)
            for child in node.children:
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
        assert self.forest is not None
        try:
            repos = [
                r
                for r in self.forest.iter_repos()
                if r.local_path and git_ops.is_git_repo(r.local_path)
            ]
            for i, repo in enumerate(repos, 1):
                st = git_ops.sync_status(repo.local_path, do_fetch=True)
                repo.status = st
                repo.state = self._state_from_status(st)
                self.call_from_thread(
                    self.log_msg, f"  [{i}/{len(repos)}] {repo.path}"
                )
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

        def go(confirmed: bool) -> None:
            if confirmed:
                self._clone_worker(targets)

        self.push_screen(
            ConfirmScreen(
                "Clone repos",
                f"Clone {len(targets)} repo(s) into {self.config.clone_base}?",
                "Clone",
            ),
            go,
        )

    @work(thread=True)
    def _clone_worker(self, targets: list[UnifiedNode]) -> None:
        for i, repo in enumerate(targets, 1):
            self.call_from_thread(
                self.log_msg, f"  [{i}/{len(targets)}] cloning {repo.path}…"
            )
            try:
                git_ops.clone(repo.clone_url, repo.local_path)
                repo.status = git_ops.sync_status(repo.local_path)
                repo.state = self._state_from_status(repo.status)
            except Exception as e:  # noqa: BLE001
                repo.state = NodeState.ERROR
                self.call_from_thread(self.log_msg, f"    ERROR: {e}")
        self.call_from_thread(self._rebuild_tree)
        self.call_from_thread(self.log_msg, "Clone done.")

    def action_update(self) -> None:
        node = self._selected()
        if node is None:
            return
        targets = self._updatable(node)
        if not targets:
            self.log_msg("Nothing to update in selection.")
            return
        self._update_worker(targets)

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
            if not confirmed:
                return
            if to_update:
                self._update_worker(to_update)
            if to_clone:
                self._clone_worker(to_clone)

        self.push_screen(
            ConfirmScreen(
                "Update + Clone",
                f"Under '{scope_label}': {' and '.join(parts)}?",
                "OK",
            ),
            go,
        )

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
    def _update_worker(self, targets: list[UnifiedNode]) -> None:
        for i, repo in enumerate(targets, 1):
            self.call_from_thread(
                self.log_msg, f"  [{i}/{len(targets)}] pull {repo.path}…"
            )
            try:
                git_ops.update(repo.local_path, fetch_url=repo.clone_url)
                repo.status = git_ops.sync_status(repo.local_path)
                # Repos updated via token URL have no tracking branch → sync_status
                # reports no-upstream even though the merge succeeded. If clean and
                # even (behind=0, ahead=0), trust the merge and mark green.
                if (
                    repo.clone_url
                    and not repo.status.has_upstream
                    and not repo.status.error
                    and not repo.status.dirty
                    and repo.status.behind == 0
                    and repo.status.ahead == 0
                ):
                    repo.state = NodeState.SYNCED
                else:
                    repo.state = self._state_from_status(repo.status)
            except Exception as e:  # noqa: BLE001
                repo.state = NodeState.ERROR
                self.call_from_thread(self.log_msg, f"    ERROR: {e}")
        self.call_from_thread(self._rebuild_tree)
        self.call_from_thread(self.log_msg, "Update done.")


def run(vault_path: Path | None = None) -> None:
    GroveApp(vault_path).run()
