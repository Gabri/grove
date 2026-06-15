"""Modal screens: confirm, vault create/unlock, workspace + credential mgmt."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    TextArea,
)

from .models import ProviderCred, Workspace
from .vault import Vault

DEFAULT_BRANCHES = {"main", "master"}

PROVIDERS = ["gitlab", "github", "bitbucket"]
_DEFAULT_ROOT_KEY = {"gitlab": "group", "github": "org", "bitbucket": "workspace"}
_DEFAULT_HOST = {
    "gitlab": "gitlab.com",
    "github": "github.com",
    "bitbucket": "bitbucket.org",
}


def parse_root_url(provider: str, line: str) -> tuple[dict, str | None]:
    """Parse one 'roots' line into (root_dict, derived_base_url | None).

    Accepts three forms:
      - a full web URL ('https://gitlab.client.com/team/platform') — the host
        becomes base_url (None for the provider's public host) and the path
        becomes the group/org/workspace;
      - 'key=value' — an explicit override (group=, org=, user=, workspace=);
      - a bare path — uses the provider's default root key.
    """
    from urllib.parse import urlsplit

    line = line.strip()
    if not line:
        return {}, None
    if line.startswith(("http://", "https://")):
        u = urlsplit(line)
        host, path, scheme = u.netloc, u.path.strip("/"), u.scheme
        default_host = host == _DEFAULT_HOST.get(provider)
        if provider == "github":
            base = None if default_host else f"{scheme}://{host}/api/v3"
            segs = [s for s in path.split("/") if s]
            if len(segs) >= 2 and segs[0] in ("orgs", "users"):
                key = "user" if segs[0] == "users" else "org"
                return {key: segs[1]}, base
            return ({"org": segs[0]} if segs else {}), base
        # gitlab / bitbucket: base_url is just scheme://host
        base = None if default_host else f"{scheme}://{host}"
        if provider == "bitbucket":
            segs = [s for s in path.split("/") if s]
            return ({"workspace": segs[0]} if segs else {}), base
        return ({"group": path} if path else {}), base
    if "=" in line:
        key, _, val = line.partition("=")
        return {key.strip(): val.strip()}, None
    return {_DEFAULT_ROOT_KEY[provider]: line}, None


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No confirmation. Dismisses with True/False."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, body: str, confirm_label: str = "Confirm"):
        super().__init__()
        self._title = title
        self._body = body
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._title, id="confirm-title")
            yield Static(self._body, id="confirm-body")
            with Horizontal(id="confirm-buttons"):
                yield Button(self._confirm_label, variant="primary", id="ok")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_cancel(self) -> None:
        self.dismiss(False)


MIN_PASSWORD_LEN = 8


class CreateVaultScreen(ModalScreen[str | None]):
    """Choose a master password (vault creation or change). Returns it or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str = "Create encrypted vault",
        button: str = "Create",
    ):
        super().__init__()
        self._screen_title = title
        self._button = button

    def compose(self) -> ComposeResult:
        with Vertical(id="form-box"):
            yield Label(self._screen_title, id="form-title")
            yield Static(
                "Choose a master password. It encrypts all your keys. "
                "There is no recovery if you forget it.",
                id="form-help",
            )
            yield Input(password=True, placeholder="master password", id="pw1")
            yield Input(password=True, placeholder="confirm password", id="pw2")
            yield Static("", id="form-error")
            with Horizontal(id="confirm-buttons"):
                yield Button(self._button, variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        pw1 = self.query_one("#pw1", Input).value
        pw2 = self.query_one("#pw2", Input).value
        err = self.query_one("#form-error", Static)
        if len(pw1) < MIN_PASSWORD_LEN:
            err.update(
                f"[red]password too short (min {MIN_PASSWORD_LEN} chars)[/]"
            )
            return
        if pw1 != pw2:
            err.update("[red]passwords do not match[/]")
            return
        self.dismiss(pw1)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChoiceScreen(ModalScreen[str | None]):
    """Multi-button choice. Dismisses with the pressed button's id (or None)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, body: str, choices: list[tuple[str, str]]):
        """choices: list of (id, label); first one gets the primary variant."""
        super().__init__()
        self._title = title
        self._body = body
        self._choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._title, id="confirm-title")
            yield Static(self._body, id="confirm-body")
            with Horizontal(id="confirm-buttons"):
                for i, (cid, label) in enumerate(self._choices):
                    yield Button(
                        label,
                        variant="primary" if i == 0 else "default",
                        id=cid,
                    )
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else event.button.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class UnlockScreen(ModalScreen[str | None]):
    """Prompt for the master password. Returns it (or None on cancel)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, error: str | None = None):
        super().__init__()
        self._error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="form-box"):
            yield Label("Unlock vault", id="form-title")
            if self._error:
                yield Static(f"[red]{self._error}[/]", id="form-error")
            yield Input(password=True, placeholder="master password", id="pw")
            with Horizontal(id="confirm-buttons"):
                yield Button("Unlock", variant="primary", id="ok")
                yield Button("Quit", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#pw", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self.dismiss(self.query_one("#pw", Input).value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextPromptScreen(ModalScreen[str | None]):
    """Single-line prompt prefilled with an initial value. Returns str or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, help_text: str, initial: str = ""):
        super().__init__()
        self._title = title
        self._help = help_text
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="form-box"):
            yield Label(self._title, id="form-title")
            yield Static(self._help, id="form-help")
            yield Input(value=self._initial, id="value")
            with Horizontal(id="confirm-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#value", Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class WorkspaceFormScreen(ModalScreen[Workspace | None]):
    """Create or edit a workspace (name + local folder). Returns Workspace or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        existing: "Workspace | None" = None,
        vault_default: str = "~/repos",
    ):
        super().__init__()
        self._existing = existing
        self._vault_default = vault_default

    def compose(self) -> ComposeResult:
        ex = self._existing
        title = "Edit workspace" if ex else "New workspace"
        btn = "Save" if ex else "Create"
        with Vertical(id="form-box"):
            yield Label(title, id="form-title")
            yield Input(
                value=ex.name if ex else "",
                placeholder="name (e.g. clientx)", id="name",
            )
            yield Input(
                value=ex.clone_base or "" if ex else "",
                placeholder=f"local folder — empty = vault default ({self._vault_default})",
                id="base",
            )
            yield Static("", id="form-error")
            with Horizontal(id="confirm-buttons"):
                yield Button(btn, variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        name = self.query_one("#name", Input).value.strip()
        if not name:
            self.query_one("#form-error", Static).update("[red]name required[/]")
            return
        base = self.query_one("#base", Input).value.strip() or None
        self.dismiss(Workspace(name=name, clone_base=base))

    def action_cancel(self) -> None:
        self.dismiss(None)


def _roots_to_text(roots: list[dict], base_url: str | None = None) -> str:
    """Convert stored roots back to editable text, one line per root.

    When base_url is set (self-hosted instance), reconstruct full URLs so they
    can be re-parsed by parse_root_url and the base_url is preserved.
    """
    lines = []
    for r in roots:
        if len(r) == 1:
            key, val = next(iter(r.items()))
            if base_url and key in _DEFAULT_ROOT_KEY.values():
                lines.append(f"{base_url}/{val}")
            elif key in _DEFAULT_ROOT_KEY.values():
                lines.append(val)
            else:
                lines.append(f"{key}={val}")
        else:
            lines.extend(f"{k}={v}" for k, v in r.items())
    return "\n".join(lines)


class CredentialFormScreen(ModalScreen[ProviderCred | None]):
    """Add or edit a provider key (label + token + roots) for a workspace."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, existing: ProviderCred | None = None):
        super().__init__()
        self._existing = existing

    def compose(self) -> ComposeResult:
        ex = self._existing
        title = "Edit provider key" if ex else "Add provider key"
        btn = "Save" if ex else "Add"
        with VerticalScroll(id="form-box-wide"):
            yield Label(title, id="form-title")
            yield Select(
                [(p, p) for p in PROVIDERS],
                prompt="provider",
                id="provider",
                value=ex.provider if ex else "gitlab",
            )
            yield Input(
                value=ex.label if ex else "",
                placeholder="label (e.g. work, personal)", id="label",
            )
            yield Input(
                value=ex.user if ex else "",
                placeholder="user (bitbucket only)", id="user",
            )
            yield Input(
                value=ex.token if ex else "",
                password=True, placeholder="token / app password", id="token",
            )
            yield Label("roots — what to scan (one per line):",
                        classes="field-label")
            yield Static(
                "[dim]Easiest: paste the full group/org URL, e.g.\n"
                "  https://gitlab.com/acme/team/platform\n"
                "and base_url + group are filled for you.\n"
                "Or a bare path 'team/platform' (gitlab group + subgroups), "
                "'acme' (github org), or 'org=acme' / 'user=me' / "
                "'workspace=ws'.[/]",
                classes="field-help",
            )
            yield TextArea(
                text=_roots_to_text(ex.roots, ex.base_url) if ex else "",
                id="roots",
            )
            yield Static("", id="form-error")
            with Horizontal(id="confirm-buttons"):
                yield Button(btn, variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def _parse_roots(self, provider: str, text: str) -> tuple[list[dict], str | None]:
        """Return (roots, derived_base_url) — base_url from the first URL pasted."""
        roots: list[dict] = []
        derived_base: str | None = None
        for line in text.splitlines():
            root, base = parse_root_url(provider, line)
            if root:
                roots.append(root)
            if base and derived_base is None:
                derived_base = base
        return roots, derived_base

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        err = self.query_one("#form-error", Static)
        provider = self.query_one("#provider", Select).value
        if provider == Select.BLANK:
            err.update("[red]pick a provider[/]")
            return
        label = self.query_one("#label", Input).value.strip()
        token = self.query_one("#token", Input).value.strip()
        if not label:
            err.update("[red]label required[/]")
            return
        if not token:
            err.update("[red]token required[/]")
            return
        roots, derived_base = self._parse_roots(
            provider, self.query_one("#roots", TextArea).text
        )
        if not roots:
            err.update("[red]add at least one root[/]")
            return
        self.dismiss(
            ProviderCred(
                provider=provider,
                label=label,
                token=token,
                user=self.query_one("#user", Input).value.strip() or None,
                base_url=derived_base,
                roots=roots,
            )
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


class BranchSelectScreen(ModalScreen[str | None]):
    """Pick a branch from a list. Returns branch name or None on cancel."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, branches: list[str], current: str | None = None):
        super().__init__()
        self._branches = branches
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="ws-box"):
            yield Label("Switch branch", id="form-title")
            yield Static("[dim]enter=switch  esc=cancel[/]", id="ws-hint")
            yield ListView(id="branch-list")

    def on_mount(self) -> None:
        lv = self.query_one("#branch-list", ListView)
        for b in self._branches:
            marker = "[green]●[/] " if b == self._current else "  "
            item = ListItem(Label(f"{marker}{b}"))
            item.branch_name = b  # type: ignore[attr-defined]
            lv.append(item)
        if self._current and self._current in self._branches:
            lv.index = self._branches.index(self._current)
        lv.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(getattr(event.item, "branch_name", None))


class WorkspaceManagerScreen(ModalScreen[str | None]):
    """List/switch/create/delete workspaces and manage their keys.

    Mutates the live vault and saves in place. Dismisses with the name of the
    workspace to make active (or None if unchanged).
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("n", "new_workspace", "New workspace"),
        ("r", "edit_workspace", "Edit ws"),
        ("c", "copy", "Copy"),
        ("a", "add_key", "Add key"),
        ("e", "edit_key", "Edit key"),
        ("b", "set_base", "Set local dir"),
        ("d", "delete", "Delete"),
        ("enter", "switch", "Switch to"),
    ]

    def __init__(self, vault: Vault):
        super().__init__()
        self.vault = vault
        self._result: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="ws-box"):
            yield Label("Workspaces", id="form-title")
            yield Static(
                "[dim]enter=switch  n=new  r=edit  c=copy  a=add key  e=edit key  "
                "b=local dir  d=delete  esc=close[/]",
                id="ws-hint",
            )
            yield ListView(id="ws-list")
            yield Static("", id="ws-detail")

    def on_mount(self) -> None:
        self._refresh_list()
        self.query_one("#ws-list", ListView).focus()

    def _refresh_list(self, highlight: str | None = None) -> None:
        lv = self.query_one("#ws-list", ListView)
        lv.clear()
        active = self.vault.data.active_workspace
        default_base = self.vault.data.default_clone_base
        for ws in self.vault.data.workspaces:
            marker = "[green]●[/] " if ws.name == active else "  "
            base = ws.clone_base or default_base
            label = (
                f"{marker}{ws.name}  [dim]{ws.provider_summary()}"
                f"   ↧ {base}[/]"
            )
            item = ListItem(Label(label))
            item.ws_name = ws.name  # type: ignore[attr-defined]
            lv.append(item)
        if self.vault.data.workspaces:
            names = [w.name for w in self.vault.data.workspaces]
            target = highlight if highlight in names else names[0]
            lv.index = names.index(target)
        else:
            self.query_one("#ws-detail", Static).update(
                "[yellow]No workspaces yet. Press 'n' to create one.[/]"
            )
        lv.focus()

    def _selected_name(self) -> str | None:
        lv = self.query_one("#ws-list", ListView)
        item = lv.highlighted_child
        name = getattr(item, "ws_name", None) if item else None
        if name is None and len(self.vault.data.workspaces) == 1:
            return self.vault.data.workspaces[0].name
        return name

    def _mark_config_changed(self, ws_name: str) -> None:
        """If ws_name is the active workspace, ensure app re-discovers on close."""
        if ws_name == self.vault.data.active_workspace:
            self._result = ws_name

    def action_close(self) -> None:
        self.dismiss(self._result)

    def action_new_workspace(self) -> None:
        def done(ws: Workspace | None) -> None:
            if ws is None:
                return
            if self.vault.data.get_workspace(ws.name):
                self.notify(f"workspace '{ws.name}' exists", severity="error")
                return
            self.vault.data.workspaces.append(ws)
            if self.vault.data.active_workspace is None:
                self.vault.data.active_workspace = ws.name
            self.vault.save()
            self._refresh_list(highlight=ws.name)
            self.notify(f"created workspace '{ws.name}' — press 'a' to add a key")

        self.app.push_screen(
            WorkspaceFormScreen(vault_default=self.vault.data.default_clone_base), done
        )

    def action_edit_workspace(self) -> None:
        name = self._selected_name()
        if name is None:
            self.notify("select a workspace first", severity="warning")
            return
        ws = self.vault.data.get_workspace(name)
        if ws is None:
            return

        def done(result: Workspace | None) -> None:
            if result is None or ws is None:
                return
            new_name = result.name
            if new_name != name and self.vault.data.get_workspace(new_name):
                self.notify(f"'{new_name}' exists", severity="error")
                return
            name_changed = new_name != name
            base_changed = result.clone_base != ws.clone_base
            ws.name = new_name
            ws.clone_base = result.clone_base
            if self.vault.data.active_workspace == name:
                self.vault.data.active_workspace = new_name
                self._result = new_name
            elif base_changed:
                self._mark_config_changed(new_name)
            self.vault.save()
            self._refresh_list(highlight=new_name)
            parts = []
            if name_changed:
                parts.append(f"renamed to '{new_name}'")
            if base_changed:
                shown = ws.clone_base or self.vault.data.default_clone_base
                parts.append(f"folder → {shown}")
            self.notify(", ".join(parts) if parts else f"'{name}' unchanged")

        self.app.push_screen(
            WorkspaceFormScreen(
                existing=ws, vault_default=self.vault.data.default_clone_base
            ),
            done,
        )

    def action_copy(self) -> None:
        import copy as _copy

        name = self._selected_name()
        if name is None:
            self.notify("select a workspace first", severity="warning")
            return
        ws = self.vault.data.get_workspace(name)
        if ws is None:
            return

        candidate = f"copy-of-{name}"
        # guarantee a unique default name (copy-of-X, copy-of-X-2, …)
        suffix = 2
        while self.vault.data.get_workspace(candidate):
            candidate = f"copy-of-{name}-{suffix}"
            suffix += 1

        ws_copy = _copy.deepcopy(ws)
        ws_copy.name = candidate
        ws_copy.known_repos = []

        def done(result: Workspace | None) -> None:
            if result is None:
                return
            if self.vault.data.get_workspace(result.name):
                self.notify(f"'{result.name}' exists", severity="error")
                return
            ws_copy.name = result.name
            ws_copy.clone_base = result.clone_base
            self.vault.data.workspaces.append(ws_copy)
            self.vault.save()
            self._refresh_list(highlight=ws_copy.name)
            self.notify(f"copied '{name}' → '{ws_copy.name}'")
            # auto-open key editing so user can update credentials
            if ws_copy.providers:
                self._pick_and_edit_key(ws_copy.name)

        self.app.push_screen(
            WorkspaceFormScreen(
                existing=ws_copy, vault_default=self.vault.data.default_clone_base
            ),
            done,
        )

    def action_add_key(self) -> None:
        name = self._selected_name()
        if name is None:
            self.notify("select a workspace first", severity="warning")
            return
        ws = self.vault.data.get_workspace(name)

        def done(cred: ProviderCred | None) -> None:
            if cred is None or ws is None:
                return
            existing = next(
                (p for p in ws.providers if p.provider == cred.provider), None
            )
            verb = "added"
            if existing is not None:
                ws.providers.remove(existing)
                verb = "replaced"
            ws.providers.append(cred)
            self.vault.save()
            self._mark_config_changed(name)
            self._refresh_list(highlight=name)
            self.notify(f"{verb} {cred.provider}:{cred.label} on '{name}'")

        self.app.push_screen(CredentialFormScreen(), done)

    def _open_edit_key(self, ws_name: str, cred: ProviderCred) -> None:
        ws = self.vault.data.get_workspace(ws_name)

        def done(updated: ProviderCred | None) -> None:
            if updated is None or ws is None:
                return
            ws.providers = [p for p in ws.providers if p is not cred]
            ws.providers.append(updated)
            self.vault.save()
            self._mark_config_changed(ws_name)
            self._refresh_list(highlight=ws_name)
            self.notify(f"updated {updated.provider}:{updated.label}")

        self.app.push_screen(CredentialFormScreen(existing=cred), done)

    def _pick_and_edit_key(self, ws_name: str) -> None:
        ws = self.vault.data.get_workspace(ws_name)
        if ws is None or not ws.providers:
            return
        if len(ws.providers) == 1:
            self._open_edit_key(ws_name, ws.providers[0])
        else:
            choices = [(p.provider, f"{p.provider}:{p.label}") for p in ws.providers]

            def pick(choice: str | None) -> None:
                if choice is None:
                    return
                c = next((p for p in ws.providers if p.provider == choice), None)
                if c:
                    self._open_edit_key(ws_name, c)

            self.app.push_screen(
                ChoiceScreen("Edit key", f"Which key to edit in '{ws_name}'?", choices),
                pick,
            )

    def action_edit_key(self) -> None:
        name = self._selected_name()
        if name is None:
            self.notify("select a workspace first", severity="warning")
            return
        ws = self.vault.data.get_workspace(name)
        if ws is None or not ws.providers:
            self.notify("no keys yet — press 'a' to add one", severity="warning")
            return
        self._pick_and_edit_key(name)

    def action_set_base(self) -> None:
        name = self._selected_name()
        if name is None:
            self.notify("select a workspace first", severity="warning")
            return
        ws = self.vault.data.get_workspace(name)
        current = (ws.clone_base if ws else "") or self.vault.data.default_clone_base

        def done(value: str | None) -> None:
            if value is None or ws is None:
                return
            ws.clone_base = value.strip() or None
            self.vault.save()
            self._mark_config_changed(name)
            self._refresh_list(highlight=name)
            shown = ws.clone_base or self.vault.data.default_clone_base
            self.notify(f"'{name}' syncs into {shown}")

        self.app.push_screen(
            TextPromptScreen(
                "Local sync folder",
                "Absolute or ~-path where this workspace's repos live / are cloned. "
                "Existing clones are matched by their remote anywhere below it.",
                current,
            ),
            done,
        )

    def action_delete(self) -> None:
        name = self._selected_name()
        if name is None:
            return

        def done(ok: bool) -> None:
            if not ok:
                return
            self.vault.data.workspaces = [
                w for w in self.vault.data.workspaces if w.name != name
            ]
            if self.vault.data.active_workspace == name:
                self.vault.data.active_workspace = (
                    self.vault.data.workspaces[0].name
                    if self.vault.data.workspaces
                    else None
                )
                self._result = self.vault.data.active_workspace
            self.vault.save()
            self._refresh_list()

        self.app.push_screen(
            ConfirmScreen("Delete workspace", f"Delete '{name}'?", "Delete"), done
        )

    def action_switch(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        ws = self.vault.data.get_workspace(name)
        if ws and not ws.providers:
            self.notify("workspace has no keys — press 'a' first", severity="warning")
            return
        self.vault.data.active_workspace = name
        self.vault.save()
        self.dismiss(name)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_switch()
